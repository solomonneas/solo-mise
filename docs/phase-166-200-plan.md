# Brigade Phase 166-200 Plan

This plan keeps the post-ledger AFK work bounded and auditable. The source of truth for run-specific evidence is the local phase ledger under `.brigade/work/phases/`; this public file describes what the phase range is meant to deliver.

## Objective

Turn the phase execution ledger from a basic record store into a usable AFK control surface with range status, next-step selection, report bundles, work-inbox routing, release evidence, and daily/operator visibility.

## Phase Map

| Phase | Deliverable |
| --- | --- |
| 166 | Plan explicit phase records for 166-200 before implementation starts. |
| 167 | Add phase ledger schema output for wrapper-facing contracts. |
| 168 | Add range status summaries with counts, missing records, and completion state. |
| 169 | Add next-phase selection for resuming unattended work. |
| 170 | Add local phase report bundle build. |
| 171 | Add phase report list and show commands. |
| 172 | Add phase-ledger issue imports into the work inbox. |
| 173 | Add release readiness and release candidate phase-ledger evidence. |
| 174 | Keep daily status aware of phase-ledger health. |
| 175 | Keep daily doctor aware of phase-ledger issues. |
| 176 | Keep work brief aware of phase-ledger health. |
| 177 | Keep work doctor aware of phase-ledger issues. |
| 178 | Keep center status and review queue aware of phase-ledger issues. |
| 179 | Preserve explicit grouped phase range semantics. |
| 180 | Detect completed phases without tests. |
| 181 | Detect completed phases without changed files or deferral evidence. |
| 182 | Detect committed phases without commit hashes. |
| 183 | Detect pushed phases without push refs. |
| 184 | Detect stale in-progress phases. |
| 185 | Detect blocked phases without next recommendations. |
| 186 | Write human-readable phase report Markdown. |
| 187 | Write machine-readable phase report JSON evidence. |
| 188 | Dedupe phase-ledger work imports by stable fingerprint. |
| 189 | Give imported ledger issues actionable acceptance criteria. |
| 190 | Document the AFK ledger command workflow. |
| 191 | Regenerate the public command inventory for new commands. |
| 192 | Keep phase outputs public-safe and free of raw private evidence. |
| 193 | Keep run-specific phase records local and gitignored. |
| 194 | Keep phase record fields stable and documented. |
| 195 | Expose status counts for wrappers. |
| 196 | Detect missing records in requested phase ranges. |
| 197 | Test explicit grouped range records. |
| 198 | Verify focused and full test suites. |
| 199 | Write and lint a Memory Handoff for durable workflow knowledge. |
| 200 | Commit, push, and mark local phase records with commit and push evidence. |

## Stop Condition

Stop only when phases 166-200 have local ledger records, the implemented command surface is tested, docs and command inventory are updated, release evidence includes phase-ledger state, the handoff is linted, verification passes, privacy scans pass, and the phase range is committed and pushed.
