"""Bounded local learning candidate aggregation."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import work_cmd

OK = "ok"
WARN = "warn"
LEARNING_CLOSEOUT_STATUSES = {"accepted-risk", "dismissed", "archived", "deferred"}
LEARNING_IMPORT_SOURCES = {
    "backup-health",
    "code-review",
    "handoff-ingest",
    "memory-care",
    "repo-fleet-release",
    "scanner-health",
    "security-scan",
    "tool-catalog",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _learning_root(target: Path) -> Path:
    return target / ".brigade" / "learn"


def _replays_root(target: Path) -> Path:
    return _learning_root(target) / "replays"


def _closeouts_root(target: Path) -> Path:
    return _learning_root(target) / "closeouts"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _candidate(candidate_id: str, subsystem: str, status: str, summary: str, command: str, *, severity: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = {
        "id": candidate_id,
        "subsystem": subsystem,
        "status": status,
        "severity": severity,
        "safe_summary": summary,
        "suggested_next_command": command,
        "metadata": metadata or {},
    }
    candidate["source_fingerprint"] = _candidate_fingerprint(candidate)
    return candidate


def _candidate_fingerprint(candidate: dict[str, Any]) -> str:
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    explicit = metadata.get("source_fingerprint")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return work_cmd._stable_hash({"id": candidate.get("id"), "subsystem": candidate.get("subsystem"), "summary": candidate.get("safe_summary"), "status": candidate.get("status")})


def _read_closeouts(target: Path) -> list[dict[str, Any]]:
    root = _closeouts_root(target.expanduser().resolve())
    receipts: list[dict[str, Any]] = []
    if not root.is_dir():
        return receipts
    for path in sorted(root.glob("*/closeout.json")):
        payload = _read_json(path)
        if payload is None:
            continue
        payload.setdefault("closeout_id", path.parent.name)
        payload["path"] = str(path.parent)
        receipts.append(payload)
    return sorted(receipts, key=lambda item: str(item.get("created_at") or item.get("closeout_id") or ""), reverse=True)


def _closeout_key(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('subsystem')}:{candidate.get('id')}"


def _latest_closeout_by_candidate(target: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for closeout in _read_closeouts(target):
        key = str(closeout.get("candidate_key") or f"{closeout.get('subsystem')}:{closeout.get('candidate_id')}")
        if key and key not in latest:
            latest[key] = closeout
    return latest


def _import_learning_summary(item: dict[str, Any]) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for key in ("safe_summary", "safe_detail", "evidence_summary"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    source = str(item.get("source") or "producer")
    kind = str(item.get("kind") or "import")
    return f"{source} {kind} import requires review"


def _raw_candidates(target: Path) -> list[dict[str, Any]]:
    target = target.expanduser().resolve()
    results: list[dict[str, Any]] = []
    for item in work_cmd._read_imports(target):
        if item.get("status", "pending") != "pending":
            continue
        source = str(item.get("source") or "manual")
        if source in LEARNING_IMPORT_SOURCES:
            import_id = str(item.get("id") or "")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            results.append(
                _candidate(
                    import_id,
                    source,
                    "pending",
                    _import_learning_summary(item),
                    f"brigade work import plan {import_id}",
                    severity=item.get("priority") if isinstance(item.get("priority"), str) else None,
                    metadata={"import_id": import_id, "source": source, "source_fingerprint": metadata.get("source_fingerprint")},
                )
            )
    for receipt in work_cmd._review_receipts(target):
        if receipt.get("status") == "failed":
            run_id = str(receipt.get("run_id") or "")
            results.append(_candidate(run_id, "code-review", "failed", "failed review run", f"brigade work review show {run_id}"))
    tool_runs = target / ".brigade" / "tools" / "runs"
    if tool_runs.is_dir():
        for path in sorted(tool_runs.glob("*/receipt.json")):
            payload = _read_json(path)
            if isinstance(payload, dict) and payload.get("status") == "failed":
                run_id = str(payload.get("run_id") or path.parent.name)
                results.append(_candidate(run_id, "tool-run", "failed", "failed portable tool run", f"brigade tools run show {run_id}"))
    return results


def candidates(target: Path, *, include_quieted: bool = False) -> list[dict[str, Any]]:
    target = target.expanduser().resolve()
    closeout_by_candidate = _latest_closeout_by_candidate(target)
    results: list[dict[str, Any]] = []
    for item in _raw_candidates(target):
        closeout = closeout_by_candidate.get(_closeout_key(item))
        if closeout and closeout.get("source_fingerprint") == item.get("source_fingerprint") and closeout.get("status") in LEARNING_CLOSEOUT_STATUSES:
            if include_quieted:
                item = {**item, "quieted_by": closeout.get("closeout_id"), "closeout_status": closeout.get("status")}
                results.append(item)
            continue
        if closeout and closeout.get("source_fingerprint") != item.get("source_fingerprint"):
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            item["metadata"] = {**metadata, "changed_closeout_id": closeout.get("closeout_id"), "previous_fingerprint": closeout.get("source_fingerprint")}
            item["closeout_status"] = "changed-fingerprint"
        results.append(item)
    return results


def plan_payload(target: Path) -> dict[str, Any]:
    target = target.expanduser().resolve()
    items = candidates(target)
    raw = _raw_candidates(target)
    quieted = candidates(target, include_quieted=True)
    quieted_count = len([item for item in quieted if item.get("quieted_by")])
    changed_count = len([item for item in items if item.get("closeout_status") == "changed-fingerprint"])
    checks = [
        {
            "status": WARN if items else OK,
            "name": "learning_candidates",
            "detail": f"{len(items)} candidate(s)" if items else "none",
        }
    ]
    return {
        "target": str(target),
        "candidate_count": len(items),
        "raw_candidate_count": len(raw),
        "quieted_candidate_count": quieted_count,
        "changed_fingerprint_count": changed_count,
        "candidates": items,
        "checks": checks,
        "issues": [check for check in checks if check["status"] != OK],
        "issue_count": 1 if items else 0,
        "top_issue": checks[0] if items else None,
        "replay_policy": "safe local summaries only, no private raw evidence",
    }


def plan(*, target: Path, json_output: bool = False) -> int:
    payload = plan_payload(target)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"learn plan: {payload['target']}")
    print(f"candidates: {payload['candidate_count']}")
    for item in payload["candidates"][:20]:
        print(f"- {item['id']} [{item['subsystem']}] {item['safe_summary']}")
    return 0


def doctor(*, target: Path, json_output: bool = False) -> int:
    payload = plan_payload(target)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["issue_count"] == 0 else 1
    print(f"learn doctor: {payload['target']}")
    for check in payload["checks"]:
        print(f"[{check['status']}] {check['name']}: {check['detail']}")
    return 0 if payload["issue_count"] == 0 else 1


def import_issues(*, target: Path, dry_run: bool = False, json_output: bool = False) -> int:
    payload = plan_payload(target)
    records: list[dict[str, Any]] = []
    for item in payload["candidates"]:
        fingerprint = str(item.get("source_fingerprint") or work_cmd._stable_hash({"id": item["id"], "subsystem": item["subsystem"], "summary": item["safe_summary"]}))
        records.append(
            {
                "text": f"Review learning candidate: {item['safe_summary']}",
                "kind": "task",
                "source": "learning-loop",
                "type": "research",
                "priority": "normal",
                "template": "docs",
                "acceptance": [
                    "The candidate is routed to a task, handoff, suppression, accepted risk, archive, or dismissal.",
                    "No canonical memory, source, policy, or tool config is edited automatically.",
                ],
                "metadata": {
                    "candidate_id": item["id"],
                    "subsystem": item["subsystem"],
                    "source_item_key": f"{item['subsystem']}:{item['id']}",
                    "source_fingerprint": fingerprint,
                    "safe_summary": item["safe_summary"],
                },
            }
        )
    imported, skipped, dismissed = work_cmd._append_import_records(target.expanduser().resolve(), records, dry_run=dry_run)
    output = {"target": payload["target"], "created": len(imported), "skipped": len(skipped), "dismissed": len(dismissed), "dry_run": dry_run}
    if json_output:
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    print(f"learning_imports: {payload['target']}")
    print(f"created: {len(imported)}")
    print(f"skipped: {len(skipped)}")
    print(f"dismissed: {len(dismissed)}")
    return 0


def closeout(*, target: Path, candidate_id: str, status: str, reason: str, subsystem: str | None = None, json_output: bool = False) -> int:
    if status not in LEARNING_CLOSEOUT_STATUSES:
        print(f"error: status must be one of: {', '.join(sorted(LEARNING_CLOSEOUT_STATUSES))}", file=sys.stderr)
        return 1
    target = target.expanduser().resolve()
    matches = [
        item
        for item in _raw_candidates(target)
        if str(item.get("id") or "") == candidate_id and (subsystem is None or item.get("subsystem") == subsystem)
    ]
    if not matches:
        print(f"error: learning candidate not found: {candidate_id}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: learning candidate id is ambiguous: {candidate_id}", file=sys.stderr)
        return 1
    candidate = matches[0]
    closeout_id = f"{_now().strftime('%Y%m%d-%H%M%S-%f')}-learning-closeout"
    payload = {
        "target": str(target),
        "closeout_id": closeout_id,
        "candidate_id": candidate.get("id"),
        "candidate_key": _closeout_key(candidate),
        "subsystem": candidate.get("subsystem"),
        "status": status,
        "reason": reason,
        "safe_summary": candidate.get("safe_summary"),
        "source_fingerprint": candidate.get("source_fingerprint"),
        "created_at": _now().isoformat(),
        "manual_only": True,
        "remote_mutation": False,
        "receipt_fingerprint": work_cmd._stable_hash({"candidate_key": _closeout_key(candidate), "status": status, "reason": reason, "source_fingerprint": candidate.get("source_fingerprint")}),
    }
    root = _closeouts_root(target) / closeout_id
    _write_json(root / "closeout.json", payload)
    payload["path"] = str(root)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"learning_closeout: {closeout_id}")
    print(f"candidate: {candidate.get('id')}")
    print(f"subsystem: {candidate.get('subsystem')}")
    print(f"status: {status}")
    print(f"path: {root}")
    return 0


def closeouts(*, target: Path, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    receipts = _read_closeouts(target)
    payload = {"target": str(target), "closeouts": receipts, "closeout_count": len(receipts)}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"learning_closeouts: {target}")
    print(f"closeouts: {len(receipts)}")
    for receipt in receipts:
        print(f"- {receipt.get('closeout_id')} status={receipt.get('status')} candidate={receipt.get('candidate_key')}")
    return 0


def closeout_show(*, target: Path, closeout_id: str, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    receipts = _read_closeouts(target)
    if closeout_id == "latest":
        matches = receipts[:1]
    else:
        matches = [receipt for receipt in receipts if str(receipt.get("closeout_id") or "").startswith(closeout_id)]
    if not matches:
        print(f"error: learning closeout not found: {closeout_id}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(f"error: learning closeout id is ambiguous: {closeout_id}", file=sys.stderr)
        return 1
    payload = {"target": str(target), "closeout": matches[0]}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"learning_closeout: {matches[0].get('closeout_id')}")
    print(f"status: {matches[0].get('status')}")
    print(f"candidate: {matches[0].get('candidate_key')}")
    return 0


def write_replay(target: Path, *, scenario_id: str, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    target = target.expanduser().resolve()
    replay_id = f"{_now().strftime('%Y%m%d-%H%M%S')}-learning-replay-{scenario_id}"
    payload = {
        "replay_id": replay_id,
        "scenario_id": scenario_id,
        "created_at": _now().isoformat(),
        "before": before,
        "after": after,
        "privacy": "safe summaries only",
    }
    _write_json(_replays_root(target) / replay_id / "replay.json", payload)
    return payload


def health(target: Path) -> dict[str, Any]:
    payload = plan_payload(target)
    return {
        "target": payload["target"],
        "candidate_count": payload["candidate_count"],
        "raw_candidate_count": payload["raw_candidate_count"],
        "quieted_candidate_count": payload["quieted_candidate_count"],
        "changed_fingerprint_count": payload["changed_fingerprint_count"],
        "issue_count": payload["issue_count"],
        "top_issue": payload["top_issue"],
        "candidates": payload["candidates"],
        "latest_closeout": _read_closeouts(target)[0] if _read_closeouts(target) else None,
    }
