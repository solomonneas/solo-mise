"""Local portable tool and skill catalog inspection."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 fallback.
    tomllib = None  # type: ignore[assignment]

from . import dogfood_cmd
from .install import apply_gitignore
from .selection import Selection

OK = "ok"
WARN = "warn"
FAIL = "fail"
CONFIG_REL_PATH = ".brigade/tools.toml"
CALLS_REL_PATH = ".brigade/tools/calls.jsonl"
RUNS_REL_PATH = ".brigade/tools/runs"
HEALTH_STALE_HOURS = 48
CALL_STALE_HOURS = 72
CALL_RUNNING_STALE_HOURS = 2
PROJECTION_MARKER = "brigade-tool-projection:"
FAMILIES = ("skill", "slash-command", "superpower", "mcp", "openapi", "graphql", "script", "custom")
KNOWN_HARNESSES = ("claude", "codex", "opencode", "hermes", "openclaw", "mcp", "scripts")
APPROVAL_MODES = ("never", "on-request", "always")
SCHEMA_TYPES = ("object", "array", "string", "number", "integer", "boolean", "null")
UNSAFE_FIELD_PATTERN = re.compile(r"(password|secret|token|credential|api[_-]?key)", re.IGNORECASE)
HIGH_RISK_COMMAND_PATTERNS = (
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bcurl\b.+\|\s*(?:sh|bash)\b"),
    re.compile(r"\b(?:sh|bash)\s+-c\b"),
    re.compile(r"\bsudo\b"),
)
DEFAULT_TOOLS = (
    {
        "id": "simplify",
        "name": "Simplify",
        "family": "slash-command",
        "enabled": True,
        "description": "Portable simplify command placeholder.",
        "source_path": "tools/simplify.md",
        "supported_harnesses": ["claude", "codex"],
        "projections": {
            "claude": ".claude/commands/simplify.md",
            "codex": ".codex/skills/simplify/SKILL.md",
        },
    },
    {
        "id": "superpowers",
        "name": "Superpowers",
        "family": "superpower",
        "enabled": True,
        "description": "Portable superpowers placeholder.",
        "source_path": "tools/superpowers.md",
        "supported_harnesses": ["claude", "codex", "opencode"],
        "projections": {
            "claude": ".claude/commands/superpowers.md",
            "codex": ".codex/skills/superpowers/SKILL.md",
            "opencode": ".opencode/superpowers/superpowers.md",
        },
    },
)


def config_path(target: Path) -> Path:
    return target / CONFIG_REL_PATH


def calls_path(target: Path) -> Path:
    return target / CALLS_REL_PATH


def runs_path(target: Path) -> Path:
    return target / RUNS_REL_PATH


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    rendered = value.strip()
    if rendered.endswith("Z"):
        rendered = rendered[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(rendered)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_hash(value: object) -> str:
    rendered = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]


def _file_hash(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:16]


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _short(text: str, limit: int = 96) -> str:
    rendered = " ".join(text.split())
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 3].rstrip() + "..."


def _as_path(target: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    return path if path.is_absolute() else target / path


def _format_inline_list(values: list[str]) -> str:
    return "[" + ", ".join(dogfood_cmd._format_toml_value(value) for value in values) + "]"


def _format_inline_table(values: dict[str, str]) -> str:
    rendered = ", ".join(f"{key} = {dogfood_cmd._format_toml_value(value)}" for key, value in values.items())
    return "{ " + rendered + " }"


def _format_tools_toml(tools: tuple[dict[str, Any], ...] = DEFAULT_TOOLS) -> str:
    lines = [
        "# Local portable tool and skill catalog. Brigade inspects this file but does not sync projections.",
        "",
    ]
    for tool in tools:
        lines.append("[[tool]]")
        for key in ("id", "name", "family", "enabled", "description", "source_path"):
            lines.append(f"{key} = {dogfood_cmd._format_toml_value(tool[key])}")
        lines.append(f"supported_harnesses = {_format_inline_list(list(tool['supported_harnesses']))}")
        lines.append(f"projections = {_format_inline_table(dict(tool['projections']))}")
        lines.append("")
    return "\n".join(lines)


def _load_config(target: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = config_path(target)
    if not path.is_file():
        return [], [f"tool catalog config missing: {path}"]
    if tomllib is None:
        return [], ["tool catalog requires Python tomllib support"]
    try:
        payload = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:  # type: ignore[union-attr]
        return [], [f"invalid tool catalog config: {exc}"]
    values = payload.get("tool")
    if not isinstance(values, list):
        return [], ["tool catalog must contain [[tool]] entries"]
    tools: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, raw_tool in enumerate(values, start=1):
        label = f"tool {index}"
        if not isinstance(raw_tool, dict):
            errors.append(f"{label} must be a table")
            continue
        tool: dict[str, Any] = {"raw": raw_tool}
        for field in ("id", "name", "family"):
            value = raw_tool.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{label}: {field} must be a non-empty string")
            else:
                tool[field] = value.strip()
        if tool.get("family") and tool["family"] not in FAMILIES:
            errors.append(f"{label}: family must be one of: {', '.join(FAMILIES)}")
        tool_id = tool.get("id")
        if isinstance(tool_id, str):
            if tool_id in seen:
                errors.append(f"{label}: duplicate id {tool_id}")
            seen.add(tool_id)
        enabled = raw_tool.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append(f"{label}: enabled must be true or false")
        else:
            tool["enabled"] = enabled
        for field in (
            "description",
            "source_path",
            "manifest_path",
            "schema_path",
            "command",
            "auth_label",
            "health_path",
            "fingerprint",
            "input_schema_path",
            "output_schema_path",
            "examples_path",
            "approval_mode",
            "cwd",
        ):
            value = raw_tool.get(field)
            if value is not None:
                if not isinstance(value, str):
                    errors.append(f"{label}: {field} must be a string")
                else:
                    tool[field] = value.strip()
        if tool.get("approval_mode") and tool["approval_mode"] not in APPROVAL_MODES:
            errors.append(f"{label}: approval_mode must be one of: {', '.join(APPROVAL_MODES)}")
        for field in ("permissions", "effects", "env_labels"):
            values = raw_tool.get(field, [])
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                errors.append(f"{label}: {field} must be a list of strings")
                values = []
            tool[field] = [item.strip() for item in values if isinstance(item, str) and item.strip()]
        argument_template = raw_tool.get("argument_template", {})
        if argument_template is None:
            argument_template = {}
        if not isinstance(argument_template, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in argument_template.items()):
            errors.append(f"{label}: argument_template must be a table of name = template")
            argument_template = {}
        tool["argument_template"] = {str(key): str(value) for key, value in argument_template.items()}
        timeout = raw_tool.get("timeout")
        if timeout is not None:
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
                errors.append(f"{label}: timeout must be a positive number")
            else:
                tool["timeout"] = float(timeout)
        harnesses = raw_tool.get("supported_harnesses", [])
        if not isinstance(harnesses, list) or any(not isinstance(item, str) or not item.strip() for item in harnesses):
            errors.append(f"{label}: supported_harnesses must be a list of strings")
            harnesses = []
        tool["supported_harnesses"] = [item.strip() for item in harnesses if isinstance(item, str) and item.strip()]
        projections = raw_tool.get("projections", {})
        if not isinstance(projections, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in projections.items()):
            errors.append(f"{label}: projections must be a table of harness = path")
            projections = {}
        tool["projections"] = {str(key): str(value) for key, value in projections.items()}
        projection_fingerprints = raw_tool.get("projection_fingerprints", {})
        if projection_fingerprints is None:
            projection_fingerprints = {}
        if not isinstance(projection_fingerprints, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in projection_fingerprints.items()):
            errors.append(f"{label}: projection_fingerprints must be a table of harness = fingerprint")
            projection_fingerprints = {}
        tool["projection_fingerprints"] = {str(key): str(value) for key, value in projection_fingerprints.items()}
        tools.append(tool)
    return tools, errors


def _unsafe_fields(value: object, prefix: str = "") -> list[str]:
    unsafe: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            rendered = str(key)
            path = f"{prefix}.{rendered}" if prefix else rendered
            if UNSAFE_FIELD_PATTERN.search(rendered) and rendered != "auth_label":
                unsafe.append(path)
                continue
            unsafe.extend(_unsafe_fields(nested, path))
    elif isinstance(value, list):
        for index, nested in enumerate(value, start=1):
            unsafe.extend(_unsafe_fields(nested, f"{prefix}[{index}]"))
    return unsafe


def _command_parts(command: object) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        return []
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _command_resolves(command: object) -> bool:
    parts = _command_parts(command)
    if not parts:
        return False
    executable = parts[0]
    if executable == "brigade":
        return True
    if "/" in executable:
        return Path(executable).expanduser().exists()
    return shutil.which(executable) is not None


def _high_risk_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    return any(pattern.search(command) for pattern in HIGH_RISK_COMMAND_PATTERNS)


def _read_json(path: Path) -> tuple[object | None, str | None]:
    try:
        payload = json.loads(path.read_text())
    except OSError as exc:
        return None, str(exc)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    return payload, None


def _redact_value(key: str, value: object) -> object:
    if UNSAFE_FIELD_PATTERN.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(nested_key): _redact_value(str(nested_key), nested_value) for nested_key, nested_value in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    return value


def _redact_payload(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _redact_value(str(key), nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _redact_text(value: object, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    text = re.sub(
        r"(?i)\b([A-Za-z0-9_-]*(?:password|secret|token|credential|api[_-]?key)[A-Za-z0-9_-]*)\b\s*[:=]\s*[^\s\"']+",
        lambda match: f"{match.group(1)}=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(\"(?:password|secret|token|credential|api[_-]?key)\"\s*:\s*\")[^\"]+(\")",
        r"\1[redacted]\2",
        text,
    )
    return _short(text, limit)


def _schema_path(target: Path, tool: dict[str, Any], field: str) -> Path | None:
    return _as_path(target, tool.get(field))


def _load_schema(target: Path, tool: dict[str, Any], field: str) -> tuple[object | None, str | None]:
    path = _schema_path(target, tool, field)
    if path is None:
        return None, None
    if not path.is_file():
        return None, f"missing schema: {path}"
    return _read_json(path)


def _schema_shape_errors(schema: object, *, path: str = "$", root: bool = True) -> list[str]:
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    schema_type = schema.get("type")
    if root and schema_type != "object":
        return [f"{path}: root schema type must be object"]
    if schema_type is not None and schema_type not in SCHEMA_TYPES:
        return [f"{path}: unsupported type {schema_type!r}"]
    if "enum" in schema and not isinstance(schema["enum"], list):
        return [f"{path}.enum: must be a list"]
    errors: list[str] = []
    if schema_type == "object" or "properties" in schema or "required" in schema:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            errors.append(f"{path}.properties: must be an object")
        else:
            for key, nested in properties.items():
                if not isinstance(key, str):
                    errors.append(f"{path}.properties: keys must be strings")
                    continue
                errors.extend(_schema_shape_errors(nested, path=f"{path}.{key}", root=False))
        required = schema.get("required", [])
        if required is not None and (
            not isinstance(required, list) or any(not isinstance(item, str) for item in required)
        ):
            errors.append(f"{path}.required: must be a list of strings")
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, bool):
            errors.append(f"{path}.additionalProperties: only boolean values are supported")
    if schema_type == "array":
        items = schema.get("items")
        if items is None:
            errors.append(f"{path}.items: required for arrays")
        else:
            errors.extend(_schema_shape_errors(items, path=f"{path}[]", root=False))
    unsupported = sorted(set(schema) - {"type", "properties", "required", "additionalProperties", "items", "enum", "description"})
    if unsupported:
        errors.append(f"{path}: unsupported schema keywords: {', '.join(unsupported)}")
    return errors


def _json_type_matches(value: object, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _validate_json_value(value: object, schema: dict[str, Any], *, path: str = "$") -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")
    if isinstance(schema_type, str) and not _json_type_matches(value, schema_type):
        errors.append(f"{path}: expected {schema_type}")
        return errors
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {', '.join(repr(item) for item in schema['enum'])}")
    if (schema_type == "object" or "properties" in schema or "required" in schema) and isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required")
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property not allowed")
        for key, nested_schema in properties.items():
            if key in value and isinstance(nested_schema, dict):
                errors.extend(_validate_json_value(value[key], nested_schema, path=f"{path}.{key}"))
    if schema_type == "array" and isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(_validate_json_value(item, items, path=f"{path}[{index}]"))
    return errors


def _render_argument_template(template: str, args: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = args.get(key)
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True)
        if value is None:
            return ""
        return str(value)

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, template)


def _contract_defined(tool: dict[str, Any]) -> bool:
    return any(
        tool.get(field)
        for field in (
            "input_schema_path",
            "output_schema_path",
            "examples_path",
            "permissions",
            "effects",
            "approval_mode",
            "env_labels",
            "argument_template",
        )
    )


def _contract_summary(target: Path, tool: dict[str, Any]) -> dict[str, Any]:
    input_path = _schema_path(target, tool, "input_schema_path")
    output_path = _schema_path(target, tool, "output_schema_path")
    examples_path = _as_path(target, tool.get("examples_path"))
    return {
        "tool_id": tool.get("id"),
        "name": tool.get("name"),
        "family": tool.get("family"),
        "description": tool.get("description", ""),
        "command": tool.get("command"),
        "timeout": tool.get("timeout"),
        "auth_label": tool.get("auth_label"),
        "cwd": tool.get("cwd"),
        "approval_mode": tool.get("approval_mode") or "never",
        "permissions": tool.get("permissions", []),
        "effects": tool.get("effects", []),
        "env_labels": tool.get("env_labels", []),
        "argument_template": tool.get("argument_template", {}),
        "input_schema_path": str(input_path) if input_path is not None else None,
        "output_schema_path": str(output_path) if output_path is not None else None,
        "examples_path": str(examples_path) if examples_path is not None else None,
        "has_contract": _contract_defined(tool),
    }


def _source_fingerprint(target: Path, tool: dict[str, Any]) -> str:
    source_path = _as_path(target, tool.get("source_path"))
    if source_path is not None:
        source_hash = _file_hash(source_path)
        if source_hash is not None:
            return source_hash
    return str(tool.get("fingerprint") or "")


def _contract_fingerprint(target: Path, tool: dict[str, Any]) -> str:
    paths: dict[str, str | None] = {}
    for field in ("input_schema_path", "output_schema_path", "examples_path"):
        path = _as_path(target, tool.get(field))
        paths[field] = _file_hash(path) if path is not None else None
    return _stable_hash(
        {
            "tool_id": tool.get("id"),
            "command": tool.get("command"),
            "timeout": tool.get("timeout"),
            "auth_label": tool.get("auth_label"),
            "cwd": tool.get("cwd"),
            "approval_mode": tool.get("approval_mode"),
            "permissions": tool.get("permissions", []),
            "effects": tool.get("effects", []),
            "env_labels": tool.get("env_labels", []),
            "argument_template": tool.get("argument_template", {}),
            "paths": paths,
        }
    )


def _contract_issues(target: Path, tool: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not _contract_defined(tool):
        if tool.get("command") or tool.get("family") in {"script", "custom", "mcp", "openapi", "graphql"}:
            issues.append(_tool_issue(tool, "missing_contract", "tool has no call contract metadata"))
        return issues
    for field, issue_prefix in (("input_schema_path", "input_schema"), ("output_schema_path", "output_schema")):
        schema_path = _schema_path(target, tool, field)
        if schema_path is None:
            if field == "input_schema_path":
                issues.append(_tool_issue(tool, "missing_input_schema", "input_schema_path is required for call planning"))
            continue
        if not schema_path.is_file():
            issues.append(_tool_issue(tool, f"missing_{issue_prefix}", f"missing schema: {schema_path}"))
            continue
        schema, error = _read_json(schema_path)
        if error is not None:
            issues.append(_tool_issue(tool, f"invalid_{issue_prefix}", f"{schema_path}: {error}"))
            continue
        shape_errors = _schema_shape_errors(schema)
        if shape_errors:
            issues.append(_tool_issue(tool, f"unsupported_{issue_prefix}", f"{schema_path}: {'; '.join(shape_errors)}"))
    examples_path = _as_path(target, tool.get("examples_path"))
    if tool.get("examples_path") and (examples_path is None or not examples_path.is_file()):
        issues.append(_tool_issue(tool, "missing_examples", f"missing examples: {examples_path}"))
    for label in tool.get("env_labels", []):
        if UNSAFE_FIELD_PATTERN.search(label):
            issues.append(_tool_issue(tool, "unsafe_env_labels", f"unsafe env label name: {label}"))
    for key, value in tool.get("argument_template", {}).items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
            issues.append(_tool_issue(tool, "bad_argument_template", f"invalid template output key: {key}"))
        for variable in re.findall(r"\{([^{}]+)\}", str(value)):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
                issues.append(_tool_issue(tool, "bad_argument_template", f"invalid template variable: {variable}"))
    return issues


def _managed_header(metadata: dict[str, Any]) -> str:
    rendered = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    return f"<!-- {PROJECTION_MARKER} {rendered} -->"


def _read_projection(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        text = path.read_text()
    except OSError:
        return None, None
    lines = text.splitlines(keepends=True)
    if not lines:
        return None, text
    first = lines[0].strip()
    prefix = f"<!-- {PROJECTION_MARKER} "
    if not first.startswith(prefix) or not first.endswith(" -->"):
        return None, text
    raw = first[len(prefix) : -len(" -->")]
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        return None, text
    if not isinstance(metadata, dict):
        return None, text
    return metadata, "".join(lines[1:])


def _relative_path(target: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.relative_to(target))
    except ValueError:
        return str(path)


def _render_projection_body(tool: dict[str, Any], harness: str, source_text: str, source_ref: str) -> str:
    family = str(tool.get("family") or "")
    tool_id = str(tool.get("id") or "")
    name = str(tool.get("name") or tool_id)
    description = str(tool.get("description") or "")
    if family in {"slash-command", "skill", "superpower"}:
        return source_text if source_text.endswith("\n") else source_text + "\n"
    if family == "script":
        lines = [
            f"# {name}",
            "",
            "Managed Brigade script projection.",
            "",
            f"- tool_id: `{tool_id}`",
            f"- harness: `{harness}`",
            f"- source: `{source_ref}`",
            f"- command: `{tool.get('command') or ''}`",
        ]
        if description:
            lines.extend(["", description])
        lines.extend(["", "## Source", "", "```text", source_text.rstrip(), "```", ""])
        return "\n".join(lines)
    if family == "mcp":
        lines = [
            f"# {name}",
            "",
            "Managed Brigade MCP projection stub.",
            "",
            f"- tool_id: `{tool_id}`",
            f"- harness: `{harness}`",
            f"- source: `{source_ref}`",
            "",
            "This projection documents the local MCP catalog entry. Brigade does not start MCP servers or write runtime MCP configs from this file.",
        ]
        if description:
            lines.extend(["", description])
        return "\n".join(lines) + "\n"
    lines = [
        f"# {name}",
        "",
        "Managed Brigade tool projection.",
        "",
        f"- tool_id: `{tool_id}`",
        f"- family: `{family}`",
        f"- harness: `{harness}`",
        f"- source: `{source_ref}`",
    ]
    if description:
        lines.extend(["", description])
    if source_text.strip():
        lines.extend(["", "## Source", "", "```text", source_text.rstrip(), "```"])
    return "\n".join(lines) + "\n"


def _projection_item(
    target: Path,
    tool: dict[str, Any],
    harness: str,
    *,
    generated_at: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    projections = tool.get("projections") if isinstance(tool.get("projections"), dict) else {}
    projection_value = projections.get(harness)
    source_path = _as_path(target, tool.get("source_path"))
    projection_path = _as_path(target, projection_value)
    item: dict[str, Any] = {
        "tool_id": tool.get("id"),
        "name": tool.get("name"),
        "family": tool.get("family"),
        "harness": harness,
        "source_path": str(source_path) if source_path is not None else None,
        "projection_path": str(projection_path) if projection_path is not None else None,
        "managed": False,
        "metadata": None,
    }
    if projection_path is None:
        item.update({"status": "missing", "action": "skip", "detail": f"missing projection target for {harness}"})
        return item
    if source_path is None or not source_path.is_file():
        item.update({"status": "missing_source", "action": "skip", "detail": f"missing source: {source_path}"})
        return item
    try:
        source_text = source_path.read_text()
    except OSError as exc:
        item.update({"status": "missing_source", "action": "skip", "detail": f"cannot read source: {exc}"})
        return item
    source_fingerprint = _text_hash(source_text)
    body = _render_projection_body(tool, harness, source_text, _relative_path(target, source_path))
    projection_fingerprint = _text_hash(body)
    item.update(
        {
            "source_fingerprint": source_fingerprint,
            "expected_fingerprint": projection_fingerprint,
            "expected_projection_fingerprint": projection_fingerprint,
        }
    )
    metadata = {
        "tool_id": tool.get("id"),
        "family": tool.get("family"),
        "harness": harness,
        "source_fingerprint": source_fingerprint,
        "projection_fingerprint": projection_fingerprint,
        "generated_at": generated_at.isoformat(),
    }
    rendered = _managed_header(metadata) + "\n" + body
    item["rendered"] = rendered
    if not projection_path.exists():
        item.update({"status": "missing", "action": "create", "detail": "projection will be created"})
        return item
    existing_metadata, existing_body = _read_projection(projection_path)
    if existing_metadata is None:
        item.update(
            {
                "status": "unmanaged",
                "action": "update" if force else "conflict",
                "detail": "existing projection is not managed by Brigade",
            }
        )
        return item
    item["managed"] = True
    item["metadata"] = existing_metadata
    existing_projection_fingerprint = str(existing_metadata.get("projection_fingerprint") or "")
    actual_projection_fingerprint = _text_hash(existing_body or "")
    item["actual_projection_fingerprint"] = actual_projection_fingerprint
    if existing_projection_fingerprint != actual_projection_fingerprint:
        item.update(
            {
                "status": "conflicted",
                "action": "update" if force else "conflict",
                "detail": "managed projection has local edits",
            }
        )
        return item
    if (
        existing_metadata.get("source_fingerprint") == source_fingerprint
        and existing_projection_fingerprint == projection_fingerprint
    ):
        item.update({"status": "current", "action": "skip", "detail": "projection is current"})
        return item
    item.update({"status": "stale", "action": "update", "detail": "projection will be updated"})
    return item


def _projection_plan_payload(target: Path, tool_id: str | None = None, *, force: bool = False) -> dict[str, Any]:
    target = target.expanduser().resolve()
    tools, errors = _load_config(target)
    selected: list[dict[str, Any]] = []
    for tool in tools:
        if not tool.get("enabled", True):
            continue
        if tool_id is None or tool.get("id") == tool_id:
            selected.append(tool)
    if tool_id is not None and not selected and not errors:
        errors.append(f"tool not found: {tool_id}")
    generated_at = datetime.now(timezone.utc)
    projections: list[dict[str, Any]] = []
    for tool in selected:
        for harness in tool.get("supported_harnesses", []):
            projections.append(_projection_item(target, tool, harness, generated_at=generated_at, force=force))
    counts: dict[str, int] = {}
    for item in projections:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "target": str(target),
        "config_path": str(config_path(target)),
        "valid": not errors,
        "errors": errors,
        "tool_id": tool_id,
        "tools": [tool.get("id") for tool in selected],
        "projections": [
            {key: value for key, value in item.items() if key != "rendered"}
            for item in projections
        ],
        "counts": counts,
    }


def _projection_issue(tool: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "projection")
    harness = str(item.get("harness") or "")
    detail = str(item.get("detail") or "")
    return _tool_issue(
        tool,
        f"{status}_projection" if status not in {"missing"} else "missing_projection",
        f"{harness}: {detail}",
        harness=harness,
        target=str(item.get("projection_path") or ""),
    )


def _tool_issue(tool: dict[str, Any], issue_type: str, detail: str, *, harness: str | None = None, target: str | None = None) -> dict[str, Any]:
    return {
        "status": WARN,
        "name": f"tool_{issue_type}",
        "tool_id": tool.get("id"),
        "family": tool.get("family"),
        "issue_type": issue_type,
        "harness": harness,
        "projection_target": target,
        "description": tool.get("description"),
        "detail": detail,
    }


def _inspect_mcp_config(tool: dict[str, Any], path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    payload, error = _read_json(path)
    if error is not None:
        return None, [_tool_issue(tool, "invalid_schema", f"{path}: {error}")]
    if not isinstance(payload, dict) or not isinstance(payload.get("mcpServers"), dict):
        return None, []
    servers = payload["mcpServers"]
    issues: list[dict[str, Any]] = []
    server_ids = sorted(str(key) for key in servers)
    for server_id, server in servers.items():
        if not isinstance(server, dict):
            issues.append(_tool_issue(tool, "invalid_mcp_server", f"{server_id} must be an object"))
            continue
        command = server.get("command")
        if not isinstance(command, str) or not command.strip():
            issues.append(_tool_issue(tool, "missing_command", f"MCP server {server_id} is missing command"))
        elif _high_risk_command(command):
            issues.append(_tool_issue(tool, "high_risk_command", f"MCP server {server_id} command shape is high risk"))
        if "timeout" not in server and "timeout_seconds" not in server:
            issues.append(_tool_issue(tool, "missing_timeout", f"MCP server {server_id} is missing timeout metadata"))
    return {"server_count": len(server_ids), "server_ids": server_ids}, issues


def _inspect_tool(target: Path, tool: dict[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    now = now or datetime.now(timezone.utc)
    issues: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "id": tool.get("id"),
        "name": tool.get("name"),
        "family": tool.get("family"),
        "enabled": tool.get("enabled", True),
        "description": tool.get("description", ""),
        "supported_harnesses": tool.get("supported_harnesses", []),
        "projection_coverage": {},
        "schema_available": False,
        "manifest_available": False,
        "auth_label": tool.get("auth_label"),
        "tool_count": 1,
        "contract": _contract_summary(target, tool),
    }
    unsafe = _unsafe_fields(tool.get("raw", {}))
    if unsafe:
        issues.append(_tool_issue(tool, "unsafe_auth_fields", f"unsafe field names: {', '.join(unsafe[:8])}"))
    issues.extend(_contract_issues(target, tool))
    source_path = _as_path(target, tool.get("source_path"))
    if source_path is not None:
        summary["source_path"] = str(source_path)
        source_hash = _file_hash(source_path)
        summary["source_fingerprint"] = source_hash or tool.get("fingerprint")
        if not source_path.is_file():
            issues.append(_tool_issue(tool, "missing_source", f"missing source: {source_path}"))
    manifest_path = _as_path(target, tool.get("manifest_path"))
    if manifest_path is not None:
        summary["manifest_path"] = str(manifest_path)
        summary["manifest_available"] = manifest_path.is_file()
        if not manifest_path.is_file():
            issues.append(_tool_issue(tool, "missing_manifest", f"missing manifest: {manifest_path}"))
    schema_path = _as_path(target, tool.get("schema_path"))
    if schema_path is not None:
        summary["schema_path"] = str(schema_path)
        if not schema_path.is_file():
            issues.append(_tool_issue(tool, "missing_schema", f"missing schema: {schema_path}"))
        else:
            schema, error = _read_json(schema_path)
            if error is not None:
                issues.append(_tool_issue(tool, "invalid_schema", f"{schema_path}: {error}"))
            else:
                summary["schema_available"] = True
                if isinstance(schema, dict) and isinstance(schema.get("tools"), list):
                    summary["tool_count"] = len(schema["tools"])
    health_path = _as_path(target, tool.get("health_path"))
    if health_path is not None:
        summary["health_path"] = str(health_path)
        if not health_path.is_file():
            issues.append(_tool_issue(tool, "missing_health", f"missing health file: {health_path}"))
        else:
            age_hours = (now.timestamp() - health_path.stat().st_mtime) / 3600
            if age_hours > HEALTH_STALE_HOURS:
                issues.append(_tool_issue(tool, "stale_health", f"health file is {age_hours:.1f}h old"))
    command = tool.get("command")
    if tool.get("family") in {"script", "custom"} and not command:
        issues.append(_tool_issue(tool, "missing_command", "command is required for script/custom tools"))
    if command:
        summary["command"] = command
        if not _command_resolves(command):
            issues.append(_tool_issue(tool, "missing_command", f"command is not resolvable: {_short(str(command))}"))
        if _high_risk_command(command):
            issues.append(_tool_issue(tool, "high_risk_command", "command shape is high risk"))
    for harness in tool.get("supported_harnesses", []):
        projection_item = _projection_item(target, tool, harness)
        status = str(projection_item.get("status") or "missing")
        summary["projection_coverage"][harness] = status
        if projection_item.get("projection_path"):
            summary.setdefault("projection_paths", {})[harness] = projection_item["projection_path"]
        if status == "missing" and projection_item.get("action") == "skip":
            issues.append(_tool_issue(tool, "parity_gap", f"missing projection for {harness}", harness=harness))
            continue
        if status == "missing_source":
            continue
        if status in {"missing", "stale", "conflicted", "unmanaged"}:
            issues.append(_projection_issue(tool, projection_item))
            continue
    if tool.get("family") == "mcp":
        mcp_path = manifest_path or schema_path or source_path
        if mcp_path is not None and mcp_path.is_file():
            mcp_summary, mcp_issues = _inspect_mcp_config(tool, mcp_path)
            if mcp_summary:
                summary["mcp"] = mcp_summary
                summary["tool_count"] = mcp_summary["server_count"]
            issues.extend(mcp_issues)
    return summary, issues


def _find_tool(target: Path, tool_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    tools, errors = _load_config(target)
    for tool in tools:
        if tool.get("enabled", True) and tool.get("id") == tool_id:
            return tool, errors
    if not errors:
        errors.append(f"tool not found: {tool_id}")
    return None, errors


def _contracts_payload(target: Path) -> dict[str, Any]:
    target = target.expanduser().resolve()
    tools, errors = _load_config(target)
    contracts: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for tool in tools:
        if not tool.get("enabled", True):
            continue
        summary = _contract_summary(target, tool)
        tool_issues = _contract_issues(target, tool)
        summary["issue_count"] = len(tool_issues)
        summary["issues"] = tool_issues
        contracts.append(summary)
        issues.extend(tool_issues)
    return {
        "target": str(target),
        "config_path": str(config_path(target)),
        "valid": not errors,
        "errors": errors,
        "contracts": contracts,
        "contract_count": len(contracts),
        "issue_count": len(issues),
        "issues": issues,
    }


def _describe_payload(target: Path, tool_id: str) -> dict[str, Any]:
    target = target.expanduser().resolve()
    tool, errors = _find_tool(target, tool_id)
    summary: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = []
    if tool is not None:
        inspected, inspect_issues = _inspect_tool(target, tool)
        summary = inspected
        issues = inspect_issues
    return {
        "target": str(target),
        "config_path": str(config_path(target)),
        "valid": not errors,
        "errors": errors,
        "tool": summary,
        "issues": issues,
        "issue_count": len(issues),
    }


def _load_args(args: str | None, args_json: Path | None) -> tuple[object | None, str | None]:
    if args and args_json:
        return None, "pass only one of --args or --args-json"
    if args_json is not None:
        try:
            return json.loads(args_json.expanduser().read_text()), None
        except OSError as exc:
            return None, str(exc)
        except json.JSONDecodeError as exc:
            return None, f"invalid args JSON: {exc.msg}"
    if args is not None:
        try:
            return json.loads(args), None
        except json.JSONDecodeError as exc:
            return None, f"invalid args JSON: {exc.msg}"
    return {}, None


def _call_plan_payload(
    target: Path,
    tool_id: str,
    *,
    args: str | None = None,
    args_json: Path | None = None,
) -> dict[str, Any]:
    target = target.expanduser().resolve()
    tool, errors = _find_tool(target, tool_id)
    parsed_args, args_error = _load_args(args, args_json)
    blockers: list[str] = list(errors)
    validation_errors: list[str] = []
    if args_error is not None:
        blockers.append(args_error)
    if parsed_args is not None and not isinstance(parsed_args, dict):
        blockers.append("args must be a JSON object")
    mapped_arguments: dict[str, str] = {}
    projection_blockers: list[dict[str, Any]] = []
    schema: object | None = None
    if tool is not None:
        if not tool.get("command"):
            blockers.append("command is required for call planning")
        auth_label = str(tool.get("auth_label") or "")
        if auth_label and UNSAFE_FIELD_PATTERN.search(auth_label):
            blockers.append("auth_label appears unsafe")
        for label in tool.get("env_labels", []):
            if UNSAFE_FIELD_PATTERN.search(str(label)):
                blockers.append(f"env label appears unsafe: {label}")
        schema, schema_error = _load_schema(target, tool, "input_schema_path")
        if schema_error is not None:
            blockers.append(schema_error)
        elif schema is None:
            blockers.append("input_schema_path is required for call planning")
        else:
            shape_errors = _schema_shape_errors(schema)
            if shape_errors:
                blockers.extend(shape_errors)
            elif isinstance(parsed_args, dict):
                validation_errors = _validate_json_value(parsed_args, schema)  # type: ignore[arg-type]
                blockers.extend(validation_errors)
        for harness in tool.get("supported_harnesses", []):
            projection = _projection_item(target, tool, harness)
            if projection.get("status") in {"conflicted", "unmanaged"}:
                projection_blockers.append({key: value for key, value in projection.items() if key != "rendered"})
        if projection_blockers:
            blockers.append("one or more projections are conflicted or unmanaged")
        if isinstance(parsed_args, dict):
            for key, template in tool.get("argument_template", {}).items():
                missing = [var for var in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template) if var not in parsed_args]
                for var in missing:
                    blockers.append(f"argument_template {key} references missing arg {var}")
                rendered = _render_argument_template(str(template), parsed_args)
                if UNSAFE_FIELD_PATTERN.search(str(key)) or any(
                    UNSAFE_FIELD_PATTERN.search(var)
                    for var in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(template))
                ):
                    mapped_arguments[str(key)] = "[redacted]"
                else:
                    mapped_arguments[str(key)] = rendered
    safe_env_labels = [
        "[redacted]" if UNSAFE_FIELD_PATTERN.search(str(label)) else str(label)
        for label in (tool.get("env_labels", []) if tool is not None else [])
    ]
    safe_args = _redact_payload(parsed_args) if parsed_args is not None else None
    plan_payload = {
        "tool_id": tool_id,
        "family": tool.get("family") if tool is not None else None,
        "command": tool.get("command") if tool is not None else None,
        "cwd": tool.get("cwd") if tool is not None else None,
        "timeout": tool.get("timeout") if tool is not None else None,
        "auth_label": "[redacted]" if tool is not None and UNSAFE_FIELD_PATTERN.search(str(tool.get("auth_label") or "")) else (tool.get("auth_label") if tool is not None else None),
        "env_labels": safe_env_labels,
        "arguments": mapped_arguments,
        "args": safe_args,
        "permissions": tool.get("permissions", []) if tool is not None else [],
        "effects": tool.get("effects", []) if tool is not None else [],
        "approval_required": (tool.get("approval_mode") if tool is not None else "never") != "never",
        "approval_mode": tool.get("approval_mode", "never") if tool is not None else "never",
    }
    projection_summary: dict[str, Any] = {"counts": {}, "projections": []}
    contract_fingerprint = None
    source_fingerprint = None
    if tool is not None:
        projection_summary = _projection_plan_payload(target, tool_id=tool_id)["counts"]
        projection_items = _projection_plan_payload(target, tool_id=tool_id)["projections"]
        projection_summary = {
            "counts": projection_summary,
            "projections": [
                {
                    "harness": item.get("harness"),
                    "status": item.get("status"),
                    "action": item.get("action"),
                    "projection_path": item.get("projection_path"),
                }
                for item in projection_items
            ],
        }
        contract_fingerprint = _contract_fingerprint(target, tool)
        source_fingerprint = _source_fingerprint(target, tool)
    return {
        "target": str(target),
        "config_path": str(config_path(target)),
        "valid": tool is not None and not blockers,
        "tool_id": tool_id,
        "plan": plan_payload,
        "blockers": blockers,
        "validation_errors": validation_errors,
        "projection_blockers": projection_blockers,
        "projection_summary": projection_summary,
        "contract_fingerprint": contract_fingerprint,
        "source_fingerprint": source_fingerprint,
    }


def _read_calls(target: Path) -> list[dict[str, Any]]:
    path = calls_path(target)
    if not path.is_file():
        return []
    calls: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            calls.append(item)
    return calls


def _write_calls(target: Path, calls: list[dict[str, Any]]) -> None:
    path = calls_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in calls)
    path.write_text(text)


def _call_fingerprint(plan_payload: dict[str, Any]) -> str:
    return _stable_hash(
        {
            "tool_id": plan_payload.get("tool_id"),
            "plan": plan_payload.get("plan"),
            "contract_fingerprint": plan_payload.get("contract_fingerprint"),
            "source_fingerprint": plan_payload.get("source_fingerprint"),
        }
    )


def _call_plan_from_record(call: dict[str, Any]) -> dict[str, Any]:
    contract = call.get("contract") if isinstance(call.get("contract"), dict) else {}
    return {
        "tool_id": call.get("tool_id"),
        "family": call.get("family"),
        "command": call.get("command"),
        "cwd": contract.get("cwd"),
        "timeout": contract.get("timeout"),
        "auth_label": contract.get("auth_label"),
        "env_labels": contract.get("env_labels", []),
        "arguments": call.get("arguments"),
        "args": call.get("args"),
        "permissions": contract.get("permissions", []),
        "effects": contract.get("effects", []),
        "approval_required": contract.get("approval_required"),
        "approval_mode": contract.get("approval_mode"),
    }


def _stored_call_fingerprint(call: dict[str, Any]) -> str:
    return _stable_hash(
        {
            "tool_id": call.get("tool_id"),
            "plan": _call_plan_from_record(call),
            "contract_fingerprint": call.get("contract_fingerprint"),
            "source_fingerprint": call.get("source_fingerprint"),
        }
    )


def _approval_fingerprint(call: dict[str, Any]) -> str:
    return _stable_hash(
        {
            "id": call.get("id"),
            "tool_id": call.get("tool_id"),
            "status": call.get("status"),
            "reviewed_at": call.get("reviewed_at"),
            "review_reason": call.get("review_reason"),
            "call_fingerprint": call.get("call_fingerprint"),
            "contract_fingerprint": call.get("contract_fingerprint"),
            "source_fingerprint": call.get("source_fingerprint"),
        }
    )


def _make_call_record(plan_payload: dict[str, Any]) -> dict[str, Any]:
    fingerprint = _call_fingerprint(plan_payload)
    now = _now().isoformat()
    plan = plan_payload.get("plan") if isinstance(plan_payload.get("plan"), dict) else {}
    return {
        "id": f"call-{fingerprint}",
        "status": "pending",
        "created_at": now,
        "reviewed_at": None,
        "review_reason": None,
        "tool_id": plan_payload.get("tool_id"),
        "family": plan.get("family"),
        "command": plan.get("command"),
        "args": plan.get("args"),
        "arguments": plan.get("arguments"),
        "contract": {
            "approval_mode": plan.get("approval_mode"),
            "approval_required": plan.get("approval_required"),
            "permissions": plan.get("permissions", []),
            "effects": plan.get("effects", []),
            "auth_label": plan.get("auth_label"),
            "env_labels": plan.get("env_labels", []),
            "cwd": plan.get("cwd"),
            "timeout": plan.get("timeout"),
        },
        "blockers": plan_payload.get("blockers", []),
        "projection_summary": plan_payload.get("projection_summary", {}),
        "contract_fingerprint": plan_payload.get("contract_fingerprint"),
        "source_fingerprint": plan_payload.get("source_fingerprint"),
        "call_fingerprint": fingerprint,
        "approval_fingerprint": None,
        "started_at": None,
        "completed_at": None,
        "run_id": None,
        "receipt_path": None,
        "exit_code": None,
    }


def _queue_call_payload(
    target: Path,
    tool_id: str,
    *,
    args: str | None = None,
    args_json: Path | None = None,
    include_blocked: bool = False,
) -> tuple[dict[str, Any], int]:
    target = target.expanduser().resolve()
    plan_payload = _call_plan_payload(target, tool_id, args=args, args_json=args_json)
    record = _make_call_record(plan_payload)
    if record["blockers"] and not include_blocked:
        return {
            "target": str(target),
            "calls_path": str(calls_path(target)),
            "created": 0,
            "skipped": 0,
            "blocked": 1,
            "call": record,
            "reason": "blocked call plans require --include-blocked",
        }, 1
    calls = _read_calls(target)
    for existing in calls:
        if existing.get("call_fingerprint") != record["call_fingerprint"]:
            continue
        if existing.get("status") in {"pending", "approved"}:
            return {
                "target": str(target),
                "calls_path": str(calls_path(target)),
                "created": 0,
                "skipped": 1,
                "blocked": 0,
                "call": existing,
                "reason": f"equivalent call already {existing.get('status')}",
            }, 0
        if existing.get("status") == "rejected":
            return {
                "target": str(target),
                "calls_path": str(calls_path(target)),
                "created": 0,
                "skipped": 1,
                "blocked": 0,
                "call": existing,
                "reason": "equivalent rejected call requires changed args or contract fingerprint",
            }, 0
    calls.append(record)
    _write_calls(target, calls)
    return {
        "target": str(target),
        "calls_path": str(calls_path(target)),
        "created": 1,
        "skipped": 0,
        "blocked": 0,
        "call": record,
        "reason": None,
    }, 0


def _resolve_call(target: Path, call_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    calls = _read_calls(target)
    matches = [item for item in calls if str(item.get("id", "")).startswith(call_id)]
    if not matches:
        return None, calls, f"call not found: {call_id}"
    if len(matches) > 1:
        return None, calls, f"call id is ambiguous: {call_id}"
    return matches[0], calls, None


def _call_current_fingerprints(target: Path, call: dict[str, Any]) -> tuple[str | None, str | None]:
    tool_id = str(call.get("tool_id") or "")
    tool, _ = _find_tool(target, tool_id)
    if tool is None:
        return None, None
    return _contract_fingerprint(target, tool), _source_fingerprint(target, tool)


def _call_projection_summary(target: Path, tool_id: str) -> dict[str, Any]:
    payload = _projection_plan_payload(target, tool_id=tool_id)
    return {
        "counts": payload.get("counts", {}),
        "projections": [
            {
                "harness": item.get("harness"),
                "status": item.get("status"),
                "action": item.get("action"),
                "projection_path": item.get("projection_path"),
            }
            for item in payload.get("projections", [])
            if isinstance(item, dict)
        ],
    }


def _run_id_for_call(call: dict[str, Any], started_at: str) -> str:
    suffix = _stable_hash({"call_id": call.get("id"), "started_at": started_at})
    return f"run-{suffix}"


def _call_run_blockers(target: Path, call: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    status = str(call.get("status") or "")
    if status != "approved":
        if status == "completed":
            blockers.append("completed calls cannot be run again")
        elif status == "failed":
            blockers.append("failed calls are not approved for another run")
        elif status == "running":
            blockers.append("call is already running")
        else:
            blockers.append(f"call must be approved before run: {status or 'unknown'}")
    if call.get("blockers"):
        blockers.append("blocked calls cannot be run")
    if call.get("family") != "script":
        blockers.append("only script family calls can be run")
    if not isinstance(call.get("command"), str) or not str(call.get("command")).strip():
        blockers.append("command is required")
    if _high_risk_command(call.get("command")):
        blockers.append("command shape is high risk")
    approval_fingerprint = call.get("approval_fingerprint")
    if not approval_fingerprint:
        blockers.append("approval fingerprint is missing")
    elif approval_fingerprint != _approval_fingerprint(call):
        blockers.append("approval fingerprint is stale")
    if call.get("call_fingerprint") != _stored_call_fingerprint(call):
        blockers.append("stored args or call metadata fingerprint is stale")
    current_contract, current_source = _call_current_fingerprints(target, call)
    if current_contract != call.get("contract_fingerprint"):
        blockers.append("contract fingerprint is stale")
    if current_source != call.get("source_fingerprint"):
        blockers.append("source fingerprint is stale")
    tool_id = str(call.get("tool_id") or "")
    if not tool_id:
        blockers.append("tool_id is missing")
    else:
        tool, errors = _find_tool(target, tool_id)
        if tool is None:
            blockers.extend(errors or [f"tool not found: {tool_id}"])
        else:
            if tool.get("family") != "script":
                blockers.append("configured tool is not script family")
            current_projection = _call_projection_summary(target, tool_id)
            if current_projection != call.get("projection_summary", {}):
                blockers.append("projection summary is stale")
            cwd_value = call.get("contract", {}).get("cwd") if isinstance(call.get("contract"), dict) else None
            cwd_path = _as_path(target, cwd_value) if cwd_value else target
            if cwd_path is None or not cwd_path.is_dir():
                blockers.append(f"cwd does not exist: {cwd_path}")
    if not _command_parts(call.get("command")):
        blockers.append("command could not be parsed")
    return blockers


def _write_run_receipt(
    target: Path,
    *,
    call: dict[str, Any],
    run_id: str,
    started_at: str,
    completed_at: str,
    duration_seconds: float,
    status: str,
    exit_code: int | None,
    timed_out: bool,
    stdout: object,
    stderr: object,
    argv: list[str],
    cwd: Path,
) -> dict[str, Any]:
    run_dir = runs_path(target)
    run_dir.mkdir(parents=True, exist_ok=True)
    stdout_text = "" if stdout is None else str(stdout)
    stderr_text = "" if stderr is None else str(stderr)
    stdout_path = run_dir / f"{run_id}.stdout.log"
    stderr_path = run_dir / f"{run_id}.stderr.log"
    receipt_path = run_dir / f"{run_id}.json"
    stdout_path.write_text(stdout_text)
    stderr_path.write_text(stderr_text)
    contract = call.get("contract") if isinstance(call.get("contract"), dict) else {}
    receipt = {
        "id": run_id,
        "call_id": call.get("id"),
        "tool_id": call.get("tool_id"),
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": round(duration_seconds, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "command_label": call.get("command"),
        "argv": [_redact_text(part, 160) for part in argv],
        "cwd": str(cwd),
        "stdout_summary": _redact_text(stdout_text),
        "stderr_summary": _redact_text(stderr_text),
        "stdout_log_path": str(stdout_path),
        "stderr_log_path": str(stderr_path),
        "receipt_path": str(receipt_path),
        "contract_fingerprint": call.get("contract_fingerprint"),
        "source_fingerprint": call.get("source_fingerprint"),
        "call_fingerprint": call.get("call_fingerprint"),
        "approval_fingerprint": call.get("approval_fingerprint"),
        "approval": {
            "reviewed_at": call.get("reviewed_at"),
            "review_reason": call.get("review_reason"),
        },
        "permissions": contract.get("permissions", []),
        "effects": contract.get("effects", []),
        "projection_summary": call.get("projection_summary", {}),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt


def _next_approved_call(calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    approved = [call for call in calls if call.get("status") == "approved"]
    approved.sort(key=lambda call: str(call.get("created_at") or ""))
    return approved[0] if approved else None


def _run_call_payload(target: Path, *, call_id: str | None = None, next_call: bool = False) -> tuple[dict[str, Any], int]:
    target = target.expanduser().resolve()
    calls = _read_calls(target)
    call: dict[str, Any] | None
    error: str | None = None
    if next_call:
        call = _next_approved_call(calls)
        if call is None:
            error = "no approved calls available"
    elif call_id:
        call, calls, error = _resolve_call(target, call_id)
    else:
        call = None
        error = "pass a call id or --next"
    if call is None:
        return {"target": str(target), "calls_path": str(calls_path(target)), "error": error}, 1
    blockers = _call_run_blockers(target, call)
    if blockers:
        return {
            "target": str(target),
            "calls_path": str(calls_path(target)),
            "call": call,
            "blockers": blockers,
            "error": "call is not runnable",
        }, 1
    contract = call.get("contract") if isinstance(call.get("contract"), dict) else {}
    cwd_value = contract.get("cwd")
    cwd = _as_path(target, cwd_value) if cwd_value else target
    assert cwd is not None
    argv = _command_parts(call.get("command"))
    for key in sorted((call.get("arguments") if isinstance(call.get("arguments"), dict) else {}).keys()):
        value = call["arguments"][key]
        if value is None:
            continue
        argv.extend(shlex.split(str(value)))
    started_at = _now().isoformat()
    run_id = _run_id_for_call(call, started_at)
    receipt_path = runs_path(target) / f"{run_id}.json"
    call["status"] = "running"
    call["started_at"] = started_at
    call["completed_at"] = None
    call["run_id"] = run_id
    call["receipt_path"] = str(receipt_path)
    call["exit_code"] = None
    _write_calls(target, calls)

    timeout = contract.get("timeout")
    timeout_value = float(timeout) if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) else None
    start_monotonic = time.monotonic()
    stdout: object = ""
    stderr: object = ""
    exit_code: int | None = None
    timed_out = False
    status = "completed"
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=timeout_value,
            check=False,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        exit_code = completed.returncode
        if completed.returncode != 0:
            status = "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        timed_out = True
        status = "failed"
    except OSError as exc:
        stderr = str(exc)
        status = "failed"
    duration_seconds = time.monotonic() - start_monotonic
    completed_at = _now().isoformat()
    receipt = _write_run_receipt(
        target,
        call=call,
        run_id=run_id,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        argv=argv,
        cwd=cwd,
    )
    call["status"] = status
    call["completed_at"] = completed_at
    call["exit_code"] = exit_code
    call["timed_out"] = timed_out
    call["receipt_path"] = receipt["receipt_path"]
    _write_calls(target, calls)
    return {
        "target": str(target),
        "calls_path": str(calls_path(target)),
        "runs_path": str(runs_path(target)),
        "call": call,
        "receipt": receipt,
    }, 0 if status == "completed" else 1


def _call_health(target: Path) -> dict[str, Any]:
    calls = _read_calls(target)
    now = _now()
    issues: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for call in calls:
        status = str(call.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        created = _parse_iso_datetime(call.get("created_at"))
        if status == "pending" and created is not None:
            age_hours = (now - created).total_seconds() / 3600
            if age_hours > CALL_STALE_HOURS:
                issues.append(
                    {
                        "status": WARN,
                        "name": "tool_call_stale_pending",
                        "issue_type": "call_stale_pending",
                        "tool_id": call.get("tool_id"),
                        "call_id": call.get("id"),
                        "detail": f"{call.get('id')} pending for {age_hours:.1f}h",
                    }
                )
        if status == "pending" and call.get("blockers"):
            issues.append(
                {
                    "status": WARN,
                    "name": "tool_call_blocked",
                    "issue_type": "call_blocked",
                    "tool_id": call.get("tool_id"),
                    "call_id": call.get("id"),
                    "detail": f"{call.get('id')} has {len(call.get('blockers', []))} blocker(s)",
                }
            )
        if status == "approved":
            current_contract, current_source = _call_current_fingerprints(target, call)
            if current_contract != call.get("contract_fingerprint") or current_source != call.get("source_fingerprint"):
                issues.append(
                    {
                        "status": WARN,
                        "name": "tool_call_stale_approved",
                        "issue_type": "call_stale_approved",
                        "tool_id": call.get("tool_id"),
                        "call_id": call.get("id"),
                        "detail": f"{call.get('id')} approved with stale contract or source fingerprint",
                    }
                )
        if status == "running":
            started = _parse_iso_datetime(call.get("started_at"))
            if started is not None:
                age_hours = (now - started).total_seconds() / 3600
                if age_hours > CALL_RUNNING_STALE_HOURS:
                    issues.append(
                        {
                            "status": WARN,
                            "name": "tool_call_running_stale",
                            "issue_type": "call_running_stale",
                            "tool_id": call.get("tool_id"),
                            "call_id": call.get("id"),
                            "detail": f"{call.get('id')} running for {age_hours:.1f}h",
                        }
                    )
        if status == "failed":
            issues.append(
                {
                    "status": WARN,
                    "name": "tool_call_failed",
                    "issue_type": "call_failed",
                    "tool_id": call.get("tool_id"),
                    "call_id": call.get("id"),
                    "detail": f"{call.get('id')} failed with exit_code={call.get('exit_code')}",
                }
            )
        if status in {"held", "rejected"}:
            issues.append(
                {
                    "status": WARN,
                    "name": f"tool_call_{status}",
                    "issue_type": f"call_{status}",
                    "tool_id": call.get("tool_id"),
                    "call_id": call.get("id"),
                    "detail": f"{call.get('id')} is {status}: {call.get('review_reason') or ''}".strip(),
                }
            )
    return {
        "calls_path": str(calls_path(target)),
        "calls": calls,
        "counts": counts,
        "pending_count": counts.get("pending", 0),
        "issue_count": len(issues),
        "issues": issues,
        "top_issue": issues[0] if issues else None,
    }


def _catalog_payload(target: Path) -> dict[str, Any]:
    target = target.expanduser().resolve()
    tools, errors = _load_config(target)
    summaries: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for tool in tools:
        if not tool.get("enabled", True):
            continue
        summary, tool_issues = _inspect_tool(target, tool, now=now)
        summaries.append(summary)
        issues.extend(tool_issues)
    call_health = _call_health(target)
    issues.extend(call_health["issues"])
    if errors:
        issues.insert(0, {"status": WARN, "name": "tool_config", "issue_type": "config", "detail": "; ".join(errors)})
    return {
        "target": str(target),
        "config_path": str(config_path(target)),
        "valid": not errors,
        "errors": errors,
        "tools": summaries,
        "tool_count": len(summaries),
        "issues": issues,
        "issue_count": len(issues),
        "top_issue": issues[0] if issues else None,
        "call_queue": {
            "calls_path": call_health["calls_path"],
            "counts": call_health["counts"],
            "pending_count": call_health["pending_count"],
            "issue_count": call_health["issue_count"],
            "top_issue": call_health["top_issue"],
        },
    }


def health(target: Path) -> dict[str, Any]:
    payload = _catalog_payload(target)
    return {
        "config_path": payload["config_path"],
        "valid": payload["valid"],
        "tool_count": payload["tool_count"],
        "issue_count": payload["issue_count"],
        "top_issue": payload["top_issue"],
        "issues": payload["issues"],
        "call_queue": payload["call_queue"],
    }


def _issue_records(target: Path) -> list[dict[str, Any]]:
    payload = _catalog_payload(target)
    records: list[dict[str, Any]] = []
    for issue in payload["issues"]:
        issue_type = str(issue.get("issue_type") or issue.get("name") or "tool_issue")
        tool_id = str(issue.get("tool_id") or "catalog")
        detail = str(issue.get("detail") or "")
        metadata = {
            "tool_id": tool_id,
            "tool_family": issue.get("family"),
            "tool_issue_type": issue_type,
            "tool_harness": issue.get("harness"),
            "tool_call_id": issue.get("call_id"),
            "projection_target": issue.get("projection_target"),
            "tool_issue_detail": detail,
            "source_item_key": f"tool-catalog:{tool_id}:{issue_type}:{issue.get('harness') or ''}:{issue.get('call_id') or ''}",
            "source_fingerprint": _stable_hash(
                {
                    "tool_id": tool_id,
                    "issue_type": issue_type,
                    "detail": detail,
                    "harness": issue.get("harness"),
                    "call_id": issue.get("call_id"),
                    "projection_target": issue.get("projection_target"),
                }
            ),
        }
        records.append(
            {
                "text": f"Repair tool catalog issue {tool_id}/{issue_type}: {detail}",
                "kind": "task",
                "source": "tool-catalog",
                "type": "workflow",
                "priority": "normal",
                "template": "bugfix",
                "acceptance": [f"`brigade tools doctor` no longer reports {tool_id}/{issue_type}."],
                "metadata": metadata,
            }
        )
    return records


def init(*, target: Path, force: bool = False, update_gitignore: bool = True) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    path = config_path(target)
    if path.exists() and not force:
        print(f"error: tool catalog config already exists: {path}", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_format_tools_toml())
    print(f"tools_config: {path}")
    print(f"tools: {len(DEFAULT_TOOLS)}")
    if update_gitignore:
        result = apply_gitignore(target, Selection("repo", ["codex"], "codex"))
        print(f"gitignore: {result}")
    else:
        print("gitignore: skipped")
    print("next_command: brigade tools list")
    return 0


def list_tools(*, target: Path, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _catalog_payload(target)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] else 1
    print(f"tools: {target}")
    print(f"config_path: {payload['config_path']}")
    if payload["errors"]:
        for error in payload["errors"]:
            print(f"error: {error}")
        return 1
    if not payload["tools"]:
        print("tools: none")
        return 0
    for tool in payload["tools"]:
        print(
            f"- {tool.get('id')} [{tool.get('family')}] "
            f"harnesses={','.join(tool.get('supported_harnesses', []))} "
            f"tools={tool.get('tool_count')}"
        )
        if tool.get("description"):
            print(f"  {_short(str(tool['description']))}")
    return 0


def show(*, target: Path, tool_id: str, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _catalog_payload(target)
    tool = None
    for item in payload["tools"]:
        if item.get("id") == tool_id:
            tool = item
            break
    if json_output:
        print(json.dumps({"target": str(target), "config_path": payload["config_path"], "tool": tool}, indent=2, sort_keys=True))
        return 0 if tool is not None else 1
    if tool is None:
        print(f"error: tool not found: {tool_id}", file=sys.stderr)
        return 1
    print(f"tool: {tool.get('id')}")
    print(f"name: {tool.get('name')}")
    print(f"family: {tool.get('family')}")
    print(f"description: {tool.get('description')}")
    print(f"supported_harnesses: {', '.join(tool.get('supported_harnesses', []))}")
    print(f"tool_count: {tool.get('tool_count')}")
    print(f"schema_available: {tool.get('schema_available')}")
    print(f"auth_label: {tool.get('auth_label') or ''}")
    print("projections:")
    for harness, status in sorted(tool.get("projection_coverage", {}).items()):
        print(f"  {harness}: {status}")
    if tool.get("mcp"):
        print(f"mcp_servers: {tool['mcp'].get('server_count')}")
    return 0


def search(*, target: Path, query: str, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    needle = query.casefold().strip()
    payload = _catalog_payload(target)
    matches = [
        tool
        for tool in payload["tools"]
        if needle
        and needle
        in " ".join(str(tool.get(key, "")) for key in ("id", "name", "family", "description")).casefold()
    ]
    result = {"target": str(target), "query": query, "matches": matches, "match_count": len(matches)}
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"tool search: {query}")
    print(f"matches: {len(matches)}")
    for tool in matches:
        print(f"- {tool.get('id')} [{tool.get('family')}] {_short(str(tool.get('description', '')))}")
    return 0


def describe(*, target: Path, tool_id: str, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _describe_payload(target, tool_id)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] and payload["tool"] is not None else 1
    if payload["errors"]:
        for error in payload["errors"]:
            print(f"error: {error}", file=sys.stderr)
        return 1
    tool = payload["tool"]
    assert isinstance(tool, dict)
    contract = tool.get("contract") if isinstance(tool.get("contract"), dict) else {}
    print(f"tool: {tool.get('id')}")
    print(f"name: {tool.get('name')}")
    print(f"family: {tool.get('family')}")
    print(f"description: {tool.get('description')}")
    print(f"command: {contract.get('command') or ''}")
    print(f"approval_mode: {contract.get('approval_mode')}")
    print(f"input_schema: {contract.get('input_schema_path') or ''}")
    print(f"output_schema: {contract.get('output_schema_path') or ''}")
    print(f"permissions: {', '.join(contract.get('permissions', []))}")
    print(f"effects: {', '.join(contract.get('effects', []))}")
    print(f"contract_issues: {payload['issue_count']}")
    return 0


def contracts(*, target: Path, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _contracts_payload(target)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] else 1
    print(f"tools contracts: {target}")
    print(f"config_path: {payload['config_path']}")
    if payload["errors"]:
        for error in payload["errors"]:
            print(f"error: {error}")
        return 1
    print(f"contracts: {payload['contract_count']}")
    print(f"contract_issues: {payload['issue_count']}")
    for contract in payload["contracts"]:
        status = "ready" if contract.get("has_contract") and contract.get("issue_count") == 0 else "needs-review"
        print(f"- {contract.get('tool_id')} [{contract.get('family')}] {status} issues={contract.get('issue_count')}")
        print(f"  input_schema: {contract.get('input_schema_path') or ''}")
        print(f"  approval_mode: {contract.get('approval_mode')}")
    return 0


def call_plan(
    *,
    target: Path,
    tool_id: str,
    args: str | None = None,
    args_json: Path | None = None,
    json_output: bool = False,
) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _call_plan_payload(target, tool_id, args=args, args_json=args_json)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] else 1
    print(f"tools call plan: {tool_id}")
    print(f"target: {target}")
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    if blockers:
        print(f"blockers: {len(blockers)}")
        for blocker in blockers:
            print(f"- {blocker}")
        return 1
    plan_payload = payload["plan"]
    print(f"command: {plan_payload.get('command')}")
    print(f"approval_mode: {plan_payload.get('approval_mode')}")
    print(f"approval_required: {plan_payload.get('approval_required')}")
    print(f"permissions: {', '.join(plan_payload.get('permissions', []))}")
    print(f"effects: {', '.join(plan_payload.get('effects', []))}")
    print("arguments:")
    for key, value in plan_payload.get("arguments", {}).items():
        print(f"  {key}: {value}")
    return 0


def call_queue(
    *,
    target: Path,
    tool_id: str,
    args: str | None = None,
    args_json: Path | None = None,
    include_blocked: bool = False,
    json_output: bool = False,
) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload, rc = _queue_call_payload(
        target,
        tool_id,
        args=args,
        args_json=args_json,
        include_blocked=include_blocked,
    )
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return rc
    print(f"tools call queue: {tool_id}")
    print(f"calls_path: {payload['calls_path']}")
    print(f"created: {payload['created']}")
    print(f"skipped: {payload['skipped']}")
    print(f"blocked: {payload['blocked']}")
    if payload.get("reason"):
        print(f"reason: {payload['reason']}")
    call = payload.get("call") if isinstance(payload.get("call"), dict) else {}
    if call:
        print(f"call: {call.get('id')}")
        print(f"status: {call.get('status')}")
        print(f"blockers: {len(call.get('blockers', [])) if isinstance(call.get('blockers'), list) else 0}")
    return rc


def call_list(*, target: Path, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    calls = _read_calls(target)
    counts: dict[str, int] = {}
    for call in calls:
        status = str(call.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    payload = {"target": str(target), "calls_path": str(calls_path(target)), "calls": calls, "counts": counts}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"tools call list: {target}")
    print(f"calls_path: {calls_path(target)}")
    print(f"calls: {len(calls)}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    for call in calls:
        print(f"- {call.get('id')} [{call.get('status')}] {call.get('tool_id')} blockers={len(call.get('blockers', []))}")
    return 0


def call_show(*, target: Path, call_id: str, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    call, _, error = _resolve_call(target, call_id)
    payload = {"target": str(target), "calls_path": str(calls_path(target)), "call": call, "error": error}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if call is not None else 1
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    assert call is not None
    print(f"call: {call.get('id')}")
    print(f"tool_id: {call.get('tool_id')}")
    print(f"status: {call.get('status')}")
    print(f"created_at: {call.get('created_at')}")
    if call.get("reviewed_at"):
        print(f"reviewed_at: {call.get('reviewed_at')}")
    if call.get("review_reason"):
        print(f"review_reason: {call.get('review_reason')}")
    print(f"blockers: {len(call.get('blockers', []))}")
    return 0


def _call_review(
    *,
    target: Path,
    call_id: str,
    status: str,
    reason: str | None = None,
    json_output: bool = False,
) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    call, calls, error = _resolve_call(target, call_id)
    if call is None:
        payload = {"target": str(target), "error": error}
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if status == "approved" and call.get("blockers"):
        payload = {"target": str(target), "error": "blocked calls cannot be approved", "call": call}
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("error: blocked calls cannot be approved", file=sys.stderr)
        return 1
    call["status"] = status
    call["reviewed_at"] = _now().isoformat()
    call["review_reason"] = reason
    call["approval_fingerprint"] = _approval_fingerprint(call) if status == "approved" else None
    _write_calls(target, calls)
    payload = {"target": str(target), "calls_path": str(calls_path(target)), "call": call}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"call: {call.get('id')}")
    print(f"status: {call.get('status')}")
    if reason:
        print(f"review_reason: {reason}")
    return 0


def call_approve(*, target: Path, call_id: str, json_output: bool = False) -> int:
    return _call_review(target=target, call_id=call_id, status="approved", json_output=json_output)


def call_reject(*, target: Path, call_id: str, reason: str, json_output: bool = False) -> int:
    return _call_review(target=target, call_id=call_id, status="rejected", reason=reason, json_output=json_output)


def call_hold(*, target: Path, call_id: str, reason: str, json_output: bool = False) -> int:
    return _call_review(target=target, call_id=call_id, status="held", reason=reason, json_output=json_output)


def call_run(*, target: Path, call_id: str | None = None, next_call: bool = False, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    if bool(call_id) == bool(next_call):
        print("error: pass exactly one call id or --next", file=sys.stderr)
        return 2
    payload, rc = _run_call_payload(target, call_id=call_id, next_call=next_call)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return rc
    if payload.get("error"):
        print(f"error: {payload['error']}", file=sys.stderr)
        for blocker in payload.get("blockers", []):
            print(f"- {blocker}", file=sys.stderr)
        return rc
    call = payload["call"]
    receipt = payload["receipt"]
    print(f"tools call run: {call.get('id')}")
    print(f"status: {call.get('status')}")
    print(f"exit_code: {call.get('exit_code')}")
    print(f"receipt_path: {receipt.get('receipt_path')}")
    print(f"stdout_summary: {receipt.get('stdout_summary')}")
    print(f"stderr_summary: {receipt.get('stderr_summary')}")
    return rc


def plan(*, target: Path, tool_id: str | None = None, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _projection_plan_payload(target, tool_id=tool_id)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] else 1
    print(f"tools projection plan: {target}")
    print(f"config_path: {payload['config_path']}")
    if tool_id is not None:
        print(f"tool_id: {tool_id}")
    if payload["errors"]:
        for error in payload["errors"]:
            print(f"error: {error}")
        return 1
    projections = payload["projections"]
    print(f"projections: {len(projections)}")
    if payload["counts"]:
        print("counts:")
        for status, count in sorted(payload["counts"].items()):
            print(f"  {status}: {count}")
    for item in projections:
        print(
            "- "
            f"{item.get('tool_id')} {item.get('harness')} "
            f"{item.get('status')} action={item.get('action')}"
        )
        print(f"  source: {item.get('source_path')}")
        print(f"  target: {item.get('projection_path')}")
        if item.get("expected_fingerprint"):
            print(f"  expected_fingerprint: {item.get('expected_fingerprint')}")
        print(f"  detail: {item.get('detail')}")
    return 0


def apply(
    *,
    target: Path,
    tool_id: str | None = None,
    all_tools: bool = False,
    dry_run: bool = False,
    force: bool = False,
    json_output: bool = False,
) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    if bool(tool_id) == bool(all_tools):
        print("error: pass exactly one of <tool-id> or --all", file=sys.stderr)
        return 2
    tools, errors = _load_config(target)
    selected = [
        tool
        for tool in tools
        if tool.get("enabled", True) and (all_tools or tool.get("id") == tool_id)
    ]
    if tool_id is not None and not selected and not errors:
        errors.append(f"tool not found: {tool_id}")
    generated_at = datetime.now(timezone.utc)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for tool in selected:
        for harness in tool.get("supported_harnesses", []):
            item = _projection_item(target, tool, harness, generated_at=generated_at, force=force)
            public_item = {key: value for key, value in item.items() if key != "rendered"}
            action = item.get("action")
            if action == "conflict":
                conflicts.append(public_item)
                continue
            if action not in {"create", "update"}:
                skipped.append(public_item)
                continue
            if dry_run:
                applied.append({**public_item, "dry_run": True})
                continue
            projection_path = Path(str(item["projection_path"]))
            projection_path.parent.mkdir(parents=True, exist_ok=True)
            projection_path.write_text(str(item["rendered"]))
            applied.append(public_item)
    payload = {
        "target": str(target),
        "config_path": str(config_path(target)),
        "valid": not errors,
        "errors": errors,
        "tool_id": tool_id,
        "all": all_tools,
        "dry_run": dry_run,
        "force": force,
        "applied": applied,
        "skipped": skipped,
        "conflicts": conflicts,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "conflict_count": len(conflicts),
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if not errors and not conflicts else 1
    print(f"tools projection apply: {target}")
    print(f"config_path: {config_path(target)}")
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print(f"dry_run: {dry_run}")
    print(f"force: {force}")
    print(f"applied: {len(applied)}")
    print(f"skipped: {len(skipped)}")
    print(f"conflicts: {len(conflicts)}")
    for item in applied:
        verb = "would_write" if dry_run else "wrote"
        print(f"- {verb}: {item.get('tool_id')} {item.get('harness')} {item.get('projection_path')}")
    for item in conflicts:
        print(f"- conflict: {item.get('tool_id')} {item.get('harness')} {item.get('detail')}")
    return 0 if not conflicts else 1


def doctor(*, target: Path, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    payload = _catalog_payload(target)
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["valid"] else 1
    print(f"tools doctor: {target}")
    print(f"config_path: {payload['config_path']}")
    if payload["errors"]:
        for error in payload["errors"]:
            print(f"[warn] tool_config: {error}")
    else:
        print(f"[ok] tool_config: {payload['config_path']}")
    if payload["issues"]:
        for issue in payload["issues"]:
            print(f"[{issue.get('status', WARN)}] {issue.get('name')}: {issue.get('detail')}")
    else:
        print("[ok] tool_catalog: no issues")
    print(f"tool_issues: {payload['issue_count']}")
    return 0 if payload["valid"] else 1


def import_issues(*, target: Path, json_output: bool = False) -> int:
    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2
    records = _issue_records(target)
    from . import work_cmd

    imported, skipped, skipped_dismissed = work_cmd._append_import_records(target, records)
    payload = {
        "target": str(target),
        "imports_path": str(work_cmd._imports_path(target)),
        "issues": len(records),
        "created": len(imported),
        "skipped": len(skipped),
        "dismissed": len(skipped_dismissed),
        "imports": imported,
    }
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"tool issue imports: {target}")
    print(f"imports_path: {payload['imports_path']}")
    print(f"issues: {len(records)}")
    print(f"created: {len(imported)}")
    print(f"skipped: {len(skipped)}")
    print(f"dismissed: {len(skipped_dismissed)}")
    for item in imported:
        print(f"- {item.get('id')} [{item.get('kind')}] {_short(str(item.get('text', '')))}")
    return 0
