"""
models.py — Per-agent model service config loader for the investment committee.

Usage:
    from cio.committee.models import load_config, resolve, nim_settings, openai_settings, resolve_chain

``load_config`` is lru_cached.  Call ``load_config.cache_clear()`` in tests to swap configs.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in defaults (identical to config/committee_models.yaml)
# ---------------------------------------------------------------------------

_BUILTIN: dict[str, Any] = {
    "defaults": {"service": "nim", "model": "minimaxai/minimax-m2.7"},
    "agents": {
        "market":    {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "equity":    {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "industry":  {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "valuation": {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "quant":     {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "etf":       {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "risk":      {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "catalyst":  {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "moderator":  {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "cio": {
            "chain": [
                {"service": "openai", "model": "gpt-5.5-2026-04-23",    "daily_limit": 200000},
                {"service": "claude", "model": "claude-opus-4-8",         "daily_limit": 200000},
                {"service": "nim",    "model": "minimaxai/minimax-m2.7"},  # last resort
            ]
        },
        "wma": {
            "chain": [
                {"service": "openai", "model": "gpt-5.5-2026-04-23",    "daily_limit": 200000},
                {"service": "claude", "model": "claude-opus-4-8",         "daily_limit": 200000},
                {"service": "nim",    "model": "moonshotai/kimi-k2.6"},  # last resort
            ]
        },
        "translator": {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
    },
    "nim": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
}

# Repo-relative config path (resolved once at module import)
_REPO_CONFIG = Path(__file__).parent.parent.parent / "config" / "committee_models.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    """
    Load and return the committee models config dict.

    Resolution order:
      1. explicit ``path`` argument
      2. ``CIO_MODELS_CONFIG`` env var
      3. repo ``config/committee_models.yaml``
      4. built-in defaults (if file missing or unparseable — never raises)
    """
    resolved = path or os.getenv("CIO_MODELS_CONFIG") or str(_REPO_CONFIG)
    try:
        import yaml  # pyyaml is a dep
        with open(resolved, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError("config root is not a dict")
        return data
    except FileNotFoundError:
        log.debug("committee_models.yaml not found at %s; using built-in defaults", resolved)
        return _BUILTIN
    except Exception as exc:
        log.warning("Failed to parse committee_models.yaml (%s): %s; using built-in defaults", resolved, exc)
        return _BUILTIN


def resolve(role_key: str) -> tuple[str, str | None]:
    """
    Return (service, model) for the given agent role_key.

    Falls back to config defaults, then hard-coded ('nim', 'minimaxai/minimax-m2.7').
    Never raises.
    """
    cfg = load_config()
    agents: dict = cfg.get("agents", {})
    defaults: dict = cfg.get("defaults", {})

    agent_cfg = agents.get(role_key, {})
    service = agent_cfg.get("service") or defaults.get("service") or "nim"

    # Use a sentinel to distinguish explicit null from missing key.
    # If the agent explicitly sets model: null → honour it (None).
    # If the agent key is absent → fall through to defaults.
    _MISSING = object()
    raw_model = agent_cfg.get("model", _MISSING)
    if raw_model is _MISSING:
        # Key not present in agent config — use defaults
        model = defaults.get("model") or "minimaxai/minimax-m2.7"
    else:
        # Key present (may be null/None)
        model = raw_model

    # Normalise yaml null / "null" / "none" / "~" strings → Python None
    if model is None or (isinstance(model, str) and model.lower() in ("null", "none", "~")):
        model = None

    return str(service), model


def _int_setting(env_name: str, yaml_val, default: int) -> int:
    """Resolve an int knob: env var > yaml value > default. Bad value → default."""
    raw = os.getenv(env_name)
    if raw is None:
        raw = yaml_val
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        log.warning("%s: bad int %r; using %d", env_name, raw, default)
        return default


def nim_settings() -> dict:
    """
    Return NIM connection settings: {base_url, api_key_env, max_output_tokens}.

    The output cap is configurable, priority env > yaml > default(2048):
      env CIO_NIM_MAX_TOKENS, yaml nim.max_tokens.
    Falls back to hard-coded defaults if config is missing those keys.
    """
    cfg = load_config()
    nim: dict = cfg.get("nim", {})
    return {
        "base_url": nim.get("base_url", "https://integrate.api.nvidia.com/v1"),
        "api_key_env": nim.get("api_key_env", "NVIDIA_API_KEY"),
        "max_output_tokens": _int_setting("CIO_NIM_MAX_TOKENS", nim.get("max_tokens"), 2048),
    }


def claude_settings() -> dict:
    """
    Return Claude (claude-agent-sdk) settings: {max_thinking_tokens}.

    The agentic SDK has no plain output `max_tokens` param; its only token knob is
    the thinking-token budget. Configurable, priority env > yaml, default None
    (= SDK default, unchanged behaviour):
      env CIO_CLAUDE_MAX_THINKING_TOKENS, yaml claude.max_thinking_tokens.
    """
    cfg = load_config()
    cl: dict = cfg.get("claude", {})
    raw = os.getenv("CIO_CLAUDE_MAX_THINKING_TOKENS")
    if raw is None:
        raw = cl.get("max_thinking_tokens")
    val: int | None = None
    if raw is not None:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            log.warning("claude_settings: bad max_thinking_tokens %r; ignoring", raw)
    return {"max_thinking_tokens": val}


def openai_settings() -> dict:
    """
    Return OpenAI connection settings:
        {base_url, api_key_env, max_output_tokens, token_param}.

    ``token_param`` is the name of the output-cap parameter: gpt-5.x wants
    ``max_completion_tokens``; older chat models want ``max_tokens``. Both that
    name and the cap value are configurable, priority: env > yaml > default.
      env:  CIO_OPENAI_TOKEN_PARAM, CIO_OPENAI_MAX_TOKENS
      yaml: openai.token_param, openai.max_tokens
    Falls back to hard-coded defaults if config is missing those keys.
    """
    cfg = load_config()
    oa: dict = cfg.get("openai", {})

    token_param = os.getenv("CIO_OPENAI_TOKEN_PARAM") or oa.get("token_param", "max_completion_tokens")
    if token_param not in ("max_completion_tokens", "max_tokens"):
        log.warning("openai_settings: unknown token_param %r; using max_completion_tokens", token_param)
        token_param = "max_completion_tokens"

    return {
        "base_url": oa.get("base_url", "https://api.openai.com/v1"),
        "api_key_env": oa.get("api_key_env", "OPENAI_API_KEY"),
        "max_output_tokens": _int_setting("CIO_OPENAI_MAX_TOKENS", oa.get("max_tokens"), 2048),
        "token_param": token_param,
    }


def resolve_chain(role_key: str) -> list[dict]:
    """
    Return the fallback chain for *role_key* as a list of link dicts.

    Each link has at minimum ``service`` and ``model``; CIO links also carry
    ``daily_limit``.  If the agent config has a ``chain`` key, return it
    directly.  Otherwise wrap ``resolve(role_key)`` into a single-link list
    (no ``daily_limit`` — specialists have no budget cap).  Never raises.
    """
    cfg = load_config()
    agents: dict = cfg.get("agents", {})
    agent_cfg = agents.get(role_key, {})

    if "chain" in agent_cfg:
        raw: list = agent_cfg["chain"]
        # Normalise: each link must have service + model at minimum.
        result = []
        for link in raw:
            if not isinstance(link, dict):
                continue
            svc = link.get("service") or "nim"
            mdl = link.get("model") or "minimaxai/minimax-m2.7"
            entry: dict = {"service": str(svc), "model": mdl}
            if "daily_limit" in link and link["daily_limit"] is not None:
                entry["daily_limit"] = int(link["daily_limit"])
            result.append(entry)
        return result

    # Single-service role → 1-link chain, no limit.
    service, model = resolve(role_key)
    return [{"service": service, "model": model}]
