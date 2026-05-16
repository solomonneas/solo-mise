# Contributing to solo-mise

solo-mise is the installable kit behind [Solomon's Cookbook](https://github.com/solomonneas/solos-cookbook). Patches are welcome. Before you start, please skim this file so we both spend our time on the right things.

## What kinds of changes land easily

- **Bug fixes** for `solo-mise init`, `doctor`, `scrub`, or the ingester.
- **Profile improvements**: new bootstrap content, sharper post-install notes, better defaults.
- **New harness adapters** (with doctor checks) under `src/solo_mise/templates/<harness>/`.
- **Doctor checks** that catch real, observed failure modes.
- **Test coverage** for any of the above.

## What needs a conversation first

- **A new top-level profile.** Open an issue first describing the user story. Profiles are the public surface and renaming or splitting them later is painful.
- **Breaking changes** to template paths, the handoff TEMPLATE.md fields, or the ingester routing rules.
- **Anything that adds a runtime dependency.** solo-mise has zero runtime deps on purpose, and we want to keep it that way.

## What does not land

- Personal details, hostnames, IPs, account IDs, or live auth profiles in templates or tests. The whole point of this kit is to keep that stuff out of public repos. The `content-guard` job in CI will fail if it finds any.
- Cron jobs or hooks that post or call out to the network without explicit opt-in.
- AI-co-authorship trailers on commits (`Co-Authored-By: <model>`). Conventional commits only.

## Local dev

```bash
git clone https://github.com/solomonneas/solo-mise.git
cd solo-mise
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

To smoke-test a profile end-to-end the same way CI does:

```bash
target=/tmp/solo-mise-smoke
rm -rf "$target" && mkdir -p "$target" && git init -q "$target"
python -m solo_mise init --target "$target" --profile workspace
python -m solo_mise doctor --target "$target"
```

## Adding a profile

A profile is a single JSON manifest in `src/solo_mise/templates/profiles/<id>.json` and any template files it references. Manifests support `extends` for inheritance. See `publisher.json` (extends `repo`) for the simplest example.

When you add a profile:

1. Add it to the `choices=[...]` list in `src/solo_mise/cli.py` (the `--profile` flag).
2. Add a row to the profile table in `README.md`.
3. Add it to the matrix in `.github/workflows/ci.yml` so the smoke job exercises it.
4. If it has post-install steps, list them in `post_install_notes`. They are printed at the end of `solo-mise init`.

## Adding a doctor check

Check functions live in `src/solo_mise/doctor.py`. Each returns a list of `(status, name, detail)` tuples where status is `OK`, `WARN`, `FAIL`, or `MANUAL`. Prefer `WARN` or `MANUAL` over `FAIL` for things the user can choose not to wire up - `FAIL` should mean "this profile is broken."

## Promoting an experimental adapter

The Hermes adapter is currently marked experimental. To graduate it (or any future experimental adapter) to "tested":

- A doctor check exists that meaningfully exercises the adapter against a real install.
- Someone has run the full init + doctor cycle on a real Hermes workspace and reported it on an issue.
- The post-install notes no longer say "experimental".

Open a PR with all three and we'll land it.

## Filing issues

Please use the templates under `.github/ISSUE_TEMPLATE/` - they exist to save you from re-typing the version and profile every time.

The `ingester-misclassified` template is the most useful one to file early. If a handoff that should have promoted to a card got bounced (or vice versa), that is a real bug in the routing rules, not a corner case. We want to see it.

## License

By contributing you agree that your contribution is licensed under the MIT License, same as the rest of the repo.
