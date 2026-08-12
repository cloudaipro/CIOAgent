"""Offline coverage for the local Muse Glimmer provider."""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_muse_is_available_to_every_configured_chain():
    from cio.committee import models

    models.load_config.cache_clear()
    assert "muse" in models.SERVICES
    assert all(any(link["service"] == "muse" for link in links)
               for links in models.chains().values())
    assert models.model_catalog()["muse"] == [
        "muse-glimmer-30b-ud-q4-k-xl",
        "muse-glimmer-30b-nvfp4",
    ]


def test_muse_settings_env_overrides(monkeypatch, tmp_path):
    from cio.committee import models

    cfg = tmp_path / "models.yaml"
    cfg.write_text(
        "muse: {base_url: 'http://yaml:1/v1', api_key_env: MUSE_KEY, "
        "max_tokens: 111, timeout: 12, reasoning_strength: medium}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(cfg))
    monkeypatch.setenv("CIO_MUSE_BASE_URL", "http://env:2/v1")
    monkeypatch.setenv("CIO_MUSE_MAX_TOKENS", "222")
    monkeypatch.setenv("CIO_MUSE_TIMEOUT", "34")
    monkeypatch.setenv("CIO_MUSE_REASONING_STRENGTH", "xhigh")
    models.load_config.cache_clear()

    assert models.muse_settings() == {
        "base_url": "http://env:2/v1",
        "api_key_env": "MUSE_KEY",
        "max_output_tokens": 222,
        "timeout": 34,
        "reasoning_strength": "xhigh",
    }
    models.load_config.cache_clear()


def test_muse_reasoning_effort_defaults_to_xhigh(monkeypatch, tmp_path):
    from cio.committee import models

    cfg = tmp_path / "models.yaml"
    cfg.write_text("muse: {}\n", encoding="utf-8")
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(cfg))
    monkeypatch.delenv("CIO_MUSE_REASONING_STRENGTH", raising=False)
    models.load_config.cache_clear()

    assert models.muse_reasoning_effort() == "xhigh"
    assert models.muse_reasoning_effort("medium") == "medium"

    monkeypatch.setenv("CIO_MUSE_REASONING_STRENGTH", "low")
    assert models.muse_reasoning_effort("medium") == "low"
    models.load_config.cache_clear()


def test_ask_muse_uses_recommended_sampling_without_requiring_key(monkeypatch):
    from cio.committee import engine

    sent = {}

    class Response:
        is_success = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {"total_tokens": 42},
            }

    class Client:
        def __init__(self, **kwargs):
            sent["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            sent["url"] = url
            sent.update(kwargs)
            return Response()

    monkeypatch.delenv("CIO_MUSE_API_KEY", raising=False)
    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(engine, "muse_settings", lambda: {
        "base_url": "http://127.0.0.1:8001/v1",
        "api_key_env": "CIO_MUSE_API_KEY",
        "max_output_tokens": 2048,
        "timeout": 300,
        "reasoning_strength": "high",
    })

    text, tokens = _run(engine._ask_muse(
        "system", "user", "muse-local", reasoning_effort="high"))

    assert (text, tokens) == ("answer", 42)
    assert sent["url"] == "http://127.0.0.1:8001/v1/chat/completions"
    assert "Authorization" not in sent["headers"]
    assert sent["json"]["model"] == "muse-local"
    assert sent["json"]["temperature"] == 1.0
    assert sent["json"]["top_p"] == 0.95
    assert sent["json"]["top_k"] == 64
    assert sent["json"]["messages"][0]["content"].endswith(
        "Reasoning strength: high.")


def test_bot_chat_selects_muse_tool_runtime(monkeypatch):
    from cio import bot_runtime

    monkeypatch.setattr(bot_runtime.models, "bot_chat_chain", lambda: [
        {"service": "muse", "model": "muse-glimmer-30b-nvfp4",
         "reasoning_effort": "medium"},
    ])
    runtime = bot_runtime.select_runtime(chat_id=7701)

    assert type(runtime).__name__ == "OpenAIRuntime"
    assert runtime._service == "muse"
    assert runtime._model == "muse-glimmer-30b-nvfp4"
    assert runtime._reasoning_effort == "medium"


def test_dispatch_passes_muse_reasoning_effort(monkeypatch):
    from cio.committee import engine

    seen = {}

    async def fake_muse(system, user, model=None, reasoning_effort=None):
        seen["args"] = (system, user, model, reasoning_effort)
        return "ok", 1

    monkeypatch.setattr(engine, "_ask_muse", fake_muse)
    result = _run(engine._dispatch(
        "muse", "system", "user", "muse-local", reasoning_effort="low"))

    assert result == ("ok", 1)
    assert seen["args"] == ("system", "user", "muse-local", "low")


def test_muse_bot_model_settings_include_top_k(monkeypatch):
    from cio import agent_openai

    monkeypatch.setattr(agent_openai.models, "muse_settings", lambda: {
        "max_output_tokens": 4096,
    })
    settings = agent_openai._model_settings("muse")

    assert settings.temperature == 1.0
    assert settings.top_p == 0.95
    assert settings.max_tokens == 4096
    assert settings.extra_body == {"top_k": 64}
