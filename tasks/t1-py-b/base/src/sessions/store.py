"""An in-memory session store with TTL-based expiry.

Sessions are created with a time-to-live; callers can renew them (push the
expiry forward), revoke them (delete immediately), or ask whether a given
session has expired. All internal timestamps are timezone-aware UTC.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional


class SessionNotFound(KeyError):
    """Raised when a session id is not present in the store."""


@dataclass
class Session:
    session_id: str
    user_id: str
    created_at: datetime
    expires_at: datetime
    revoked: bool = False


class SessionStore:
    """A small in-memory session store.

    All stored timestamps (``created_at`` / ``expires_at``) are timezone
    aware, in UTC. Callers may pass an explicit ``now`` (also timezone
    aware) to any method for deterministic testing; when omitted, the
    store uses the real current time.
    """

    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def _current_time(self) -> datetime:
        return datetime.now(timezone.utc)

    def create_session(
        self,
        user_id: str,
        ttl_seconds: int,
        now: Optional[datetime] = None,
    ) -> Session:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = now if now is not None else self._current_time()
        session = Session(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            created_at=current,
            expires_at=current + timedelta(seconds=ttl_seconds),
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFound(session_id) from exc

    def is_expired(self, session_id: str, now: Optional[datetime] = None) -> bool:
        """Return True if the session is revoked or past its expiry.

        ``now`` may be supplied explicitly (timezone-aware) for
        deterministic tests. When omitted, the current wall-clock time is
        used.
        """
        session = self.get(session_id)
        if session.revoked:
            return True
        # BUG: falls back to a naive UTC timestamp instead of an
        # aware one. Comparing a naive datetime against the store's
        # timezone-aware `expires_at` raises TypeError ("can't compare
        # offset-naive and offset-aware datetimes") instead of returning
        # a boolean.
        current = now if now is not None else datetime.utcnow()
        return current > session.expires_at

    def renew(
        self,
        session_id: str,
        ttl_seconds: int,
        now: Optional[datetime] = None,
    ) -> Session:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        session = self.get(session_id)
        current = now if now is not None else self._current_time()
        session.expires_at = current + timedelta(seconds=ttl_seconds)
        return session

    def revoke(self, session_id: str) -> None:
        session = self.get(session_id)
        session.revoked = True

    def list_active(self, now: Optional[datetime] = None) -> List[Session]:
        current = now if now is not None else self._current_time()
        return [
            s
            for s in self._sessions.values()
            if not s.revoked and current <= s.expires_at
        ]

    def cleanup_expired(self, now: Optional[datetime] = None) -> int:
        """Remove expired or revoked sessions from the store; return the
        count removed."""
        current = now if now is not None else self._current_time()
        expired_ids = [
            sid
            for sid, s in self._sessions.items()
            if s.revoked or current > s.expires_at
        ]
        for sid in expired_ids:
            del self._sessions[sid]
        return len(expired_ids)

    def __len__(self) -> int:
        return len(self._sessions)
