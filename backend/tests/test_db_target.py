"""
Database target resolution (app.core.db._resolve_db_target).

This exists because of a real incident: CLOUD_SQL_INSTANCE used to win
unconditionally, so exporting POSTGRES_DB_URL to aim a migration at a scratch
database did nothing, and the migration ran against Cloud SQL instead — no
error, no log line naming the target. The tests below pin the two properties
that would have prevented it: contradictory settings are refused, and the
escape hatch named in the refusal actually works.
"""

from __future__ import annotations

import logging

import pytest

from app.core import db as core_db
from app.core.db import DatabaseConfigError, _redact, _resolve_db_target

_INSTANCE = "proj:us-central1:palladium-db"
_URL = "postgresql://user:hunter2@localhost:5432/app"


@pytest.fixture
def cfg(monkeypatch):
    """Set the three settings that decide the target, independent of .env.

    POSTGRES_DB_URL rather than SQLALCHEMY_DATABASE_URI: the latter is a
    computed property, so patching the raw field also exercises the
    postgresql:// -> postgresql+psycopg:// rewrite the resolver sees.
    """

    def _set(instance=None, url=None, target=None):
        monkeypatch.setattr(core_db.settings, "CLOUD_SQL_INSTANCE", instance)
        monkeypatch.setattr(core_db.settings, "POSTGRES_DB_URL", url)
        monkeypatch.setattr(core_db.settings, "DB_TARGET", target)

    return _set


def test_cloud_sql_alone_resolves_to_the_connector(cfg):
    cfg(instance=_INSTANCE)
    assert _resolve_db_target() == "cloud_sql"


def test_url_alone_resolves_to_a_direct_connection(cfg):
    cfg(url=_URL)
    assert _resolve_db_target() == "url"


def test_both_set_is_refused_rather_than_guessed(cfg):
    """The incident itself: two contradictory instructions must not silently
    resolve in favour of the remote one."""
    cfg(instance=_INSTANCE, url=_URL)
    with pytest.raises(DatabaseConfigError) as exc:
        _resolve_db_target()
    message = str(exc.value)
    # Names both targets, so the reader can see which one they didn't mean.
    assert _INSTANCE in message
    assert "DB_TARGET=url" in message


def test_the_refusal_does_not_leak_the_password(cfg):
    cfg(instance=_INSTANCE, url=_URL)
    with pytest.raises(DatabaseConfigError) as exc:
        _resolve_db_target()
    assert "hunter2" not in str(exc.value)


def test_neither_set_is_refused_with_an_actionable_message(cfg):
    cfg()
    with pytest.raises(DatabaseConfigError) as exc:
        _resolve_db_target()
    assert "CLOUD_SQL_INSTANCE" in str(exc.value)
    assert "POSTGRES_DB_URL" in str(exc.value)


# --- the escape hatch named in the refusal -----------------------------------
# Load-bearing: Settings uses env_ignore_empty=True, so blanking a variable on
# the command line is discarded and the .env value survives. DB_TARGET is the
# only way to override without editing .env, which is exactly what someone
# aiming a migration at a scratch database needs.


def test_db_target_url_wins_over_a_configured_cloud_sql_instance(cfg):
    cfg(instance=_INSTANCE, url=_URL, target="url")
    assert _resolve_db_target() == "url"


def test_db_target_cloud_sql_wins_over_a_configured_url(cfg):
    cfg(instance=_INSTANCE, url=_URL, target="cloud_sql")
    assert _resolve_db_target() == "cloud_sql"


@pytest.mark.parametrize(
    "target,instance,url",
    [("url", _INSTANCE, None), ("cloud_sql", None, _URL)],
)
def test_db_target_pointing_at_something_unset_is_refused(cfg, target, instance, url):
    """Asking for a target that isn't configured is a typo, not a fallback."""
    cfg(instance=instance, url=url, target=target)
    with pytest.raises(DatabaseConfigError):
        _resolve_db_target()


# --- observability ------------------------------------------------------------


def test_the_chosen_target_is_logged(cfg, caplog):
    """The incident had no log line naming the target. Now every resolution
    says where it is about to connect."""
    cfg(url=_URL)
    with caplog.at_level(logging.INFO, logger="app.core.db"):
        _resolve_db_target()
    assert "direct URL" in caplog.text
    assert "hunter2" not in caplog.text


def test_redact_strips_credentials_but_keeps_the_host():
    out = _redact(_URL)
    assert "hunter2" not in out
    assert "localhost:5432/app" in out
