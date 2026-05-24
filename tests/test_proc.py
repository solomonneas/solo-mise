from brigade import proc


def test_run_captures_exit_and_output():
    r = proc.run(["python3", "-c", "import sys; print('hi'); sys.exit(3)"])
    assert r.code == 3
    assert r.stdout.strip() == "hi"


def test_run_json_parses_stdout():
    r = proc.run(["python3", "-c", "print('{\"a\": 1}')"])
    assert r.json() == {"a": 1}


def test_run_json_returns_none_on_nonjson():
    r = proc.run(["python3", "-c", "print('not json')"])
    assert r.json() is None


def test_which_detects_present_and_absent():
    assert proc.which("python3") is not None
    assert proc.which("definitely-not-a-real-binary-xyz") is None
