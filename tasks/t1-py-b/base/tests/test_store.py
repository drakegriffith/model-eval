import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from sessions import SessionStore
from sessions.store import SessionNotFound

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_create_session_sets_expiry():
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=60, now=EPOCH)
    assert s.expires_at == EPOCH + timedelta(seconds=60)
    assert s.user_id == "u1"
    assert not s.revoked


def test_create_session_rejects_nonpositive_ttl():
    store = SessionStore()
    with pytest.raises(ValueError):
        store.create_session("u1", ttl_seconds=0, now=EPOCH)


def test_get_unknown_session_raises():
    store = SessionStore()
    with pytest.raises(SessionNotFound):
        store.get("does-not-exist")


def test_is_expired_false_before_expiry_explicit_now():
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=60, now=EPOCH)
    check_time = EPOCH + timedelta(seconds=30)
    assert store.is_expired(s.session_id, now=check_time) is False


def test_is_expired_true_after_expiry_explicit_now():
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=60, now=EPOCH)
    check_time = EPOCH + timedelta(seconds=61)
    assert store.is_expired(s.session_id, now=check_time) is True


def test_is_expired_default_now_does_not_raise():
    """The store must be able to answer is_expired using the real
    current clock (no explicit `now`), not just in tests that pin the
    clock. A fresh 5-minute session checked right away must report
    False, never raise."""
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=300)
    assert store.is_expired(s.session_id) is False


def test_revoked_session_is_always_expired():
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=300, now=EPOCH)
    store.revoke(s.session_id)
    assert store.is_expired(s.session_id, now=EPOCH) is True


def test_renew_pushes_expiry_forward():
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=60, now=EPOCH)
    later = EPOCH + timedelta(seconds=50)
    store.renew(s.session_id, ttl_seconds=120, now=later)
    assert s.expires_at == later + timedelta(seconds=120)
    assert store.is_expired(s.session_id, now=later) is False


def test_renew_rejects_nonpositive_ttl():
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=60, now=EPOCH)
    with pytest.raises(ValueError):
        store.renew(s.session_id, ttl_seconds=-5, now=EPOCH)


def test_list_active_excludes_expired_and_revoked():
    store = SessionStore()
    live = store.create_session("live", ttl_seconds=100, now=EPOCH)
    store.create_session("expiring", ttl_seconds=10, now=EPOCH)
    revoked = store.create_session("revoked-user", ttl_seconds=100, now=EPOCH)
    store.revoke(revoked.session_id)

    check_time = EPOCH + timedelta(seconds=50)
    active = store.list_active(now=check_time)
    active_ids = {s.session_id for s in active}

    assert active_ids == {live.session_id}


def test_cleanup_expired_removes_and_counts():
    store = SessionStore()
    store.create_session("keep", ttl_seconds=1000, now=EPOCH)
    store.create_session("gone1", ttl_seconds=5, now=EPOCH)
    store.create_session("gone2", ttl_seconds=5, now=EPOCH)

    check_time = EPOCH + timedelta(seconds=30)
    removed = store.cleanup_expired(now=check_time)

    assert removed == 2
    assert len(store) == 1


def test_multiple_sessions_are_independent():
    store = SessionStore()
    a = store.create_session("a", ttl_seconds=10, now=EPOCH)
    b = store.create_session("b", ttl_seconds=1000, now=EPOCH)
    check_time = EPOCH + timedelta(seconds=20)
    assert store.is_expired(a.session_id, now=check_time) is True
    assert store.is_expired(b.session_id, now=check_time) is False


def test_session_boundary_exact_expiry_is_expired():
    """A check at exactly expires_at counts as expired (strict >, i.e.
    the session is valid up to and including the instant of expiry)."""
    store = SessionStore()
    s = store.create_session("u1", ttl_seconds=60, now=EPOCH)
    assert store.is_expired(s.session_id, now=EPOCH + timedelta(seconds=60)) is False
    assert store.is_expired(s.session_id, now=EPOCH + timedelta(seconds=60, microseconds=1)) is True
