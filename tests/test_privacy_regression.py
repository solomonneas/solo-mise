import json
import subprocess
from pathlib import Path

from brigade import context_cmd
from brigade import learn_cmd
from brigade import release_cmd
from brigade import repos_cmd
from brigade import security_cmd
from brigade import work_cmd


PRIVATE_SENTINEL = "RAW_PRIVATE_EVIDENCE_SENTINEL"


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _init_git_repo(path: Path):
    subprocess.run(["git", "init"], cwd=path, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "dev@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=path, check=True)
    (path / "README.md").write_text("readme\n")
    (path / "CHANGELOG.md").write_text("## [Unreleased]\n\n- Initial.\n")
    (path / "ROADMAP.md").write_text("# Roadmap\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, stdout=subprocess.DEVNULL)


def _rendered(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def test_chat_backup_and_security_outputs_do_not_copy_private_values(tmp_path, capsys):
    _init_git_repo(tmp_path)
    sweep = tmp_path / ".brigade" / "chat-memory-sweeps" / "latest.json"
    _write_json(
        sweep,
        {
            "sweep_id": "nightly-private",
            "provider": "local",
            "issues": [
                {
                    "id": "chat-1",
                    "title": "Chat follow-up",
                    "summary": "Safe chat follow-up.",
                    "actionable": True,
                    "raw_text": PRIVATE_SENTINEL,
                    "metadata": {"raw_messages": [PRIVATE_SENTINEL]},
                }
            ],
        },
    )
    assert work_cmd.import_chat_sweep(target=tmp_path, json_output=True) == 0
    chat_payload = json.loads(capsys.readouterr().out)
    assert PRIVATE_SENTINEL not in _rendered(chat_payload)
    assert PRIVATE_SENTINEL not in _rendered(work_cmd._read_imports(tmp_path))

    (tmp_path / ".brigade" / "backups.toml").write_text(
        """
[[destination]]
id = "nas"
kind = "nas"
command_label = "safe summary"
summary_path = ".brigade/backups/nas-summary.json"
snapshot_stale_hours = 24
check_stale_hours = 48
prune_stale_hours = 48
restore_rehearsal_stale_days = 30
enabled = true
"""
    )
    _write_json(
        tmp_path / ".brigade" / "backups" / "nas-summary.json",
        {
            "destination_label": "NAS backup",
            "latest_snapshot_at": "2026-05-25T12:00:00+00:00",
            "latest_check_at": "2026-05-30T10:00:00+00:00",
            "latest_check_result": "ok",
            "latest_prune_at": "2026-05-30T10:00:00+00:00",
            "latest_prune_result": "ok",
            "latest_restore_rehearsal_at": "2026-05-01T12:00:00+00:00",
            "latest_restore_rehearsal_result": "ok",
            "summary": "NAS snapshot is stale.",
            "evidence_path": ".brigade/backups/nas-evidence.json",
            "hostname": PRIVATE_SENTINEL,
            "repo_path": f"/private/{PRIVATE_SENTINEL}",
        },
    )
    assert work_cmd.backup_doctor(target=tmp_path, json_output=True) == 0
    assert PRIVATE_SENTINEL not in capsys.readouterr().out
    assert work_cmd.backup_import_issues(target=tmp_path, json_output=True) == 0
    backup_payload = json.loads(capsys.readouterr().out)
    assert PRIVATE_SENTINEL not in _rendered(backup_payload)
    assert PRIVATE_SENTINEL not in _rendered(work_cmd._read_imports(tmp_path))

    secret_value = "secret" + "value" + "1234567890"
    (tmp_path / ".env").write_text("SERVICE_" + "TOKEN=" + secret_value + "\n")
    assert security_cmd.scan(target=tmp_path, fail_on="none", import_findings=True, json_output=True) == 0
    security_payload = json.loads(capsys.readouterr().out)
    assert secret_value not in _rendered(security_payload)
    assert secret_value not in (tmp_path / ".brigade" / "security" / "latest" / "security-report.json").read_text()
    assert secret_value not in _rendered(work_cmd._read_imports(tmp_path))


def test_repo_context_learning_and_release_outputs_do_not_copy_private_values(tmp_path, capsys):
    _init_git_repo(tmp_path)
    (tmp_path / "AGENTS.md").write_text(f"private guidance {PRIVATE_SENTINEL}\n")
    (tmp_path / "README.md").write_text(f"private readme {PRIVATE_SENTINEL}\n")
    _write_json(
        tmp_path / ".brigade" / "work" / "tasks.json",
        {
            "version": 1,
            "tasks": [
                {
                    "id": "task-one",
                    "text": "Build privacy regression",
                    "status": "pending",
                    "acceptance": ["Private raw evidence stays out of generated artifacts."],
                    "created_at": "2026-05-30T12:00:00+00:00",
                }
            ],
        },
    )

    assert repos_cmd.init(target=tmp_path, force=True, update_gitignore=False, json_output=True) == 0
    capsys.readouterr()
    assert repos_cmd.scan(target=tmp_path, json_output=True) == 0
    assert PRIVATE_SENTINEL not in capsys.readouterr().out
    assert repos_cmd.import_issues(target=tmp_path, json_output=True) == 0
    assert PRIVATE_SENTINEL not in capsys.readouterr().out

    assert context_cmd.build(target=tmp_path, kind="task", task_id="task-one", json_output=True) == 0
    context_payload = json.loads(capsys.readouterr().out)
    context_dir = Path(context_payload["path"])
    assert PRIVATE_SENTINEL not in _rendered(context_payload)
    assert PRIVATE_SENTINEL not in (context_dir / "context.json").read_text()
    assert PRIVATE_SENTINEL not in (context_dir / "CONTEXT.md").read_text()

    private_import = work_cmd._make_import(
        f"Security finding includes {PRIVATE_SENTINEL}",
        kind="finding",
        source="security-scan",
        metadata={"source_item_key": "security:private", "source_fingerprint": "private-fp"},
    )
    work_cmd._write_imports(tmp_path, [private_import])
    assert learn_cmd.plan(target=tmp_path, json_output=True) == 0
    learning_payload = json.loads(capsys.readouterr().out)
    assert PRIVATE_SENTINEL not in _rendered(learning_payload)
    assert learn_cmd.import_issues(target=tmp_path, dry_run=True, json_output=True) == 0
    assert PRIVATE_SENTINEL not in capsys.readouterr().out

    private_release_value = "release" + "secret" + "1234567890"
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n- Rotate SERVICE_" + "TOKEN=" + private_release_value + "\n"
    )
    subprocess.run(["git", "add", "README.md", "AGENTS.md", "CHANGELOG.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "update local docs"], cwd=tmp_path, check=True, stdout=subprocess.DEVNULL)
    assert release_cmd.candidate_build(target=tmp_path, base_ref=None, json_output=True) == 0
    candidate = json.loads(capsys.readouterr().out)
    candidate_dir = Path(candidate["path"])
    assert private_release_value not in _rendered(candidate)
    assert private_release_value not in (candidate_dir / "RELEASE_NOTES_DRAFT.md").read_text()
    assert private_release_value not in (candidate_dir / "EVIDENCE.json").read_text()
