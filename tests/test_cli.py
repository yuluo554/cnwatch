"""CLI contract: exit codes, dispatch, and the no-side-effect guarantee."""

import json
from pathlib import Path

import pytest

from cnwatch import __version__
from cnwatch.cli import STAGE_NOT_READY, build_parser, main
from cnwatch.exit_codes import (
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_STAGE_NOT_READY,
    EXIT_USAGE,
)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
SAMPLE = DATA / "fixtures" / "watchlist-sample.csv"
SNAPSHOTS = DATA / "snapshots"


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
    implemented = {"selftest", "watchlist", "fetch"}
    assert registered == implemented | set(STAGE_NOT_READY)


def test_stage_not_ready_shrinks_as_milestones_land():
    """M2 landed: watchlist and fetch must no longer be stage-gated."""

    assert "watchlist" not in STAGE_NOT_READY
    assert "fetch" not in STAGE_NOT_READY


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


# --------------------------------------------------------------------------- #
# watchlist command
# --------------------------------------------------------------------------- #


def test_watchlist_import_then_list(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "watchlist", "import", "--file", str(SAMPLE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "added 20" in out

    assert main(["--home", str(home), "watchlist", "list"]) == EXIT_OK
    table = capsys.readouterr().out
    assert "600519" in table
    assert "gssh0600519" in table


def test_watchlist_import_with_bad_rows_exits_one(tmp_path, capsys):
    home = tmp_path / "home"
    bad = DATA / "fixtures" / "negative" / "watchlist-bad-rows.csv"
    code = main(["--home", str(home), "watchlist", "import", "--file", str(bad)])
    out = capsys.readouterr().out
    assert code == EXIT_DEGRADED
    assert "rejected 3" in out
    assert "rejected line" in out


def test_watchlist_unknown_group_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        ["--home", str(home), "watchlist", "add", "--code", "600519", "--group", "重点"]
    )
    assert code == EXIT_USAGE
    assert "unknown group" in capsys.readouterr().err


def test_watchlist_code_outside_master_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        ["--home", str(home), "watchlist", "add", "--code", "999999", "--group", "观察"]
    )
    assert code == EXIT_USAGE
    assert "stock master" in capsys.readouterr().err


def test_watchlist_add_edit_remove_round_trip(tmp_path, capsys):
    home = tmp_path / "home"
    base = ["--home", str(home), "watchlist"]
    assert main(base + ["add", "--code", "300750", "--group", "观察"]) == EXIT_OK
    assert "GD165627" in capsys.readouterr().out

    assert main(base + ["edit", "--code", "300750", "--group", "风险", "--note", "关注"]) == EXIT_OK
    assert "风险" in capsys.readouterr().out

    assert main(base + ["search", "--query", "ndsd"]) == EXIT_OK
    assert "300750" in capsys.readouterr().out

    assert main(base + ["remove", "--code", "300750"]) == EXIT_OK
    assert main(base + ["list"]) == EXIT_OK
    assert "watchlist is empty" in capsys.readouterr().out


def test_watchlist_audit_flag_shows_trail(tmp_path, capsys):
    home = tmp_path / "home"
    base = ["--home", str(home), "watchlist"]
    main(base + ["add", "--code", "600519", "--group", "持仓"])
    main(base + ["edit", "--code", "600519", "--note", "长期"])
    capsys.readouterr()
    assert main(base + ["list", "--audit"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "add" in out and "edit" in out


def test_watchlist_missing_file_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(["--home", str(home), "watchlist", "import", "--file", str(tmp_path / "nope.csv")])
    assert code == EXIT_USAGE
    assert "CSV not found" in capsys.readouterr().err


def test_watchlist_without_required_arguments_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    for action in ("add", "remove", "edit"):
        assert main(["--home", str(home), "watchlist", action]) == EXIT_USAGE
    assert main(["--home", str(home), "watchlist", "search"]) == EXIT_USAGE
    assert main(["--home", str(home), "watchlist", "import"]) == EXIT_USAGE


# --------------------------------------------------------------------------- #
# fetch command
# --------------------------------------------------------------------------- #


def test_fetch_show_config_prints_the_parsed_policy(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "fetch", "--show-config"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "interval: 1.0s" in out
    assert "page_size: 30" in out
    assert "retry_delays: 1s, 2s, 4s" in out


def test_fetch_on_empty_watchlist_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        [
            "--home", str(home), "fetch",
            "--snapshots-dir", str(SNAPSHOTS),
            "--today", "2026-10-16",
        ]
    )
    assert code == EXIT_USAGE
    assert "watchlist is empty" in capsys.readouterr().err


def test_fetch_replays_the_frozen_day(tmp_path, capsys):
    home = tmp_path / "home"
    assert main(["--home", str(home), "watchlist", "import", "--file", str(SAMPLE)]) == EXIT_OK
    capsys.readouterr()

    code = main(
        [
            "--home", str(home), "fetch",
            "--snapshots-dir", str(SNAPSHOTS),
            "--today", "2026-10-16",
        ]
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK, out
    assert "requests 20" in out
    assert "baseline established" in out
    assert "result: complete" in out


def test_fetch_all_without_date_exits_two(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(["--home", str(home), "fetch", "--all"])
    assert code == EXIT_USAGE
    assert "--date" in capsys.readouterr().err


def test_fetch_all_with_date_labels_the_run(tmp_path, capsys):
    home = tmp_path / "home"
    code = main(
        [
            "--home", str(home), "fetch", "--all",
            "--date", "2026-10-16",
            "--snapshots-dir", str(SNAPSHOTS),
        ]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "market" in out
    # The full market day is 1313 rows and 44 pages of 30.
    assert "requests 44" in out
