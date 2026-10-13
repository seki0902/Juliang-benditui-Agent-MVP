"""Local model settings shared by the dashboard and daily runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

_LOCK = RLock()
_PROVIDERS = {
    "deepseek": ("DEEPSEEK_API_KEY", "deepseek_api_key", "https://api.deepseek.com"),
    "openai": ("OPENAI_API_KEY", "openai_api_key", "https://api.openai.com/v1"),
}


class ModelConfigStore:
    def __init__(self, root=None, *, environment=None):
        self.root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
        self.path = self.root / "state" / "model_settings.json"
        self.environment = os.environ if environment is None else environment

    def _read(self, *, include_legacy=True):
        values = {}
        for name in ([".env.txt"] if include_legacy else []) + [".env"]:
            legacy = self.root / name
            if legacy.exists():
                for line in legacy.read_text(encoding="utf-8-sig").splitlines():
                    name, sep, value = line.strip().partition("=")
                    if sep and name in {"AGENT_PROVIDER", "AGENT_MODEL", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"}:
                        values[name] = value.strip().strip("\"'")
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            values.update(saved)
        return values

    def _effective(self, *, include_legacy=True):
        values = self._read(include_legacy=include_legacy)
        for name in ("AGENT_PROVIDER", "AGENT_MODEL", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
            if self.environment.get(name):
                values[name] = self.environment[name].strip()
        return values

    def public(self):
        values = self._effective()
        provider = values.get("AGENT_PROVIDER", "deepseek")
        provider = provider if provider in _PROVIDERS else "deepseek"
        return {
            "provider": provider, "model": values.get("AGENT_MODEL", "") or "",
            "key_configured": bool(values.get(_PROVIDERS[provider][0])),
            "environment_override": any(self.environment.get(name) for name in (
                "AGENT_PROVIDER", "AGENT_MODEL", _PROVIDERS[provider][0])),
        }

    @staticmethod
    def _validate(payload):
        if not isinstance(payload, dict):
            raise ValueError("配置必须是 JSON 对象。")
        provider = payload.get("provider")
        if provider not in _PROVIDERS:
            raise ValueError("请选择 DeepSeek 或 OpenAI。")
        model, key = payload.get("model", ""), payload.get("api_key", "")
        if not isinstance(model, str) or not isinstance(key, str):
            raise ValueError("模型名和 Key 必须是文本。")
        model, key = model.strip(), key.strip()
        if not model or len(model) > 160 or any(char.isspace() for char in model):
            raise ValueError("请填写有效的模型名。")
        if len(key) > 1024 or any(char.isspace() for char in key):
            raise ValueError("Key 中不能包含空格或换行。")
        return provider, model, key

    def save(self, payload):
        provider, model, key = self._validate(payload)
        with _LOCK:
            values = self._read()
            values.update(AGENT_PROVIDER=provider, AGENT_MODEL=model)
            if key:
                values[_PROVIDERS[provider][0]] = key
            if not values.get(_PROVIDERS[provider][0]) and not self.environment.get(_PROVIDERS[provider][0]):
                raise ValueError("请先填写此服务商的 API Key。")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f".model-settings-{uuid4().hex}.tmp")
            try:
                temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
                temp.replace(self.path)
            finally:
                temp.unlink(missing_ok=True)
        return self.public()

    def overrides(self, *, include_legacy=True):
        values = self._effective(include_legacy=include_legacy)
        names = {"AGENT_PROVIDER": "agent_provider", "AGENT_MODEL": "agent_model",
                 "DEEPSEEK_API_KEY": "deepseek_api_key", "OPENAI_API_KEY": "openai_api_key"}
        return {field: values[name] for name, field in names.items() if values.get(name)}

    def test(self, payload):
        provider, model, key = self._validate(payload)
        key = key or self._effective().get(_PROVIDERS[provider][0])
        if not key:
            raise ValueError("请填写 API Key，或先保存此服务商的 Key。")
        endpoint = _PROVIDERS[provider][2] + "/chat/completions"
        body = {"model": model, "messages": [{"role": "user", "content": "Reply OK."}],
                "max_tokens": 1, "stream": False}
        request = Request(endpoint, json.dumps(body).encode("utf-8"), headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read(65536))
            if not isinstance(result, dict) or not result.get("choices"):
                return {"status": "fail", "message": "服务返回格式异常，请核对模型名。"}
            return {"status": "pass", "message": "连接成功，Key 和模型可用。", "model": model}
        except HTTPError as exc:
            messages = {
                401: "Key 无效或已失效，请更换后重试。", 403: "该 Key 无权访问此模型。",
                402: "账户余额不足，请在服务商平台检查余额。",
                404: "模型不存在，请核对模型名。", 429: "调用受限，请稍后重试。",
                400: "模型名或请求参数不受支持，请核对模型名。",
            }
            return {"status": "fail", "http_status": exc.code,
                    "message": messages.get(exc.code, "模型服务暂时不可用，请稍后重试。")}
        except (URLError, TimeoutError, OSError):
            return {"status": "fail", "message": "连接超时或网络不可用，请检查网络后重试。"}
        except (ValueError, UnicodeError):
            return {"status": "fail", "message": "服务返回格式异常，请稍后重试。"}
