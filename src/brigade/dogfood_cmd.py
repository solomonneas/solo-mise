"""Dogfood Brigade against a trusted workspace with a safe default roster."""
from __future__ import annotations

import sys
from pathlib import Path

from . import aboyeur
from . import runs_cmd
from .roster import Agent, Roster

DEFAULT_TASK = "Review this repo and recommend the next implementation slice."


def _dogfood_roster(timeout_seconds: float) -> Roster:
    return Roster(
        orchestrator="chef",
        agents={
            "chef": Agent(
                "chef",
                "codex",
                "Plan one small read-only review task and synthesize a concise final answer.",
                timeout_seconds=timeout_seconds,
            ),
            "reviewer": Agent(
                "reviewer",
                "codex",
                "Inspect the target repo in read-only mode and recommend the next practical implementation slice.",
                timeout_seconds=timeout_seconds,
            ),
        },
        max_workers=1,
        allow_models=("codex",),
        timeout_seconds=timeout_seconds,
    )


def run(
    task: str | None,
    *,
    target: Path,
    output_dir: Path | None = None,
    handoff: bool = True,
    handoff_inbox: Path | None = None,
    inspect: bool = True,
    native_read_only_sandbox: bool = False,
    timeout_seconds: float = 180.0,
) -> int:
    if timeout_seconds <= 0:
        print("error: --timeout-seconds must be positive", file=sys.stderr)
        return 2

    target = target.expanduser().resolve()
    if not target.is_dir():
        print(f"error: --target is not a directory: {target}", file=sys.stderr)
        return 2

    chosen_output_dir = output_dir.expanduser() if output_dir is not None else aboyeur.make_run_dir(
        target / ".brigade" / "runs"
    )
    chosen_handoff_inbox = None
    if handoff:
        chosen_handoff_inbox = (
            handoff_inbox.expanduser() if handoff_inbox is not None else target / ".claude" / "memory-handoffs"
        )

    rc = aboyeur.run(
        task or DEFAULT_TASK,
        _dogfood_roster(timeout_seconds),
        show_plan=True,
        cwd=target,
        output_dir=chosen_output_dir,
        handoff_inbox=chosen_handoff_inbox,
        read_only=True,
        sandbox="read-only" if native_read_only_sandbox else "danger-full-access",
    )
    print(f"artifacts: {chosen_output_dir}", file=sys.stderr)
    if inspect:
        runs_cmd.show(chosen_output_dir)
    return rc
