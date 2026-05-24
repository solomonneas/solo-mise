import pytest


@pytest.fixture
def tmp_target(tmp_path):
    """Return a clean temp directory to act as a workspace target."""
    return tmp_path / "ws"


@pytest.fixture(autouse=True)
def _no_managed_tools_on_path(monkeypatch, request):
    """Default to the bare-host baseline: no managed tool detected on PATH.

    The doctor folds in installed managed tools, but a dev host may have some
    of them globally installed. Neutralize detection so checks assert against
    the documented bare-`$HOME` condition. Tests that exercise installed tools
    re-patch `managed.proc.which` in their own body, which overrides this.

    `tests/test_proc.py` validates `proc.which` against real binaries, so it
    opts out (patching `managed.proc.which` would patch the same function).
    """
    if request.module.__name__.rsplit(".", 1)[-1] == "test_proc":
        return
    from brigade import managed

    monkeypatch.setattr(managed.proc, "which", lambda cmd: None)
