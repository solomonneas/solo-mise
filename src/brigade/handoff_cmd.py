"""Handoff health checks shared by CLI doctors."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OK = "ok"
WARN = "warn"
FAIL = "fail"

WRITER_INBOXES = (".claude/memory-handoffs", ".codex/memory-handoffs")
IGNORED_HANDOFF_NAMES = {"TEMPLATE.md"}


@dataclass(frozen=True)
class WatchedInbox:
    root: Path
    inbox: str


@dataclass(frozen=True)
class InboxHealth:
    inbox: str
    path: Path
    exists: bool
    pending: int
    processed: int
    watched: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "inbox": self.inbox,
            "path": str(self.path),
            "exists": self.exists,
            "pending": self.pending,
            "processed": self.processed,
            "watched": self.watched,
        }


@dataclass(frozen=True)
class HandoffHealth:
    target: Path
    sources_path: Path | None
    sources_loaded: bool
    inboxes: tuple[InboxHealth, ...]
    warnings: tuple[str, ...]
    failures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": str(self.target),
            "sources_path": str(self.sources_path) if self.sources_path else None,
            "sources_loaded": self.sources_loaded,
            "inboxes": [inbox.as_dict() for inbox in self.inboxes],
            "warnings": list(self.warnings),
            "failures": list(self.failures),
        }


def default_sources_path(target: Path) -> Path:
    return target / ".brigade" / "handoff-sources.json"


def inspect(target: Path, sources: Path | None = None) -> HandoffHealth:
    target = target.expanduser().resolve()
    sources_path = sources.expanduser().resolve() if sources is not None else default_sources_path(target)
    watched: tuple[WatchedInbox, ...] = ()
    failures: list[str] = []
    sources_loaded = False

    if sources_path.is_file():
        try:
            watched = _load_sources(target, sources_path)
        except ValueError as exc:
            failures.append(f"invalid handoff source config {sources_path}: {exc}")
        else:
            sources_loaded = True
    elif sources is not None:
        failures.append(f"handoff source config not found: {sources_path}")
        sources_path = sources_path
    else:
        sources_path = None

    inboxes = tuple(_inspect_inbox(target, rel, watched) for rel in WRITER_INBOXES)
    warnings: list[str] = []
    pending_total = sum(inbox.pending for inbox in inboxes)
    if pending_total and not sources_loaded and not failures:
        warnings.append(
            "pending handoffs exist but no .brigade/handoff-sources.json is configured"
        )
    for inbox in inboxes:
        if inbox.pending and not inbox.watched:
            warnings.append(
                f"{inbox.inbox} has {inbox.pending} pending handoff"
                f"{'s' if inbox.pending != 1 else ''} but is not watched by the source config"
            )

    return HandoffHealth(
        target=target,
        sources_path=sources_path,
        sources_loaded=sources_loaded,
        inboxes=inboxes,
        warnings=tuple(warnings),
        failures=tuple(failures),
    )


def doctor_checks(target: Path, sources: Path | None = None) -> list[tuple[str, str, str]]:
    health = inspect(target, sources=sources)
    checks: list[tuple[str, str, str]] = []
    if health.failures:
        for failure in health.failures:
            checks.append((FAIL, "handoff_sources", failure))
    elif health.sources_loaded:
        checks.append((OK, "handoff_sources", str(health.sources_path)))
    else:
        pending_total = sum(inbox.pending for inbox in health.inboxes)
        level = WARN if pending_total else OK
        checks.append((level, "handoff_sources", "not configured; no pending handoffs" if not pending_total else "not configured"))

    for inbox in health.inboxes:
        if inbox.pending and not inbox.watched:
            level = WARN
        elif not inbox.exists:
            level = OK
        else:
            level = OK
        watched = "yes" if inbox.watched else "no"
        exists = "yes" if inbox.exists else "no"
        detail = (
            f"{inbox.path} "
            f"(exists={exists}, pending={inbox.pending}, processed={inbox.processed}, watched={watched})"
        )
        checks.append((level, f"handoff_watch: {inbox.inbox}", detail))

    for warning in health.warnings:
        checks.append((WARN, "handoff_warning", warning))
    return checks


def doctor(*, target: Path, sources: Path | None = None, json_output: bool = False) -> int:
    if not target.expanduser().exists():
        print(f"error: target does not exist: {target}", file=sys.stderr)
        return 2
    health = inspect(target, sources=sources)
    if json_output:
        print(json.dumps(health.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"handoff doctor: {health.target}")
        print(f"sources: {health.sources_path if health.sources_path else '(not configured)'}")
        for status, name, detail in doctor_checks(health.target, sources=health.sources_path):
            print(f"[{status}] {name}: {detail}")
    return 1 if health.failures else 0


def _load_sources(target: Path, sources_path: Path) -> tuple[WatchedInbox, ...]:
    try:
        payload = json.loads(sources_path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("root must be a JSON object")
    sources = payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")

    watched: list[WatchedInbox] = []
    for index, entry in enumerate(sources):
        if isinstance(entry, str):
            root_value = entry
            inbox_values = list(WRITER_INBOXES)
        elif isinstance(entry, dict):
            root_value = entry.get("root", ".")
            inbox_values = entry.get("inboxes", list(WRITER_INBOXES))
        else:
            raise ValueError(f"sources[{index}] must be an object or string")
        if not isinstance(root_value, str) or not root_value.strip():
            raise ValueError(f"sources[{index}].root must be a non-empty string")
        if not isinstance(inbox_values, list) or not all(isinstance(item, str) for item in inbox_values):
            raise ValueError(f"sources[{index}].inboxes must be a list of strings")
        root = _resolve_source_root(target, root_value)
        for inbox in inbox_values:
            normalized = _normalize_inbox(inbox)
            if normalized:
                watched.append(WatchedInbox(root=root, inbox=normalized))
    return tuple(watched)


def _resolve_source_root(target: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = target / path
    return path.resolve()


def _normalize_inbox(value: str) -> str:
    normalized = value.strip().replace("\\", "/").strip("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _inspect_inbox(target: Path, rel: str, watched: tuple[WatchedInbox, ...]) -> InboxHealth:
    path = target / rel
    return InboxHealth(
        inbox=rel,
        path=path,
        exists=path.is_dir(),
        pending=_count_pending(path),
        processed=_count_processed(path),
        watched=_is_watched(target, rel, watched),
    )


def _count_pending(path: Path) -> int:
    if not path.is_dir():
        return 0
    count = 0
    for candidate in path.glob("*.md"):
        if not candidate.is_file():
            continue
        if candidate.name.startswith(".") or candidate.name in IGNORED_HANDOFF_NAMES:
            continue
        count += 1
    return count


def _count_processed(path: Path) -> int:
    processed = path / "processed"
    if not processed.is_dir():
        return 0
    return len([candidate for candidate in processed.glob("*.md") if candidate.is_file()])


def _is_watched(target: Path, rel: str, watched: tuple[WatchedInbox, ...]) -> bool:
    resolved_target = target.resolve()
    normalized = _normalize_inbox(rel)
    return any(item.root == resolved_target and item.inbox == normalized for item in watched)
