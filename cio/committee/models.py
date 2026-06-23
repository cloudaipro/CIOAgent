"""
models.py — Per-agent model service config loader for the investment committee.

Usage:
    from cio.committee.models import (
        load_config, resolve, resolve_chain, chains, chain_names, resolve_chain_name,
        nim_settings, openai_settings,
    )

``load_config`` is cached per (path, file mtime), so edits saved from the
dashboard (a separate process) are picked up on the next call without a bot
restart.  Call ``load_config.cache_clear()`` in tests to swap configs.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in defaults (identical to config/committee_models.yaml)
# ---------------------------------------------------------------------------

_BUILTIN: dict[str, Any] = {
    # Named fallback-chain settings. Every agent references one of these by
    # name (agents.<key>.chain: <name>); ask_role walks the links in order,
    # skipping any link whose service is over its daily token budget or that
    # returns an empty result (key missing / API error / limit notice).
    "chains": {
        # premium: paid head → subscription → cheap last resort. For the
        # critical decision/briefing roles (cio, wma).
        "premium": [
            {"service": "openai", "model": "gpt-5.5-2026-04-23", "daily_limit": 200000},
            {"service": "claude", "model": "claude-opus-4-8",    "daily_limit": 200000},
            {"service": "nim",    "model": "moonshotai/kimi-k2.6"},  # last resort
        ],
        # standard: subscription head (same primary the specialists always
        # used), degrading to paid then cheap.
        "standard": [
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "openai", "model": "gpt-5.5-2026-04-23", "daily_limit": 200000},
            {"service": "nim",    "model": "moonshotai/kimi-k2.6"},
        ],
        # translation: sonnet head (reliable long-markdown TC), opus backup.
        "translation": [
            {"service": "claude", "model": "claude-sonnet-4-6"},
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "nim",    "model": "moonshotai/kimi-k2.6"},
        ],
    },
    "defaults": {"chain": "standard"},
    "agents": {
        "market":     {"chain": "standard"},
        "macro":      {"chain": "standard"},
        "equity":     {"chain": "standard"},
        "industry":   {"chain": "standard"},
        "valuation":  {"chain": "standard"},
        "quant":      {"chain": "standard"},
        "etf":        {"chain": "standard"},
        "risk":       {"chain": "standard"},
        "catalyst":   {"chain": "standard"},
        "moderator":  {"chain": "standard"},
        "cio":        {"chain": "premium"},
        "wma":        {"chain": "premium"},
        "translator": {"chain": "translation"},
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

# Services the dashboard Configure tab offers in its service combo box.
SERVICES = ("claude", "nim", "openai")

# Fallback model catalog per service for the Configure tab. The live catalog is
# read from ``model_catalog:`` in committee_models.yaml (editable via the dashboard);
# this constant only seeds services the yaml omits. Free text is always allowed.
MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "claude": [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "nim": [
        "nvidia/nemotron-3-ultra-550b-a55b",
        "minimaxai/minimax-m2.7", "moonshotai/kimi-k2.6",
        "deepseek-ai/deepseek-r1", "qwen/qwen3-235b-a22b",
        "meta/llama-3.3-70b-instruct", "nvidia/llama-3.3-nemotron-super-49b-v1",
    ],
    "openai": ["gpt-5.5-2026-04-23", "gpt-5.1", "gpt-5", "o4-mini", "gpt-4.1"],
}


# load_config cache: resolved path -> (file mtime, parsed dict). Keyed on mtime
# so a save from the dashboard process is seen here on the next call — the bot
# and the dashboard are separate processes, so an in-process cache_clear() in
# one cannot reach the other.
_CONFIG_CACHE: dict[str, tuple[float, dict]] = {}


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
        mtime = os.path.getmtime(resolved)
    except OSError:
        mtime = -1.0
    cached = _CONFIG_CACHE.get(resolved)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        import yaml  # pyyaml is a dep
        with open(resolved, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError("config root is not a dict")
    except FileNotFoundError:
        log.debug("committee_models.yaml not found at %s; using built-in defaults", resolved)
        data = _BUILTIN
    except Exception as exc:
        log.warning("Failed to parse committee_models.yaml (%s): %s; using built-in defaults", resolved, exc)
        data = _BUILTIN
    _CONFIG_CACHE[resolved] = (mtime, data)
    return data


def _config_cache_clear() -> None:
    _CONFIG_CACHE.clear()


# Keep the lru_cache-era API: tests and write_doc() call load_config.cache_clear().
load_config.cache_clear = _config_cache_clear  # type: ignore[attr-defined]


def resolve(role_key: str) -> tuple[str, str | None]:
    """
    Return (service, model) for the given agent role_key.

    With the named-chain mechanism this is the HEAD link of the agent's
    fallback chain. Legacy inline {service, model} agent configs are still
    honoured. Falls back to ('claude', 'claude-opus-4-8'). Never raises.
    """
    chain = resolve_chain(role_key)
    if chain:
        head = chain[0]
        return str(head.get("service") or "claude"), head.get("model")
    return ("claude", "claude-opus-4-8")


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


def _normalize_links(raw) -> list[dict]:
    """Normalise a raw chain link list: each link gets service + model, and
    daily_limit only when set. Non-dict links are skipped. Never raises."""
    result: list[dict] = []
    if not isinstance(raw, list):
        return result
    for link in raw:
        if not isinstance(link, dict):
            continue
        svc = link.get("service") or "claude"
        mdl = link.get("model") or "claude-opus-4-8"
        entry: dict = {"service": str(svc), "model": mdl}
        if "daily_limit" in link and link["daily_limit"] is not None:
            try:
                entry["daily_limit"] = int(link["daily_limit"])
            except (TypeError, ValueError):
                pass
        result.append(entry)
    return result


def chains() -> dict[str, list[dict]]:
    """Return all named fallback-chain settings {name: [link, ...]}, normalized.

    Read from ``chains:`` in the config; falls back to the built-in chains when
    the section is missing/empty so resolution never dead-ends. Never raises.
    """
    cfg = load_config()
    raw = cfg.get("chains")
    out: dict[str, list[dict]] = {}
    if isinstance(raw, dict):
        for name, links in raw.items():
            norm = _normalize_links(links)
            if norm:
                out[str(name)] = norm
    if not out:
        for name, links in _BUILTIN["chains"].items():
            out[name] = _normalize_links(links)
    return out


def chain_names() -> list[str]:
    """Names of every configured fallback-chain setting (config order)."""
    return list(chains().keys())


def resolve_chain_name(role_key: str) -> str | None:
    """Return the chain-setting NAME the agent resolves to, or None when the
    agent uses a legacy inline config (list chain / service+model)."""
    cfg = load_config()
    agent_cfg = (cfg.get("agents") or {}).get(role_key) or {}
    val = agent_cfg.get("chain")
    if isinstance(val, str):
        return val
    if val is None and "service" not in agent_cfg and "model" not in agent_cfg:
        dval = (cfg.get("defaults") or {}).get("chain")
        if isinstance(dval, str):
            return dval
    return None


def resolve_chain(role_key: str) -> list[dict]:
    """
    Return the fallback chain for *role_key* as a list of link dicts.

    Each link has at minimum ``service`` and ``model``; links may carry
    ``daily_limit``. Resolution order:
      1. ``agents.<role_key>.chain: <name>`` → the named setting in ``chains:``
         (unknown name → warn, fall through to defaults).
      2. ``agents.<role_key>.chain: [links]`` → legacy inline chain.
      3. ``agents.<role_key>.{service, model}`` → legacy 1-link chain.
      4. ``defaults.chain`` (name or inline list).
      5. ``defaults.{service, model}`` → 1-link chain.
      6. hard-coded [{claude, claude-opus-4-8}].
    Never raises; never returns an empty list.
    """
    cfg = load_config()
    agents: dict = cfg.get("agents") or {}
    defaults: dict = cfg.get("defaults") or {}
    agent_cfg = agents.get(role_key) or {}
    named = chains()

    def _from_node(node: dict, label: str) -> list[dict] | None:
        """Resolve one config node (agent or defaults) to a chain, or None."""
        val = node.get("chain")
        if isinstance(val, str):
            links = named.get(val)
            if links:
                return links
            log.warning("resolve_chain(%s): unknown chain setting %r in %s; falling through",
                        role_key, val, label)
            return None
        if isinstance(val, list):  # legacy inline chain
            norm = _normalize_links(val)
            return norm or None
        if "service" in node or "model" in node:  # legacy single service
            svc = node.get("service") or defaults.get("service") or "claude"
            mdl = node.get("model", defaults.get("model", "claude-opus-4-8"))
            if mdl is None or (isinstance(mdl, str) and mdl.lower() in ("null", "none", "~")):
                mdl = None
            return [{"service": str(svc), "model": mdl}]
        return None

    result = _from_node(agent_cfg, f"agents.{role_key}") or _from_node(defaults, "defaults")
    return result or [{"service": "claude", "model": "claude-opus-4-8"}]


def new_chain_links() -> list[dict]:
    """Template links for a chain setting created from the Configure tab:
    3 services (claude → openai → nim), editable after creation."""
    return [
        {"service": "claude", "model": "claude-opus-4-8"},
        {"service": "openai", "model": "gpt-5.5-2026-04-23", "daily_limit": 200000},
        {"service": "nim", "model": "moonshotai/kimi-k2.6"},
    ]


# ---------------------------------------------------------------------------
# Editing IO (used by the dashboard Configure tab)
# ---------------------------------------------------------------------------

def config_path() -> str:
    """Filesystem path the Configure tab reads/writes (env > repo default)."""
    return os.getenv("CIO_MODELS_CONFIG") or str(_REPO_CONFIG)


def model_catalog() -> dict[str, list[str]]:
    """Per-service model-name suggestions for the Configure tab.

    Live source is ``model_catalog:`` in the yaml (user-editable via the dashboard);
    any service the yaml omits falls back to the built-in ``MODEL_SUGGESTIONS`` so
    the dropdowns are never empty. Result is keyed by every service in ``SERVICES``.
    """
    cfg = load_config()
    cat = cfg.get("model_catalog")
    result: dict[str, list[str]] = {s: list(MODEL_SUGGESTIONS.get(s, [])) for s in SERVICES}
    if isinstance(cat, dict):
        for svc, mods in cat.items():
            if isinstance(mods, list):
                result[svc] = [str(m) for m in mods if m]
    return result


def _yaml_rt():
    """Round-trip YAML handler (ruamel) — preserves comments and flow style.
    Raises ImportError if ruamel is not installed (caller falls back to pyyaml)."""
    from ruamel.yaml import YAML
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096  # don't wrap long lines (urls, flow maps)
    return y


def read_doc(path: str | None = None):
    """Load the config for *editing*.

    Prefers ruamel round-trip (keeps comments + inline ``{flow: maps}``). Falls
    back to a plain pyyaml dict (edits then drop comments on save). If no file
    exists yet, returns a deep copy of the effective config so the form still
    renders and the first save materialises the file.
    """
    resolved = path or config_path()
    if not os.path.exists(resolved):
        import copy
        return copy.deepcopy(load_config())
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            return _yaml_rt().load(fh)
    except ImportError:
        import yaml
        with open(resolved, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)


def write_doc(doc, path: str | None = None) -> None:
    """Persist an edited config doc, then clear the load_config cache so the new
    values take effect on the next resolve()."""
    resolved = path or config_path()
    try:
        with open(resolved, "w", encoding="utf-8") as fh:
            _yaml_rt().dump(doc, fh)
    except ImportError:
        import yaml
        with open(resolved, "w", encoding="utf-8") as fh:
            yaml.safe_dump(dict(doc), fh, sort_keys=False, default_flow_style=False,
                           allow_unicode=True)
    load_config.cache_clear()
