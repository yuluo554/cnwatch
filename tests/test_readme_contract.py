"""README claims must point at real things.

Four checks, mirroring the delivery discipline: the license file exists, the
documented install extras exist in ``pyproject.toml``, documented commands are
real subcommands, and every real subcommand is documented. Substring assertions
alone would pass even when a user copying the command hits ``No module named``.

The command extractor is deliberately permissive about the token shape (digits
included) so that a typo like ``selftest2`` is *seen and rejected* instead of
being silently invisible to the scan.
"""

import re
from pathlib import Path

from cnwatch.cli import build_parser

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
PYPROJECT = REPO / "pyproject.toml"
LICENSE = REPO / "LICENSE"

# Two-space indented leading token inside the README command block.
_COMMAND_BLOCK_TOKEN = re.compile(r"^\s{2}([A-Za-z][A-Za-z0-9_-]*)\s{2,}", re.MULTILINE)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _known_commands() -> set:
    parser = build_parser()
    subparsers = [action for action in parser._actions if action.dest == "command"][0]
    return set(subparsers.choices)


def _documented_commands() -> set:
    return set(_COMMAND_BLOCK_TOKEN.findall(_read(README)))


def test_scan_surfaces_are_not_empty():
    # A gate that extracts an empty set passes for the wrong reason.
    assert len(_documented_commands()) >= 5
    assert len(_known_commands()) >= 5


def test_license_file_exists_and_matches_claim():
    assert LICENSE.is_file(), "README claims MIT; the repository must ship LICENSE"
    text = _read(LICENSE)
    assert "MIT License" in text
    assert "Permission is hereby granted" in text


def test_documented_extras_exist_in_pyproject():
    readme = _read(README)
    pyproject = _read(PYPROJECT)

    declared = set(re.findall(r"^([a-zA-Z0-9_-]+)\s*=\s*\[", pyproject, re.MULTILINE))
    used = set()
    for match in re.finditer(r'pip install -e "\.\[([^\]]+)\]"', readme):
        used.update(part.strip() for part in match.group(1).split(","))

    assert used, "README should document at least one install command"
    assert used <= declared, "README uses extras not declared in pyproject: %s" % (
        used - declared
    )


def test_documented_commands_are_real_subcommands():
    unknown = _documented_commands() - _known_commands()
    assert unknown == set(), "README documents commands the CLI does not have: %s" % unknown


def test_every_subcommand_is_documented():
    missing = _known_commands() - _documented_commands()
    assert missing == set(), "CLI has commands README never mentions: %s" % missing


def test_readme_states_the_disclaimer_and_the_data_source():
    readme = _read(README)
    assert "不构成投资建议" in readme
    assert "巨潮资讯网" in readme
