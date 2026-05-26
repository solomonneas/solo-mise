import json

from brigade import cli
from brigade import runs_cmd


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _write_run_artifacts(run_dir):
    run_dir.mkdir()
    _write_json(
        run_dir / "run.json",
        {
            "task": "build feature",
            "cwd": "/repo",
            "orchestrator": "chef",
            "dry_run": False,
            "read_only": True,
            "status": "ok",
            "started_at": "2026-05-26T14:00:00Z",
            "finished_at": "2026-05-26T14:00:02Z",
            "duration_seconds": 2.0,
            "artifacts": str(run_dir),
            "handoff": str(run_dir / "handoff.md"),
        },
    )
    _write_json(
        run_dir / "roster.json",
        {
            "orchestrator": "chef",
            "max_workers": 1,
            "timeout_seconds": 180.0,
            "allow_models": ["codex"],
            "agents": {
                "chef": {"cli": "codex", "role": "plan", "timeout_seconds": 180.0},
                "coder": {"cli": "codex", "role": "code", "timeout_seconds": None},
            },
        },
    )
    _write_json(
        run_dir / "plan.json",
        {"assignments": [{"worker": "coder", "task": "implement it"}]},
    )
    _write_json(
        run_dir / "worker-results.json",
        {"results": [{"worker": "coder", "task": "implement it", "ok": True, "detail": "", "text": "done"}]},
    )
    _write_json(
        run_dir / "synthesis.json",
        {"orchestrator": "chef", "result": {"ok": True, "detail": "", "text": "final answer"}},
    )
    (run_dir / "final.txt").write_text("final answer\n")


def test_runs_show_prints_summary(tmp_path, capsys):
    run_dir = tmp_path / "run"
    _write_run_artifacts(run_dir)

    assert runs_cmd.show(run_dir) == 0
    out = capsys.readouterr().out
    assert f"run: {run_dir}" in out
    assert "status: ok" in out
    assert "mode: read-only" in out
    assert "duration: 2s" in out
    assert "handoff:" in out
    assert "roster:" in out
    assert "  - chef: codex (orchestrator); timeout=180s" in out
    assert "plan:" in out
    assert "  -> coder: implement it" in out
    assert "workers:" in out
    assert "  [ok] coder" in out
    assert "synthesis:" in out
    assert "  [ok] chef" in out
    assert "final:" in out
    assert "  final answer" in out


def test_runs_show_reports_missing_run_json(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    assert runs_cmd.show(run_dir) == 2
    assert "run.json not found" in capsys.readouterr().err


def test_runs_show_reports_invalid_json(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text("not json")

    assert runs_cmd.show(run_dir) == 2
    assert "run.json is not valid JSON" in capsys.readouterr().err


def test_runs_show_cli(tmp_path, capsys):
    run_dir = tmp_path / "run"
    _write_run_artifacts(run_dir)

    assert cli.main(["runs", "show", str(run_dir)]) == 0
    assert "status: ok" in capsys.readouterr().out
