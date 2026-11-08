"""Databases created while the app was still called SubForge Studio must survive the rename."""
import sqlite3

import pytest

from app.core import config, database


@pytest.fixture
def default_db(tmp_path, monkeypatch):
    new = tmp_path / "subbuild.db"
    monkeypatch.setattr(config.settings, "database_url", f"sqlite:///{new.as_posix()}")
    return new


def _make_db(path, marker):
    con = sqlite3.connect(path)
    con.execute("create table t (v text)")
    con.execute("insert into t values (?)", (marker,))
    con.commit()
    con.close()


def test_old_database_is_renamed(default_db):
    old = default_db.with_name("subforge.db")
    _make_db(old, "kept")
    database._adopt_legacy_database()
    assert default_db.is_file() and not old.exists()
    assert sqlite3.connect(default_db).execute("select v from t").fetchone() == ("kept",)


def test_existing_new_database_is_never_overwritten(default_db):
    old = default_db.with_name("subforge.db")
    _make_db(old, "old")
    _make_db(default_db, "new")
    database._adopt_legacy_database()
    assert old.is_file()
    assert sqlite3.connect(default_db).execute("select v from t").fetchone() == ("new",)


def test_custom_database_url_is_left_alone(tmp_path, monkeypatch):
    custom = tmp_path / "mine.db"
    old = tmp_path / "subforge.db"
    _make_db(old, "x")
    monkeypatch.setattr(config.settings, "database_url", f"sqlite:///{custom.as_posix()}")
    database._adopt_legacy_database()
    assert old.is_file() and not custom.exists()
