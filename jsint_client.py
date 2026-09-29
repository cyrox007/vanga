from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests


DEFAULT_TIMEOUT = 10


class JSIntDemoError(RuntimeError):
    """Ошибка взаимодействия демонстрационного клиента с jsint-site."""


@dataclass
class JSIntState:
    installation_id: str
    token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "token": self.token,
        }


class JSIntDemoClient:
    """Минимальный клиент Vanga для демонстрации активации и heartbeat."""

    def __init__(
        self,
        base_url: str,
        *,
        state_path: str | Path | None = None,
        version: str = "0.1-demo",
        version_code: int = 1,
        channel: str = "stable",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/demo/v1/vanga"
        self.state_path = Path(
            state_path
            or os.getenv("VANGA_JSINT_STATE")
            or Path.home() / ".vanga" / "jsint.json"
        )
        self.version = version
        self.version_code = version_code
        self.channel = channel
        self.timeout = timeout
        self.state = self._load_or_create_state()

    def _load_or_create_state(self) -> JSIntState:
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                installation_id = str(raw["installation_id"])
                token = raw.get("token")
                if token is not None:
                    token = str(token)
                return JSIntState(installation_id=installation_id, token=token)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise JSIntDemoError(
                    f"Не удалось прочитать состояние {self.state_path}: {exc}"
                ) from exc

        state = JSIntState(installation_id=str(uuid4()))
        self._save_state(state)
        return state

    def _save_state(self, state: JSIntState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(self.state_path, 0o600)
        except OSError:
            # Windows может не поддерживать POSIX-права — это не ошибка клиента.
            pass

    @staticmethod
    def _payload(response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise JSIntDemoError(
                f"jsint-site вернул не-JSON ответ: HTTP {response.status_code}"
            ) from exc
        if not response.ok:
            message = payload.get("message") or payload.get("error") or "unknown_error"
            raise JSIntDemoError(f"jsint-site: HTTP {response.status_code}: {message}")
        if not isinstance(payload, dict):
            raise JSIntDemoError("jsint-site вернул некорректный JSON")
        return payload

    def health(self) -> dict[str, Any]:
        response = requests.get(
            f"{self.api_url}/health",
            timeout=self.timeout,
        )
        return self._payload(response)

    def activate(
        self,
        *,
        activation_code: str | None = None,
        license_token: str | None = None,
    ) -> dict[str, Any]:
        if bool(activation_code) == bool(license_token):
            raise JSIntDemoError(
                "Нужно передать ровно один параметр: activation_code или license_token"
            )

        body: dict[str, Any] = {
            "installation_id": self.state.installation_id,
            "version": self.version,
            "version_code": self.version_code,
            "channel": self.channel,
        }
        if activation_code:
            body["activation_code"] = activation_code.strip()
        else:
            body["license_token"] = license_token.strip()

        response = requests.post(
            f"{self.api_url}/activate",
            json=body,
            timeout=self.timeout,
        )
        payload = self._payload(response)
        token = payload.get("token")
        if not isinstance(token, str) or len(token) != 64:
            raise JSIntDemoError("jsint-site не вернул credential после активации")

        self.state.token = token
        self._save_state(self.state)
        return payload

    def heartbeat(self) -> dict[str, Any]:
        if not self.state.token:
            raise JSIntDemoError("Vanga ещё не активирована")

        response = requests.post(
            f"{self.api_url}/heartbeat",
            headers={
                "Authorization": f"Bearer {self.state.token}",
                "X-JSInt-Installation": self.state.installation_id,
            },
            json={
                "version": self.version,
                "version_code": self.version_code,
                "channel": self.channel,
            },
            timeout=self.timeout,
        )
        return self._payload(response)
