"""Read-only security scanner for agent workspaces."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from . import work_cmd

SEVERITY_ORDER = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}

SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}

SKIP_PREFIXES = (
    (".brigade", "runs"),
    (".brigade", "work"),
    (".claude", "memory-handoffs"),
    (".codex", "memory-handoffs"),
)

TEXT_SUFFIXES = {
    "",
    ".bash",
    ".cfg",
    ".conf",
    ".env",
    ".ini",
    ".json",
    ".jsonl",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

SECRET_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*['\"]?([A-Za-z0-9_./+=:-]{16,})"
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
ENV_ASSIGNMENT_RE = re.compile(r"(?i)\b[A-Z0-9_]*(TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*[A-Za-z0-9_./+=:-]{16,}")
REMOTE_SHELL_RE = re.compile(r"\b(curl|wget)\b[^\n|;]*(\||;)\s*(sh|bash)\b")
DESTRUCTIVE_RE = re.compile(r"\b(rm\s+-rf|git\s+reset\s+--hard|git\s+clean\s+-fdx|chmod\s+777)\b")
UNPINNED_NPX_RE = re.compile(r"\bnpx\s+(?:-y\s+)?([a-zA-Z0-9_.-]+)(?:\s|$)")
HTTP_MCP_RE = re.compile(r'"url"\s*:\s*"https?://')
AUTO_APPROVE_RE = re.compile(r"(?i)(auto[_-]?approve|always[_-]?allow|allow[_-]?all)")
PROMPT_INJECTION_RE = re.compile(
    r"(?i)(ignore (all )?(previous|prior) instructions|do not (tell|reveal)|hidden instruction|"
    r"send (all )?(secrets|tokens)|exfiltrat|disable safety|bypass safety)"
)


def _short(text: str, limit: int = 160) -> str:
    rendered = " ".join(text.split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 3].rstrip() + "..."


def _is_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(
        marker in lowered
        for marker in (
            "example",
            "placeholder",
            "changeme",
            "your_",
            "your-",
            "<",
            "{{",
            "xxxxx",
            "dummy",
        )
    )


def _iter_scan_files(target: Path) -> list[Path]:
    paths: list[Path] = []
    for path in target.rglob("*"):
        if path.is_dir():
            continue
        rel_parts = path.relative_to(target).parts
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if any(rel_parts[: len(prefix)] == prefix for prefix in SKIP_PREFIXES):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 500_000:
                continue
        except OSError:
            continue
        paths.append(path)
    paths.sort()
    return paths


def _surface_for(path: Path, target: Path) -> str:
    rel = path.relative_to(target)
    parts = rel.parts
    if parts and parts[0] == ".brigade":
        return "brigade"
    if parts and parts[0] == ".codex":
        return "codex"
    if parts and parts[0] == ".claude":
        return "claude"
    if "mcp" in path.name.lower():
        return "mcp"
    if parts and parts[0] in {"hooks", "scripts"}:
        return "automation"
    if path.name in {"AGENTS.md", "CLAUDE.md", "SAFETY_RULES.md", "INSTALL_FOR_AGENTS.md"}:
        return "agent-instructions"
    return "repo"


def _confidence_for(path: Path, target: Path) -> str:
    rel = path.relative_to(target)
    parts = rel.parts
    if parts and parts[0] == "src" and "templates" in parts:
        return "template"
    if parts and parts[0] in {".brigade", ".claude", ".codex", "hooks", "scripts"}:
        return "runtime"
    if path.name in {"AGENTS.md", "CLAUDE.md", "SAFETY_RULES.md", "INSTALL_FOR_AGENTS.md"}:
        return "runtime"
    return "repo"


def _finding(
    findings: list[dict[str, Any]],
    *,
    target: Path,
    path: Path,
    line: int,
    severity: str,
    category: str,
    title: str,
    evidence: str,
    suggestion: str,
) -> None:
    rel = path.relative_to(target)
    finding_id = f"security-{len(findings) + 1:04d}"
    findings.append(
        {
            "id": finding_id,
            "severity": severity,
            "category": category,
            "title": title,
            "path": str(rel),
            "line": line,
            "surface": _surface_for(path, target),
            "confidence": _confidence_for(path, target),
            "evidence": _short(evidence),
            "suggestion": suggestion,
        }
    )


def _scan_line(findings: list[dict[str, Any]], *, target: Path, path: Path, line_number: int, line: str) -> None:
    secret_match = SECRET_VALUE_RE.search(line)
    if secret_match and not _is_placeholder(secret_match.group(2)):
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="high",
            category="secrets",
            title="Possible hardcoded credential",
            evidence=line,
            suggestion="Move the value into local environment or secret storage and commit only a placeholder.",
        )
    if PRIVATE_KEY_RE.search(line) or (ENV_ASSIGNMENT_RE.search(line) and not _is_placeholder(line)):
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="high",
            category="secrets",
            title="Possible sensitive secret material",
            evidence=line,
            suggestion="Remove secret material from the repo and rotate the credential if it was real.",
        )
    if "danger-full-access" in line or "sandbox_permissions" in line and "require_escalated" in line:
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="medium",
            category="permissions",
            title="Broad agent execution permission",
            evidence=line,
            suggestion="Prefer read-only or workspace-scoped execution unless this is an explicitly trusted local path.",
        )
    if REMOTE_SHELL_RE.search(line):
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="high",
            category="automation",
            title="Remote script piped into shell",
            evidence=line,
            suggestion="Pin and verify downloaded scripts before execution, or replace with a checked-in script.",
        )
    if DESTRUCTIVE_RE.search(line):
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="medium",
            category="automation",
            title="Destructive command pattern",
            evidence=line,
            suggestion="Gate destructive commands behind explicit operator approval and document recovery steps.",
        )
    npx_match = UNPINNED_NPX_RE.search(line)
    if npx_match and "@" not in npx_match.group(1):
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="medium",
            category="supply-chain",
            title="Unpinned remote package execution",
            evidence=line,
            suggestion="Pin remote package versions or install through a reviewed lockfile.",
        )
    if "mcp" in path.name.lower() or '"mcpServers"' in line:
        if HTTP_MCP_RE.search(line):
            _finding(
                findings,
                target=target,
                path=path,
                line=line_number,
                severity="medium",
                category="mcp",
                title="Remote MCP transport",
                evidence=line,
                suggestion="Prefer local MCP servers, pin remote hosts, and document authentication boundaries.",
            )
        if AUTO_APPROVE_RE.search(line):
            _finding(
                findings,
                target=target,
                path=path,
                line=line_number,
                severity="medium",
                category="mcp",
                title="MCP auto-approval pattern",
                evidence=line,
                suggestion="Avoid blanket auto-approval and require review for mutable or networked tools.",
            )
    if _surface_for(path, target) in {"agent-instructions", "claude", "codex", "repo"} and PROMPT_INJECTION_RE.search(line):
        _finding(
            findings,
            target=target,
            path=path,
            line=line_number,
            severity="low",
            category="prompt-injection",
            title="Prompt-injection style instruction",
            evidence=line,
            suggestion="Keep hostile examples clearly labeled as examples and avoid executable language in trusted instructions.",
        )


def scan_target(target: Path) -> dict[str, Any]:
    target = target.expanduser().resolve()
    findings: list[dict[str, Any]] = []
    scanned_files: list[str] = []
    for path in _iter_scan_files(target):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        scanned_files.append(str(path.relative_to(target)))
        for line_number, line in enumerate(text.splitlines(), start=1):
            _scan_line(findings, target=target, path=path, line_number=line_number, line=line)
    counts: dict[str, int] = {}
    for finding in findings:
        severity = str(finding["severity"])
        counts[severity] = counts.get(severity, 0) + 1
    return {
        "target": str(target),
        "scanned_files": scanned_files,
        "scanned_file_count": len(scanned_files),
        "finding_count": len(findings),
        "severity_counts": dict(sorted(counts.items())),
        "findings": findings,
    }


def _should_fail(findings: list[dict[str, Any]], fail_on: str) -> bool:
    if fail_on == "none":
        return False
    threshold = SEVERITY_ORDER[fail_on]
    return any(SEVERITY_ORDER.get(str(item.get("severity")), 0) >= threshold for item in findings)


def _import_findings(target: Path, findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = []
    for finding in findings:
        path = finding.get("path")
        line = finding.get("line")
        title = finding.get("title")
        severity = finding.get("severity")
        category = finding.get("category")
        records.append(
            {
                "text": f"Review security finding [{severity}] {category} in {path}:{line}: {title}",
                "kind": "incident",
                "source": "security-scan",
                "metadata": {
                    "finding_id": finding.get("id"),
                    "severity": severity,
                    "category": category,
                    "path": path,
                    "line": line,
                    "surface": finding.get("surface"),
                    "confidence": finding.get("confidence"),
                    "suggestion": finding.get("suggestion"),
                },
            }
        )
    return work_cmd._append_import_records(target, records)


def scan(
    *,
    target: Path,
    json_output: bool = False,
    fail_on: str = "critical",
    import_findings: bool = False,
) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    if fail_on not in SEVERITY_ORDER and fail_on != "none":
        print("error: --fail-on must be one of: none, low, medium, high, critical", file=sys.stderr)
        return 2

    report = scan_target(target)
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if import_findings and report["findings"]:
        imported, skipped = _import_findings(target, report["findings"])
        report["imported_findings"] = len(imported)
        report["skipped_duplicate_imports"] = len(skipped)

    if json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"security scan: {target}")
        print(f"scanned_files: {report['scanned_file_count']}")
        print(f"findings: {report['finding_count']}")
        for severity, count in report["severity_counts"].items():
            print(f"{severity}: {count}")
        if import_findings:
            print(f"imported_findings: {len(imported)}")
            print(f"skipped_duplicate_imports: {len(skipped)}")
        for finding in report["findings"]:
            print(
                f"- [{finding['severity']}] {finding['category']} "
                f"{finding['path']}:{finding['line']} {finding['title']}"
            )
            print(f"  evidence: {finding['evidence']}")
            print(f"  suggestion: {finding['suggestion']}")

    return 1 if _should_fail(report["findings"], fail_on) else 0
