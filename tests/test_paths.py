"""Path resolution: explicit > environment > cwd, and no implicit writes."""

from pathlib import Path

from cnwatch.paths import ENV_HOME, Paths, paths_for, resolve_home


def test_explicit_home_wins_over_environment(tmp_path):
    env = {ENV_HOME: str(tmp_path / "from-env")}
    resolved = resolve_home(
        home=str(tmp_path / "explicit"), env=env, cwd=tmp_path
    )
    assert resolved == (tmp_path / "explicit").resolve()


def test_relative_home_is_resolved_against_cwd(tmp_path):
    resolved = resolve_home(home="runtime", env={}, cwd=tmp_path)
    assert resolved == (tmp_path / "runtime").resolve()


def test_environment_fallback(tmp_path):
    env = {ENV_HOME: str(tmp_path / "from-env")}
    assert resolve_home(env=env, cwd=tmp_path) == (tmp_path / "from-env").resolve()


def test_cwd_fallback(tmp_path):
    assert resolve_home(env={}, cwd=tmp_path) == tmp_path.resolve()


def test_blank_environment_value_falls_back_to_cwd(tmp_path):
    assert resolve_home(env={ENV_HOME: "   "}, cwd=tmp_path) == tmp_path.resolve()


def test_derived_paths_stay_under_home(tmp_path):
    paths = Paths(home=tmp_path)
    for candidate in (
        paths.db_path,
        paths.out_dir,
        paths.reports_dir,
        paths.logs_dir,
        paths.snapshots_dir,
        paths.pdf_dir,
    ):
        assert tmp_path in candidate.parents


def test_ensure_runtime_dirs_does_not_touch_home_root(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    Paths(home=home).ensure_runtime_dirs()
    assert (home / "out" / "logs").is_dir()
    assert sorted(p.name for p in home.iterdir()) == ["out"]


def test_resolve_keeps_absolute_paths(tmp_path):
    absolute = tmp_path / "elsewhere" / "list.csv"
    assert Paths(home=tmp_path / "home").resolve(str(absolute)) == absolute.resolve()


def test_paths_for_uses_explicit_home(tmp_path):
    assert paths_for(str(tmp_path)).home == tmp_path.resolve()
