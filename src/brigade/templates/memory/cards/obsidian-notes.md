---
topic: obsidian-notes
category: foundation
tags: [obsidian, notes, callouts, vault, sync, knowledge-capture]
---

# Obsidian Notes (Verbatim + Concept)

The `/note` skill captures a session's durable knowledge as an Obsidian-formatted markdown file in `~/notes/`, then syncs it to the user's configured Obsidian vault inbox. It is the user-facing complement to the [memory-scanner](memory-scanner.md): cards are for the agent, Obsidian notes are for the human.

Full skill spec lives in `skills/note/SKILL.md`. This card explains *when* to use it and the two modes you must distinguish before writing.

## Two modes

| Mode | Use when | Output style |
|------|----------|--------------|
| **Verbatim fix** | Troubleshooting, root-causing, post-incident. Future-you needs the exact commands to reproduce the fix. | `[!bug]` -> `[!success]` -> "How it works" -> `[!warning]` gotchas. Heavy on exact strings and runnable code. |
| **Summarize / concept** | Learning, capturing a workflow, documenting a system. The reader needs to understand *why* before they trust the *how*. | Overview -> How it works -> Example -> Tips and gotchas. More prose than callouts. |

Ask the user which mode applies if it is not obvious. Default to verbatim for bugs and to concept for everything else.

## Callout vocabulary

Obsidian callouts make the critical parts stick out without breaking prose flow. Use them sparingly. A note should be mostly regular markdown with callouts highlighting the parts that matter.

| Callout | Use for |
|---------|---------|
| `[!bug]` | Problems, errors, symptoms |
| `[!success]` | Solutions, fixes, what worked |
| `[!tip]` | Helpful hints, shortcuts |
| `[!warning]` | Dangers, things that can break |
| `[!info]` | Background context |
| `[!note]` | General annotations |
| `[!example]` | Practical examples, runnable commands |
| `[!question]` | Open questions, things to investigate |

Syntax:

```markdown
> [!success] Optional title
> Content.
> Multi-line is fine.
```

## YAML frontmatter (required)

```yaml
---
tags:
  - tag1
  - tag2
created: YYYY-MMM-DD
---
```

- 3-5 lowercase, hyphenated tags.
- Date format `YYYY-MMM-DD` (e.g. `2026-Jan-24`). Not ISO. The vault sorts on this.

## Sync target

Three common shapes; the user picks one and documents it in `TOOLS.md`:

1. **Google Drive + rclone bisync.** `~/notes/` writes propagate to the vault inbox on the next bisync timer fire. Most common for cross-machine vaults.
2. **Local Obsidian vault on disk.** `cp ~/notes/<slug>.md ~/Obsidian/<Vault>/<Inbox>/`. Same machine only.
3. **Direct rclone copy.** `rclone copy ~/notes/<slug>.md "gdrive:My Drive/<VaultPath>/<Inbox>/"`. One-shot, no bisync.

If no sync target is configured, leave the file in `~/notes/` and surface the path. Do not invent a sync path.

## When NOT to use /note

- **Durable agent-facing knowledge** -> memory card via the handoff flow, not a vault note. Cards are searched semantically by the agent every session; vault notes are read by humans.
- **Operational runbook detail** -> `TOOLS.md` or a `rules/*.md` file. The publish gate and memory ingester treat those as canonical.
- **Sensitive personal context** -> not in `~/notes/` if the vault syncs to a cloud provider. Use the local-only sync path or skip the note entirely.

## Relationship to the memory system

Vault notes are human-readable references; memory cards are agent-readable durable knowledge. The two have different shapes and different audiences:

- A `/note` is written for future-you reading on a phone at a coffee shop.
- A memory card is written for the agent to retrieve via `memory_search` mid-session.

When both apply, write the card first (through the handoff flow) and then optionally write a `/note` if the user will want to reference it outside the workspace. Do not duplicate content.
