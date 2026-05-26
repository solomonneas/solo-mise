import json
import subprocess
from datetime import datetime, timezone

from brigade import cli
from brigade import dogfood_cmd
from brigade import work_cmd


def _write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _init_git_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.DEVNULL)


def test_work_status_reports_repo_and_dogfood_state(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "changed.txt").write_text("work\n")
    dogfood_cmd.init(target=tmp_path, timeout_seconds=33)
    run_dir = tmp_path / ".brigade" / "runs" / "latest"
    run_dir.mkdir(parents=True)
    _write_json(
        run_dir / "run.json",
        {
            "started_at": "2026-05-26T12:00:00Z",
            "status": "ok",
            "task": "review current work",
        },
    )
    (run_dir / "final.txt").write_text("Done.\n\nNext step: Build work start.\n")
    monkeypatch.setattr(work_cmd.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert work_cmd.status(target=tmp_path) == 0
    out = capsys.readouterr().out
    assert f"work: {tmp_path.resolve()}" in out
    assert "repo:" in out
    assert "branch:" in out
    assert "dirty_files:" in out
    assert "?? changed.txt" in out
    assert "dogfood: ready" in out
    assert f"dogfood_config: {tmp_path / '.brigade' / 'dogfood.toml'}" in out
    assert "codex: /usr/bin/codex" in out
    assert "latest_run: 2026-05-26T12:00:00Z [ok]" in out
    assert "latest_task: review current work" in out
    assert "next: Build work start." in out
    assert "next_command: brigade dogfood next" in out


def test_work_status_runs_without_dogfood_config(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    monkeypatch.setattr(work_cmd.shutil, "which", lambda name: None)

    assert work_cmd.status(target=tmp_path) == 0
    out = capsys.readouterr().out
    assert "dogfood: not ready" in out
    assert "dogfood_config:" in out
    assert "(missing)" in out
    assert "codex: missing" in out
    assert "latest_run: none" in out
    assert "next: none" in out


def test_work_status_rejects_bad_limit(tmp_path, capsys):
    tmp_path.mkdir(exist_ok=True)

    assert work_cmd.status(target=tmp_path, limit=0) == 2
    assert "--limit must be a positive integer" in capsys.readouterr().err


def test_work_start_creates_active_session(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    monkeypatch.setattr(
        work_cmd,
        "_now",
        lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert work_cmd.start(target=tmp_path, title="Build Work Loop") == 0
    out = capsys.readouterr().out
    session_dir = tmp_path / ".brigade" / "work" / "20260526-120000-build-work-loop"
    assert f"session: {session_dir}" in out
    assert (tmp_path / ".brigade" / "work" / "current").read_text() == "20260526-120000-build-work-loop\n"
    payload = json.loads((session_dir / "session.json").read_text())
    assert payload["status"] == "active"
    assert payload["title"] == "Build Work Loop"
    assert payload["start"]["git"]["available"] is True
    assert (session_dir / "start.md").is_file()


def test_work_start_refuses_existing_session_without_force(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    monkeypatch.setattr(
        work_cmd,
        "_now",
        lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert work_cmd.start(target=tmp_path, title="one") == 0
    assert work_cmd.start(target=tmp_path, title="two") == 2
    assert "already active" in capsys.readouterr().err


def test_work_end_closes_active_session(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    times = iter(
        [
            datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(work_cmd, "_now", lambda: next(times))
    assert work_cmd.start(target=tmp_path, title="Build Work Loop") == 0

    assert work_cmd.end(target=tmp_path, note="done for now") == 0
    out = capsys.readouterr().out
    session_dir = tmp_path / ".brigade" / "work" / "20260526-120000-build-work-loop"
    assert f"session: {session_dir}" in out
    assert not (tmp_path / ".brigade" / "work" / "current").exists()
    payload = json.loads((session_dir / "session.json").read_text())
    assert payload["status"] == "ended"
    assert payload["note"] == "done for now"
    assert payload["ended_at"] == "2026-05-26T13:00:00+00:00"
    assert payload["end"]["git"]["available"] is True
    assert "done for now" in (session_dir / "end.md").read_text()


def test_work_end_can_write_handoff(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    times = iter(
        [
            datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(work_cmd, "_now", lambda: next(times))
    assert work_cmd.start(target=tmp_path, title="Build Work Loop") == 0

    inbox = tmp_path / "handoffs"
    assert work_cmd.end(target=tmp_path, note="done for now", handoff=True, handoff_inbox=inbox) == 0
    out = capsys.readouterr().out
    assert "handoff:" in out
    handoffs = list(inbox.glob("*-brigade-work-build-work-loop-*.md"))
    assert len(handoffs) == 1
    handoff = handoffs[0].read_text()
    assert "# Memory Handoff" in handoff
    assert "Brigade work session ended" in handoff
    assert "done for now" in handoff
    session_dir = tmp_path / ".brigade" / "work" / "20260526-120000-build-work-loop"
    payload = json.loads((session_dir / "session.json").read_text())
    assert payload["handoff"] == str(handoffs[0])


def test_work_end_reports_no_active_session(tmp_path, capsys):
    _init_git_repo(tmp_path)

    assert work_cmd.end(target=tmp_path) == 1
    assert "no active work session" in capsys.readouterr().err


def test_work_list_prints_recent_sessions(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    times = iter(
        [
            datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 12, 30, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 13, 30, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(work_cmd, "_now", lambda: next(times))
    assert work_cmd.start(target=tmp_path, title="Older Session") == 0
    assert work_cmd.end(target=tmp_path) == 0
    assert work_cmd.start(target=tmp_path, title="Newer Session") == 0
    assert work_cmd.end(target=tmp_path) == 0

    assert work_cmd.list_sessions(target=tmp_path, limit=10) == 0
    out = capsys.readouterr().out
    assert out.index("Newer Session") < out.index("Older Session")
    assert "[ended]" in out
    assert "dirty=" in out


def test_work_latest_shows_latest_session(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    times = iter(
        [
            datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 26, 13, 0, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(work_cmd, "_now", lambda: next(times))
    assert work_cmd.start(target=tmp_path, title="Build Work Loop") == 0
    assert work_cmd.end(target=tmp_path, note="done") == 0

    assert work_cmd.latest(target=tmp_path) == 0
    out = capsys.readouterr().out
    assert "session:" in out
    assert "title: Build Work Loop" in out
    assert "status: ended" in out
    assert "note: done" in out
    assert "git:" in out
    assert "dogfood:" in out


def test_work_show_accepts_session_id(tmp_path, monkeypatch, capsys):
    _init_git_repo(tmp_path)
    monkeypatch.setattr(
        work_cmd,
        "_now",
        lambda: datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert work_cmd.start(target=tmp_path, title="Build Work Loop") == 0

    assert work_cmd.show(target=tmp_path, session="20260526-120000-build-work-loop") == 0
    out = capsys.readouterr().out
    assert "id: 20260526-120000-build-work-loop" in out
    assert "status: active" in out


def test_work_latest_reports_no_sessions(tmp_path, capsys):
    _init_git_repo(tmp_path)

    assert work_cmd.latest(target=tmp_path) == 1
    assert "no work sessions found" in capsys.readouterr().err


def test_work_status_cli(tmp_path, monkeypatch):
    seen = {}

    def fake_status(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(work_cmd, "status", fake_status)

    assert cli.main(["work", "status", "--target", str(tmp_path), "--limit", "3"]) == 0
    assert seen == {"target": tmp_path, "limit": 3}


def test_work_start_and_end_cli(tmp_path, monkeypatch):
    seen = []

    def fake_start(**kwargs):
        seen.append(("start", kwargs))
        return 0

    def fake_end(**kwargs):
        seen.append(("end", kwargs))
        return 0

    monkeypatch.setattr(work_cmd, "start", fake_start)
    monkeypatch.setattr(work_cmd, "end", fake_end)

    assert cli.main(["work", "start", "Build", "Loop", "--target", str(tmp_path), "--force"]) == 0
    assert (
        cli.main(
            [
                "work",
                "end",
                "--target",
                str(tmp_path),
                "--note",
                "done",
                "--handoff",
                "--handoff-inbox",
                str(tmp_path / "handoffs"),
            ]
        )
        == 0
    )
    assert seen == [
        ("start", {"target": tmp_path, "title": "Build Loop", "force": True}),
        (
            "end",
            {
                "target": tmp_path,
                "note": "done",
                "handoff": True,
                "handoff_inbox": tmp_path / "handoffs",
            },
        ),
    ]


def test_work_inspection_cli(tmp_path, monkeypatch):
    seen = []

    def fake_list(**kwargs):
        seen.append(("list", kwargs))
        return 0

    def fake_latest(**kwargs):
        seen.append(("latest", kwargs))
        return 0

    def fake_show(**kwargs):
        seen.append(("show", kwargs))
        return 0

    monkeypatch.setattr(work_cmd, "list_sessions", fake_list)
    monkeypatch.setattr(work_cmd, "latest", fake_latest)
    monkeypatch.setattr(work_cmd, "show", fake_show)

    assert cli.main(["work", "list", "--target", str(tmp_path), "--limit", "2"]) == 0
    assert cli.main(["work", "latest", "--target", str(tmp_path)]) == 0
    assert cli.main(["work", "show", "abc123", "--target", str(tmp_path)]) == 0
    assert seen == [
        ("list", {"target": tmp_path, "limit": 2}),
        ("latest", {"target": tmp_path}),
        ("show", {"target": tmp_path, "session": "abc123"}),
    ]
