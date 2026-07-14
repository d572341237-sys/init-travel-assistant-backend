from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from threading import RLock
from time import monotonic
from typing import Any

from app.repositories.session_repository import (
    delete_session_state,
    get_session_state,
    upsert_session_state,
)


@dataclass
class SessionState:
    pending_attraction_choices: dict[str, Any] | None = None
    last_route_context: dict[str, Any] | None = None
    updated_at: float = field(default_factory=monotonic)


class SessionStore:
    def __init__(self, ttl_seconds: int = 7200, max_sessions: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[str, SessionState] = {}
        self._lock = RLock()

    def get_pending_attraction_choices(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._get_active_state(thread_id)
            if not state:
                return None
            return state.pending_attraction_choices

    def set_pending_attraction_choices(
        self,
        thread_id: str,
        value: dict[str, Any],
    ) -> None:
        with self._lock:
            state = self._get_or_create_state(thread_id)
            state.pending_attraction_choices = value
            state.updated_at = monotonic()
            upsert_session_state(thread_id, pending_attraction_choices=value)
            self._prune_locked()

    def clear_pending_attraction_choices(self, thread_id: str) -> None:
        with self._lock:
            state = self._get_active_state(thread_id)
            if not state:
                return
            state.pending_attraction_choices = None
            state.updated_at = monotonic()
            upsert_session_state(thread_id, pending_attraction_choices=None)

    def get_last_route_context(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._get_active_state(thread_id)
            if not state:
                return None
            return state.last_route_context

    def set_last_route_context(
        self,
        thread_id: str,
        value: dict[str, Any],
    ) -> None:
        with self._lock:
            state = self._get_or_create_state(thread_id)
            state.last_route_context = value
            state.updated_at = monotonic()
            upsert_session_state(thread_id, last_route_context=value)
            self._prune_locked()

    def session_count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._sessions)

    def _get_or_create_state(self, thread_id: str) -> SessionState:
        self._prune_locked()
        state = self._sessions.get(thread_id)
        if not state:
            state = SessionState()
            self._sessions[thread_id] = state
        return state

    def _get_active_state(self, thread_id: str) -> SessionState | None:
        self._prune_locked()
        state = self._sessions.get(thread_id)
        if not state:
            persisted = get_session_state(thread_id)
            if not persisted:
                return None
            state = SessionState(
                pending_attraction_choices=persisted.get("pending_attraction_choices"),
                last_route_context=persisted.get("last_route_context"),
            )
            self._sessions[thread_id] = state
        state.updated_at = monotonic()
        return state

    def _prune_locked(self) -> None:
        now = monotonic()
        expired_thread_ids = [
            thread_id
            for thread_id, state in self._sessions.items()
            if now - state.updated_at > self.ttl_seconds
        ]
        for thread_id in expired_thread_ids:
            self._sessions.pop(thread_id, None)
            delete_session_state(thread_id)

        overflow = len(self._sessions) - self.max_sessions
        if overflow <= 0:
            return

        oldest_thread_ids = sorted(
            self._sessions,
            key=lambda thread_id: self._sessions[thread_id].updated_at,
        )[:overflow]
        for thread_id in oldest_thread_ids:
            self._sessions.pop(thread_id, None)
            delete_session_state(thread_id)


@lru_cache
def get_session_store() -> SessionStore:
    return SessionStore()
