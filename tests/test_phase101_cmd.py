import json
from pathlib import Path

from brigade import center_cmd, cli, daily_cmd, handoff_cmd, work_cmd


def _write_command_inventory(path, capsys=None):
    (path / "README.md").write_text("Use `brigade roadmap commands`.\n")
    (path / "ROADMAP.md").write_text("# Roadmap\n")
    assert cli.main(["roadmap", "commands", "--target", str(path), "--write", "--json"]) == 0
    if capsys is not None:
        capsys.readouterr()


def _write_release_receipt(path, *, ready=True):
    run_dir = path / ".brigade" / "release" / "runs" / "release-ready"
    run_dir.mkdir(parents=True)
    (run_dir / "receipt.json").write_text(
        json.dumps(
            {
                "run_id": "release-ready",
                "status": "ready" if ready else "blocked",
                "ready": ready,
                "started_at": "2026-05-30T00:00:00+00:00",
                "completed_at": "2026-05-30T00:01:00+00:00",
                "blockers": [] if ready else ["blocked"],
                "warnings": [],
                "checks": [],
            }
        )
    )


def _seed_ready_repo(path, capsys=None):
    _write_command_inventory(path, capsys)
    _write_release_receipt(path)


def test_daily_status_text_and_json(tmp_path, capsys):
    _seed_ready_repo(tmp_path, capsys)
    work_cmd._add_task(
        tmp_path,
        "Implement daily loop",
        priority="high",
        acceptance=["Daily plan chooses one action."],
    )

    assert daily_cmd.status(target=tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pending_task_count"] == 1
    assert payload["selected_action"]["source_subsystem"] == "work-task"
    assert payload["next_recommended_command"] == "brigade work run"

    assert cli.main(["daily", "status", "--target", str(tmp_path)]) == 0
    assert "daily status:" in capsys.readouterr().out


def test_daily_plan_ranks_tasks_imports_center_actions_and_readiness(tmp_path, capsys):
    _seed_ready_repo(tmp_path, capsys)
    task, _ = work_cmd._add_task(
        tmp_path,
        "Top accepted task",
        priority="normal",
        acceptance=["Task acceptance exists."],
    )
    work_cmd._append_import_records(
        tmp_path,
        [
            {
                "text": "High import",
                "kind": "task",
                "source": "scanner",
                "priority": "high",
                "acceptance": ["Import acceptance exists."],
                "metadata": {"source_fingerprint": "import-fp"},
            }
        ],
    )
    center_cmd._write_actions(
        tmp_path,
        [
            {
                "action_id": "action-one",
                "status": "pending",
                "safe_summary": "Reviewed action",
                "source_fingerprint": "action-fp",
                "created_at": "2026-05-30T00:00:00+00:00",
                "updated_at": "2026-05-30T00:00:00+00:00",
            }
        ],
    )

    assert daily_cmd.plan(target=tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_action"]["metadata"]["task_id"] == task["id"]
    subsystems = {item["source_subsystem"] for item in payload["candidate_actions"]}
    assert {"work-task", "work-import", "center-action"} <= subsystems

    blocked = tmp_path / "blocked"
    blocked.mkdir()
    _write_command_inventory(blocked, capsys)
    assert daily_cmd.plan(target=blocked, json_output=True) == 0
    blocked_payload = json.loads(capsys.readouterr().out)
    assert blocked_payload["selected_action"]["source_subsystem"] == "center-readiness"


def test_daily_plan_records_and_review_previews_action(tmp_path, capsys):
    _seed_ready_repo(tmp_path, capsys)
    task, _ = work_cmd._add_task(
        tmp_path,
        "Context backed task",
        acceptance=["Context exists."],
    )

    assert cli.main(["daily", "plan", "--target", str(tmp_path), "--record", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["recorded"] is True
    assert (tmp_path / ".brigade" / "daily" / "plans" / payload["plan_id"] / "plan.json").is_file()

    assert daily_cmd.review(target=tmp_path, json_output=True) == 0
    review = json.loads(capsys.readouterr().out)
    assert review["source_subsystem"] == "work-task"
    assert review["acceptance"] == ["Context exists."]
    assert review["context_pack_plan"]["task"]["id"] == task["id"]
    assert review["approval_boundary"] == "no explicit approval required"


def test_daily_plan_includes_handoff_memory_and_security_issues(tmp_path, capsys):
    _seed_ready_repo(tmp_path, capsys)
    inbox = tmp_path / ".claude" / "memory-handoffs"
    inbox.mkdir(parents=True)
    (inbox / "broken.md").write_text("# not a valid handoff\n")

    assert daily_cmd.plan(target=tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    subsystems = {item["source_subsystem"] for item in payload["candidate_actions"]}
    assert {"handoff", "memory-care", "security"} <= subsystems


def test_daily_run_refuses_approval_required_import_and_promotes_when_approved(tmp_path, capsys):
    _seed_ready_repo(tmp_path, capsys)
    imported, _, _ = work_cmd._append_import_records(
        tmp_path,
        [
            {
                "text": "Promote me",
                "kind": "task",
                "source": "scanner",
                "priority": "high",
                "acceptance": ["Task is promoted."],
                "metadata": {"source_fingerprint": "promote-fp"},
            }
        ],
    )
    import_id = imported[0]["id"]

    assert daily_cmd.run(target=tmp_path, json_output=True) == 1
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["status"] == "blocked"
    assert "approval" in blocked["blockers"][0] or "local task ledger" in blocked["blockers"][0]

    assert cli.main(["daily", "run", "--target", str(tmp_path), "--approved", "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "completed"
    assert receipt["selected_action"]["metadata"]["import_id"] == import_id
    assert not any("push" in command["command"] or "tag" in command["command"] for command in receipt["commands_invoked"])
    assert work_cmd._pending_tasks(tmp_path)


def test_daily_selection_skips_remote_mutation_commands():
    selected = daily_cmd._selected(
        [
            {
                "action_id": "remote",
                "score": 999,
                "suggested_next_command": "git push origin main",
            },
            {
                "action_id": "local",
                "score": 100,
                "suggested_next_command": "brigade work run",
            },
        ]
    )
    assert selected["action_id"] == "local"


def test_daily_run_handles_one_task_and_builds_context(tmp_path, monkeypatch, capsys):
    _seed_ready_repo(tmp_path, capsys)
    task, _ = work_cmd._add_task(
        tmp_path,
        "Run one task",
        acceptance=["Run is bounded."],
    )
    calls = []

    def fake_run(task_text, **kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(work_cmd, "run", fake_run)

    assert daily_cmd.run(target=tmp_path, json_output=True) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    assert calls[0]["task_id"] == task["id"]
    assert receipt["context_pack_id"]
    assert receipt["task_id"] == task["id"]
    assert any(command["command"] == "brigade context build" for command in receipt["commands_invoked"])


def test_daily_closeout_states_and_handoff(tmp_path, monkeypatch, capsys):
    _seed_ready_repo(tmp_path, capsys)
    work_cmd._add_task(tmp_path, "Run closeout task", acceptance=["Closeout exists."])

    def fake_run(task_text, **kwargs):
        return 0

    monkeypatch.setattr(work_cmd, "run", fake_run)
    assert daily_cmd.run(target=tmp_path, json_output=True) == 0
    capsys.readouterr()

    for status in ("reviewed", "deferred", "blocked", "archived"):
        assert daily_cmd.closeout(target=tmp_path, status=status, reason=f"{status} reason", json_output=True) == 0
        receipt = json.loads(capsys.readouterr().out)
        assert receipt["closeout_status"] == status

    assert cli.main(["daily", "closeout", "--target", str(tmp_path), "--handoff", "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["handoff_path"]
    assert Path(receipt["handoff_path"]).is_file()
    assert handoff_cmd.lint_file(Path(receipt["handoff_path"])).valid
