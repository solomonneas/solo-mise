"""Tests for solo-mise openclaw-fragments / hermes-fragments."""
from __future__ import annotations

import json
from pathlib import Path

from solo_mise import fragments as frag_mod
from solo_mise.templates import load_depth_manifest, template_root


def test_openclaw_fragments(tmp_path: Path):
    out = tmp_path / "openclaw-fragments"
    rc = frag_mod.write_fragments(out, harness="openclaw")
    assert rc == 0
    for name in (
        "model-aliases.openclaw.json",
        "ollama-memory-search.openclaw.json",
        "acp-escalation.openclaw.json",
        "README.md",
    ):
        assert (out / name).is_file()
    # JSON fragments parse
    data = json.loads((out / "model-aliases.openclaw.json").read_text())
    assert "agents" in data


def test_hermes_fragments(tmp_path: Path):
    out = tmp_path / "hermes-fragments"
    rc = frag_mod.write_fragments(out, harness="hermes")
    assert rc == 0
    for name in (
        "workspace.harness.json",
        "memory-handoff.harness.json",
        "model-lanes.harness.json",
        "README.md",
    ):
        assert (out / name).is_file()
    data = json.loads((out / "workspace.harness.json").read_text())
    assert data.get("_solo_mise_status") == "experimental"


def test_unknown_harness_errors(tmp_path: Path):
    rc = frag_mod.write_fragments(tmp_path, harness="bogus")
    assert rc == 2


def test_load_depth_repo():
    m = load_depth_manifest("repo")
    assert m["id"] == "repo"
    dsts = [f["dst"] for f in m["files"]]
    assert "AGENTS.md" in dsts
    assert "SAFETY_RULES.md" in dsts
    assert "INSTALL_FOR_AGENTS.md" in dsts
    assert "hooks/pre-push" in dsts
    assert ".solo-mise/policies/public-repo.json" in dsts
    # depth baseline does NOT install harness-specific bridge files
    assert "CLAUDE.md" not in dsts


def test_load_depth_workspace():
    m = load_depth_manifest("workspace")
    assert m["id"] == "workspace"
    dsts = [f["dst"] for f in m["files"]]
    assert "AGENTS.md" in dsts
    assert "MEMORY.md" in dsts
    assert "TOOLS.md" in dsts
    assert "USER.md" in dsts
    assert "SOUL.md" in dsts
    assert "IDENTITY.md" in dsts
    assert "HEARTBEAT.md" in dsts
    assert "CLAUDE.md" not in dsts


def test_depth_repo_files_exist():
    m = load_depth_manifest("repo")
    root = template_root()
    for entry in m["files"]:
        assert (root / entry["src"]).is_file(), entry["src"]
