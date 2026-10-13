"""Token storage contracts.

The scheduled pipeline must persist rotated refresh tokens. The environment
store below is intentionally read-only and is suitable only for a smoke test.
Production deployment should inject a persistent secret-backed implementation.
"""

from __future__ import annotations

from typing import Protocol


class TokenStore(Protocol):
    def get_refresh_token(self) -> str | None:
        ...

    def save_refresh_token(self, refresh_token: str) -> None:
        ...


class EnvTokenStore:
    def __init__(self, refresh_token: str | None) -> None:
        self._refresh_token = refresh_token

    def get_refresh_token(self) -> str | None:
        return self._refresh_token

    def save_refresh_token(self, refresh_token: str) -> None:
        raise RuntimeError(
            "EnvTokenStore 不支持保存轮换后的 refresh_token；"
            "请接入 PostgreSQL、Docker Secret 或密钥管理工具。"
        )
