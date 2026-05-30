# Repo Fleet

`brigade repos` inspects configured local repositories from gitignored `.brigade/repos.toml`. It is a local operator workflow for safe labels, readiness checks, report bundles, and reviewed fleet actions.

Commands:

```bash
brigade repos init
brigade repos list
brigade repos show <repo-id>
brigade repos scan
brigade repos doctor
brigade repos import-issues
brigade repos report plan
brigade repos report build
brigade repos report list
brigade repos report show <report-id>
brigade repos report archive <report-id>
brigade repos report closeout <report-id>
brigade repos actions plan <report-id>
brigade repos actions build <report-id>
brigade repos actions list
brigade repos actions show <fleet-action-id>
brigade repos actions start <fleet-action-id>
brigade repos actions done <fleet-action-id>
brigade repos actions defer <fleet-action-id> --reason "not today"
brigade repos actions archive --completed
```

Fleet reports are written under:

```text
.brigade/repos/reports/
```

Fleet action queues are written under:

```text
.brigade/repos/actions/
```

Fleet reports include safe repo ids, safe labels, status counts, blocker and warning counts, top pending action summaries, receipt labels, and suggested next commands. Fleet actions store local metadata only: repo id, safe label, source subsystem, source local id, status, priority or severity, safe summary, suggested command, timestamps, and source fingerprint.

`brigade repos actions build` requires the source fleet report to be closed out as `reviewed` or `deferred` unless `--allow-unreviewed` is passed. Repeated builds dedupe by repo id, report fingerprint, and source item fingerprint, including archived actions from the same report.

Privacy boundaries:

- No cloning.
- No remote mutation.
- No automatic action execution.
- No automatic promotion or dismissal.
- No exact private repo names, owner names, org names, hostnames, local paths, raw logs, scanner output, private config contents, secrets, or raw evidence in public files.
- Gitignored local config may store local paths and safe labels.
