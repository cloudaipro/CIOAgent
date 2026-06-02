"""
models.py — Per-agent model service config loader for the investment committee.

Usage:
    from cio.committee.models import load_config, resolve, nim_settings

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
        "moderator": {"service": "nim",    "model": "minimaxai/minimax-m2.7"},
        "cio":       {"service": "claude", "model": "claude-opus-4-8"},
    },
    "nim": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
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


def nim_settings() -> dict:
    """
    Return NIM connection settings: {base_url, api_key_env}.

    Falls back to hard-coded defaults if config is missing those keys.
    """
    cfg = load_config()
    nim: dict = cfg.get("nim", {})
    return {
        "base_url": nim.get("base_url", "https://integrate.api.nvidia.com/v1"),
        "api_key_env": nim.get("api_key_env", "NVIDIA_API_KEY"),
    }
