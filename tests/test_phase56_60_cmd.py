import json
from datetime import timedelta

from brigade import cli
from brigade import repos_cmd
from brigade import work_cmd

from tests.test_phase44_cmd import _build_train


def _iso_after(days: int) -> str:
    return (repos_cmd._now() + timedelta(days=days)).isoformat()


def test_expired_waivers_do_not_satisfy_ready_until_renewed(tmp_path, monkeypatch, capsys):
    train = _build_train(tmp_path, monkeypatch, capsys)
    assert repos_cmd.release_closeout(target=tmp_path, train_id=train["train_id"], status="reviewed", json_output=True) == 0
    capsys.readouterr()
    assert repos_cmd.release_actions_build(target=tmp_path, train_id=train["train_id"], json_output=True) == 0
    capsys.readouterr()

    future = _iso_after(1)
    past = _iso_after(-1)
    assert repos_cmd.release_waiver_record(target=tmp_path, train_id=train["train_id"], scope="blocked-repo", reason="reviewed", expires_at=future, json_output=True) == 0
    capsys.readouterr()
    assert repos_cmd.release_waiver_record(target=tmp_path, train_id=train["train_id"], scope="unresolved-action", reason="reviewed", expires_at=future, json_output=True) == 0
    capsys.readouterr()
    assert repos_cmd.release_waiver_record(target=tmp_path, train_id=train["train_id"], scope="missing-evidence", reason="expired review", expires_at=past, json_output=True) == 0
    expired = json.loads(capsys.readouterr().out)["waiver"]

    assert repos_cmd.release_ready(target=tmp_path, train_id=train["train_id"], json_output=True) == 1
    not_ready = json.loads(capsys.readouterr().out)
    assert "train has missing manual evidence" in not_ready["blockers"]
    assert {item["scope"] for item in not_ready["waived"]} == {"blocked-repo", "unresolved-action"}
    assert any(issue["name"] == "release_waiver_expired" for issue in not_ready["waiver_issues"])

    assert cli.main(["repos", "release", "waivers", "renew", expired["waiver_id"], "--reason", "reviewed again", "--expires-at", future, "--target", str(tmp_path), "--json"]) == 0
    renewed = json.loads(capsys.readouterr().out)
    assert renewed["waiver"]["status"] == "active"
    assert renewed["waiver"]["expires_at"] == future

    assert repos_cmd.release_ready(target=tmp_path, train_id=train["train_id"], json_output=True) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["ready"] is True
    assert {item["scope"] for item in ready["waived"]} == {"blocked-repo", "unresolved-action", "missing-evidence"}


def test_waiver_doctor_and_import_issues_dedupe(tmp_path, monkeypatch, capsys):
    train = _build_train(tmp_path, monkeypatch, capsys)
    assert repos_cmd.release_waiver_record(target=tmp_path, train_id=train["train_id"], scope="blocked-repo", reason="temporary review", json_output=True) == 0
    waiver = json.loads(capsys.readouterr().out)["waiver"]
    waiver_path = tmp_path / ".brigade" / "repos" / "releases" / "waivers.jsonl"
    waiver["created_at"] = (repos_cmd._now() - timedelta(days=10)).isoformat()
    waiver["updated_at"] = waiver["created_at"]
    waiver_path.write_text(json.dumps(waiver, sort_keys=True) + "\n")

    assert cli.main(["repos", "release", "waivers", "doctor", train["train_id"], "--target", str(tmp_path), "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    names = {issue["name"] for issue in doctor["issues"]}
    assert {"release_waiver_missing_expiry", "release_waiver_stale_review"} <= names

    assert cli.main(["repos", "release", "waivers", "import-issues", train["train_id"], "--target", str(tmp_path), "--json"]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["created"] == 2
    assert {item["source"] for item in work_cmd._read_imports(tmp_path)} == {"repo-fleet-release-waiver"}

    assert repos_cmd.release_waiver_import_issues(target=tmp_path, train_id=train["train_id"], json_output=True) == 0
    deduped = json.loads(capsys.readouterr().out)
    assert deduped["skipped"] == 2


def test_release_audit_and_ready_include_waiver_health(tmp_path, monkeypatch, capsys):
    train = _build_train(tmp_path, monkeypatch, capsys)
    assert repos_cmd.release_closeout(target=tmp_path, train_id=train["train_id"], status="reviewed", json_output=True) == 0
    capsys.readouterr()
    assert repos_cmd.release_waiver_record(target=tmp_path, train_id=train["train_id"], scope="blocked-repo", reason="temporary review", json_output=True) == 0
    capsys.readouterr()

    assert cli.main(["repos", "release", "audit", train["train_id"], "--target", str(tmp_path), "--json"]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit["waiver_issue_count"] >= 1
    assert "release_waiver_missing_expiry" in {issue["name"] for issue in audit["issues"]}

    assert repos_cmd.release_ready(target=tmp_path, train_id=train["train_id"], json_output=True) == 1
    ready = json.loads(capsys.readouterr().out)
    assert ready["waiver_issue_count"] >= 1
    assert "release_waiver_missing_expiry" in {issue["name"] for issue in ready["waiver_issues"]}
