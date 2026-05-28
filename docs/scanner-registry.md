# Brigade Scanner Registry

`brigade work scanners` describes local scanner producers, plans safe run windows, and explicitly runs configured local producers when asked. It does not install cron jobs, start a daemon, mutate remotes, run scanners from `brief` or `doctor`, or promote imports automatically.

The local config is gitignored:

```text
.brigade/scanners.toml
```

Create it with:

```bash
brigade work scanners init
```

## Commands

```bash
brigade work scanners list
brigade work scanners list --json
brigade work scanners show chat-memory-sweep
brigade work scanners plan
brigade work scanners plan --json
brigade work scanners doctor
brigade work scanners doctor --import-issues
brigade work scanners run chat-memory-sweep
brigade work scanners run --all
brigade work scanners run --due
brigade work scanners runs
brigade work scanners run-show <run-id>
```

`plan` calculates intended run windows from scanner cadence and timeout, detects overlapping or clustered scanner windows, and prints a suggested staggered schedule. `doctor` checks missing config, disabled required local producers, bad commands, missing or stale output paths, and schedule conflicts. With `--import-issues`, doctor writes scanner health warnings into the existing work import inbox as task imports.

`run` executes only configured scanner entries from `.brigade/scanners.toml`. It supports one scanner id, `--all`, or `--due`. Due runs compare each scanner cadence with the latest successful receipt. Disabled scanners are skipped unless `--include-disabled` is present. Existing running receipts block execution unless `--force` is present.

Execution is direct and foreground-only. Brigade splits command strings into argv, rejects high-risk shell-like commands, refuses shell metacharacters, and calls the process without a shell. Scanner commands may write their own import records, but Brigade only reports pending import counts after the run. It does not promote anything.

Scanner run receipts are gitignored under:

```text
.brigade/scanners/runs/
```

Each receipt includes the run id, scanner id, source, argv, cwd, started and completed timestamps, duration, exit code, timeout state, stdout/stderr summaries, full local log paths, and output path snapshots from before and after execution. Use `brigade work scanners runs` and `brigade work scanners run-show <run-id>` to review them. `doctor` also reports failed or timed-out runs, malformed receipts, missing logs, stale successful runs, and due scanners.

## Config Shape

Each scanner is a TOML table:

```toml
[[scanner]]
id = "chat-memory-sweep"
source = "chat-memory-sweep"
command = "brigade work import chat-sweep --json"
cadence = "daily@02:15"
enabled = true
timeout = 300
output_path = ".brigade/chat-memory-sweeps/latest.json"
conflict_window = "02:00-02:30"
```

Fields:

- `id`: stable scanner id.
- `command`: command the operator or wrapper may run explicitly.
- `source`: expected work import source.
- `cadence`: `daily@HH:MM` or `hourly@MM`.
- `enabled`: true or false.
- `timeout`: expected max runtime in seconds.
- `output_path`: local output or state file used for freshness checks.
- `conflict_window`: `HH:MM-HH:MM` window that should not overlap related jobs.
- `cwd` or `target`: optional repo-relative working directory for execution.

Default local producers cover chat sweep imports, memory refresh imports, handoff ingest sync, security findings, and optional disabled memory-care, backup-health, and tool-catalog entries. Product-specific chat adapters and tool projection writers remain outside this registry phase.
