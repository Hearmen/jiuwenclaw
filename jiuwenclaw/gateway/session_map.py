from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from jiuwenclaw.utils import get_checkpoint_dir, logger


def _make_key(provider: str, chat_id: str, bot_id: str, user_id: str) -> str:
    return f"{provider}::{chat_id}::{bot_id}::{user_id}"


def _make_session_id(provider: str, chat_id: str, bot_id: str, user_id: str) -> str:
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"{provider}_{chat_id}_{bot_id}_{user_id}_{ts}_{suffix}"


class SessionMap:
    """Simple mapping: (provider,chat_id,bot_id,user_id) -> session_id."""

    def __init__(self) -> None:
        self._store_path: Path = get_checkpoint_dir() / "session_map.json"
        self._mapping: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            if not self._store_path.exists():
                return
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._mapping = {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap load failed: %s", exc)

    def _save(self) -> None:
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                json.dump(self._mapping, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap save failed: %s", exc)

    def get_session_id(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        *,
        rotate: bool = False,
    ) -> str:
        key = _make_key(provider, chat_id, bot_id, user_id)
        existing = self._mapping.get(key)
        if existing and not rotate:
            return existing

        sid = _make_session_id(provider, chat_id, bot_id, user_id)
        if existing == sid:
            return sid
        self._mapping[key] = sid
        self._save()
        return sid
