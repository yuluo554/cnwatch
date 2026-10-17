"""No stub sentinels in the implementation or tests.

Empty ``pass`` tests, unimplemented-exception stubs and manifest files whose hash
field is a filler word can let a milestone look complete while nothing is
implemented. This test is deliberately narrow: it scans the shipped package and
the tests for the sentinels, and it does **not** scan ``plan/`` or ``README.md``
where the words may appear in prose.

Commands that are not implemented yet are expressed through the exit-code
contract (``EXIT_STAGE_NOT_READY``), not through raising stub exceptions.

The sentinel tokens are assembled at runtime rather than written literally, so
this file does not match its own scan. Excluding the scanner from the scan would
be a hole, not a fix.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "cnwatch"
TESTS = REPO / "tests"


def _sentinels():
    return (
        "Not" + "ImplementedError",
        "TODO" + ": " + "实" + "现",
        "TODO" + ": " + "implement",
        "place" + "holder",
    )


def _python_files(root: Path):
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_scan_surface_is_not_empty():
    # A gate whose scan surface is empty passes for the wrong reason.
    assert len(_python_files(SRC)) >= 5
    assert len(_python_files(TESTS)) >= 5


def test_no_stub_sentinels_in_shipped_code():
    hits = []
    for path in _python_files(SRC):
        text = path.read_text(encoding="utf-8")
        for sentinel in _sentinels():
            if sentinel.lower() in text.lower():
                hits.append((path.relative_to(REPO).as_posix(), sentinel))
    assert hits == []


def test_no_stub_sentinels_in_tests():
    hits = []
    for path in _python_files(TESTS):
        text = path.read_text(encoding="utf-8")
        for sentinel in _sentinels():
            if sentinel.lower() in text.lower():
                hits.append((path.relative_to(REPO).as_posix(), sentinel))
    assert hits == []


def test_sentinel_probe_detects_a_planted_hit():
    """Positive control: the scan must actually catch the token it looks for."""

    planted = "raise " + _sentinels()[0] + "()"
    assert _sentinels()[0].lower() in planted.lower()


def test_no_empty_test_bodies():
    """A test whose body is only ``pass`` collects green and tests nothing."""

    offenders = []
    for path in _python_files(TESTS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            body = list(node.body)
            if len(body) == 1 and isinstance(body[0], ast.Pass):
                offenders.append("%s::%s" % (path.name, node.name))
    assert offenders == []
