"""Tests for canvas_course: config lookup, extends, overrides, errors."""
import argparse
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
import canvas_course as cc  # noqa: E402


def _w(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))
    return p


def _args(**kw):
    ns = argparse.Namespace(course_id=None, course_config=None, year=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_find_config_walks_up(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 1})
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert cc.find_config(deep) == (tmp_path / cc.CONFIG_NAME).resolve()


def test_find_config_none(tmp_path):
    assert cc.find_config(tmp_path) is None


def test_resolve_reads_course_id(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 261062, "name": "X"})
    cfg = cc.resolve(_args(), start=tmp_path)
    assert cfg.course_id == 261062 and cfg.name == "X"


def test_flag_overrides_config(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 1})
    assert cc.resolve(_args(course_id=2), start=tmp_path).course_id == 2


def test_no_default_course(tmp_path, monkeypatch):
    monkeypatch.delenv(cc.ENV_VAR, raising=False)
    with pytest.raises(cc.CourseConfigError, match="no course id"):
        cc.resolve(_args(), start=tmp_path)


def test_env_var_config(tmp_path, monkeypatch):
    p = _w(tmp_path / "elsewhere.json", {"course_id": 7})
    monkeypatch.setenv(cc.ENV_VAR, str(p))
    assert cc.resolve(_args(), start=tmp_path).course_id == 7


def test_extends_merges_dicts_child_wins(tmp_path):
    _w(tmp_path / "base.json", {"course_id": 5, "folders": {"a": 1, "b": 2},
                                "repos": {"r": "~/x/{year}"}, "year": 2026})
    _w(tmp_path / "sub" / cc.CONFIG_NAME,
       {"extends": "../base.json", "folders": {"b": 9}})
    cfg = cc.resolve(_args(), start=tmp_path / "sub")
    assert cfg.course_id == 5
    assert cfg.folder_id("a") == 1 and cfg.folder_id("b") == 9
    assert cfg.repo_dirs("r")[0].endswith("/x/2026")


def test_extends_cycle(tmp_path):
    _w(tmp_path / "a.json", {"extends": "b.json"})
    _w(tmp_path / "b.json", {"extends": "a.json"})
    with pytest.raises(cc.CourseConfigError, match="cycle"):
        cc.load_config(tmp_path / "a.json")


def test_year_flag_fills_template(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 1, "repos": {"r": ["/p/{year}", "/q"]}})
    cfg = cc.resolve(_args(year=2027), start=tmp_path)
    assert cfg.repo_dirs("r") == ["/p/2027", "/q"]


def test_year_missing_is_error(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 1, "repos": {"r": "/p/{year}"}})
    with pytest.raises(cc.CourseConfigError, match="year"):
        cc.resolve(_args(), start=tmp_path).repo_dirs("r")


def test_missing_folder_and_repo_are_errors(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 1})
    cfg = cc.resolve(_args(), start=tmp_path)
    with pytest.raises(cc.CourseConfigError):
        cfg.folder_id("recitation")
    with pytest.raises(cc.CourseConfigError):
        cfg.repo_dirs("recitations")


def test_bad_json(tmp_path):
    (tmp_path / cc.CONFIG_NAME).write_text("{nope")
    with pytest.raises(cc.CourseConfigError, match="invalid JSON"):
        cc.resolve(_args(), start=tmp_path)


def test_connect_reads_token_and_base(tmp_path):
    tf = tmp_path / "tok"
    tf.write_text("abc123\n")
    cfg = cc.CourseConfig({"base_url": "https://x.example/"}, course_id=1)
    assert cc.connect(cfg, token_file=str(tf), profile={}) == ("https://x.example", "abc123")


def test_connect_profile_fallback(tmp_path):
    tf = tmp_path / "tok"
    tf.write_text("zzz")
    cfg = cc.CourseConfig({}, course_id=1)
    base, tok = cc.connect(cfg, profile={"base_url": "https://p.example", "token_file": str(tf)})
    assert (base, tok) == ("https://p.example", "zzz")


def test_connect_errors(tmp_path):
    cfg = cc.CourseConfig({}, course_id=1)
    with pytest.raises(cc.CourseConfigError, match="base_url"):
        cc.connect(cfg, profile={})
    cfg2 = cc.CourseConfig({"base_url": "https://x"}, course_id=1)
    with pytest.raises(cc.CourseConfigError, match="cannot read token"):
        cc.connect(cfg2, token_file=str(tmp_path / "missing"), profile={})
    e = tmp_path / "empty"
    e.write_text("\n")
    with pytest.raises(cc.CourseConfigError, match="empty"):
        cc.connect(cfg2, token_file=str(e), profile={})


def test_setup_exits_cleanly_without_course(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(cc.ENV_VAR, raising=False)
    with pytest.raises(SystemExit) as e:
        cc.setup(_args())
    assert "[FAIL]" in str(e.value.code) and "no course id" in str(e.value.code)


def test_path_template(tmp_path):
    _w(tmp_path / cc.CONFIG_NAME, {"course_id": 1, "year": 2026,
                                   "paths": {"tsv": "/s/F.{year}/sched_{year}.tsv"}})
    cfg = cc.resolve(_args(), start=tmp_path)
    assert cfg.path("tsv") == "/s/F.2026/sched_2026.tsv"
    assert cfg.path("tsv", year=2027) == "/s/F.2027/sched_2027.tsv"
    with pytest.raises(cc.CourseConfigError):
        cfg.path("nope")


def test_find_config_via_invoked_script_dir(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _w(repo / cc.CONFIG_NAME, {"course_id": 9})
    (repo / "bin").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys, "argv", [str(repo / "bin" / "tool.py")])
    assert cc.find_config() == (repo / cc.CONFIG_NAME).resolve()


def test_find_config_explicit_start_does_not_use_script_dir(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _w(repo / cc.CONFIG_NAME, {"course_id": 9})
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setattr(sys, "argv", [str(repo / "tool.py")])
    assert cc.find_config(other) is None


def test_use_config_override(tmp_path, monkeypatch):
    a = _w(tmp_path / "a" / cc.CONFIG_NAME, {"course_id": 1})
    b = _w(tmp_path / "b.json", {"course_id": 2})
    monkeypatch.chdir(tmp_path / "a")
    try:
        assert cc.find_config() == a.resolve()
        cc.use_config(b)
        assert cc.find_config() == b.resolve()
        assert cc.find_config(tmp_path / "a") == a.resolve()  # explicit start ignores it
    finally:
        cc.use_config(None)
    assert cc.find_config() == a.resolve()
