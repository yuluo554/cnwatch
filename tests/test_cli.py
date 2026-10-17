"""CLI contract: exit codes, dispatch, and the no-side-effect guarantee."""

import pytest

from cnwatch import __version__
from cnwatch.cli import STAGE_NOT_READY, build_parser, main
from cnwatch.exit_codes import (
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_STAGE_NOT_READY,
    EXIT_USAGE,
)


def test_exit_codes_are_distinct():
    codes = {EXIT_OK, EXIT_DEGRADED, EXIT_USAGE, EXIT_STAGE_NOT_READY}
    assert codes == {0, 1, 2, 3}


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == EXIT_OK
    out = capsys.readouterr().out
    assert "selftest" in out
    assert "exit codes" in out


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == EXIT_OK
    assert __version__ in capsys.readouterr().out


def test_no_command_is_usage_error(capsys):
    assert main([]) == EXIT_USAGE
    assert "usage" in capsys.readouterr().out.lower()


def test_unknown_command_is_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        main(["definitely-not-a-command"])
    assert excinfo.value.code == EXIT_USAGE


@pytest.mark.parametrize("command", sorted(STAGE_NOT_READY))
def test_stage_not_ready_commands_exit_three(command, tmp_path, capsys):
    home = tmp_path / "home"

    assert main(["--home", str(home), command]) == EXIT_STAGE_NOT_READY

    err = capsys.readouterr().err
    assert command in err
    assert STAGE_NOT_READY[command][0] in err
    # No side effects: the runtime root must not even be created.
    assert not home.exists()


def test_stage_not_ready_registry_covers_planned_commands():
    parser = build_parser()
    subparsers = [
        action for action in parser._actions if action.dest == "command"
    ][0]
    registered = set(subparsers.choices)
    implemented = {"selftest"}
    assert registered == implemented | set(STAGE_NOT_READY)


def test_selftest_on_clean_home_passes(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(["--home", str(home), "selftest"])
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "result: pass" in out
    assert (home / "cnwatch.db").is_file()


def test_selftest_fails_on_empty_resources(tmp_path, capsys):
    home = tmp_path / "home"
    empty = tmp_path / "empty-resources"
    empty.mkdir()

    code = main(["--home", str(home), "selftest", "--resources", str(empty)])

    out = capsys.readouterr().out
    assert code == EXIT_DEGRADED
    assert "result: fail" in out
    assert "FAIL" in out
