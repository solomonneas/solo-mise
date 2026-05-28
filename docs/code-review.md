# Code Review Producers

Brigade can run configured local code-review producers and turn normalized findings into reviewed work imports. This is for explicit local review by harnesses such as Codex, Claude Opus with subagents, or custom wrappers.

## Commands

```bash
brigade work review init
brigade work review plan
brigade work review run <reviewer-id>
brigade work review run --all
brigade work review runs
brigade work review show <run-id>
brigade work review import-findings <run-id>
```

`init` writes `.brigade/reviews.toml`. `plan` validates the local config and shows the exact command, cwd, timeout, target paths, base ref, output path, findings path, and command blockers. `run` executes only the selected configured reviewer. `import-findings` reads the run's findings file and appends normalized `code-review` imports.

## Config Contract

Reviewers are configured as `[[reviewer]]` entries:

```toml
[[reviewer]]
id = "codex-review"
name = "Codex local code review"
command = "codex review --json"
cwd = "."
enabled = false
timeout = 600
target_paths = ["."]
base_ref = "HEAD"
output_path = ".brigade/reviews/codex-review-output.json"
findings_path = ".brigade/reviews/codex-review-findings.json"
supported_modes = ["diff", "workspace"]
privacy_mode = "safe-summary"
```

The command is parsed with `shlex` and executed directly without a shell. High-risk shell commands and shell metacharacters are refused. All review state and logs stay under `.brigade/` and should remain gitignored.

## Findings Contract

The findings file may be a JSON list or an object with a `findings` list. Each finding supports:

- `id` or `finding_id`
- `severity`: `low`, `medium`, `high`, or `critical`
- `category`: `bug`, `test`, `docs`, `security`, `design`, `maintainability`, `performance`, or `workflow`
- `path`
- `line`
- `safe_excerpt`
- `rationale`
- `suggested_fix`
- `confidence`: `low`, `medium`, or `high`
- `source_fingerprint`

Imported findings use source `code-review`. Brigade preserves reviewer id, review run id, finding id, severity, category, path, line, safe excerpt, rationale, suggested fix, confidence, receipt path, findings path, source item key, and source fingerprint.

## Boundaries

Code review execution is explicit. Brigade does not run reviewers from `work run`, apply code fixes, post GitHub comments, mutate remote services, start daemons, store auth, or promote findings automatically.

Raw stdout and stderr stay in local logs. Work imports receive only redacted summaries and normalized finding fields.
