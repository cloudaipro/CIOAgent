# OpenAI model access through a ChatGPT subscription

cio.bot has two intentionally separate OpenAI transports:

| Chain service | Authentication | Billing/limit surface | Runtime |
|---|---|---|---|
| `codex` | `codex login` with a ChatGPT account | ChatGPT/Codex plan limits | Codex app-server |
| `openai` | `OPENAI_API_KEY` | OpenAI API usage and billing | OpenAI Agents SDK |

ChatGPT Plus does not turn a ChatGPT login into an API key. The ordinary OpenAI API path
continues to require `OPENAI_API_KEY`; the subscription path is hosted by Codex app-server.
This distinction prevents a configuration labelled “subscription” from silently charging
the API account.

## One-time setup

```bash
npm install -g @openai/codex
codex login
codex --version
```

Choose ChatGPT sign-in during login. Login must be performed as the OS user that runs
cio.bot. For the supplied systemd unit that is `skchen`, with `HOME=/home/skchen`.
If the service intentionally uses another auth directory, set `CIO_CODEX_HOME` to it.

The current default `bot_chat` chain in `config/committee_models.yaml` is:

```yaml
- {service: codex, model: gpt-5.6-sol, reasoning_effort: medium}
- {service: openai, model: gpt-5.6-terra, daily_limit: 120000}
- {service: nim, model: moonshotai/kimi-k2.6}
```

The NIM entry is retained from the current configuration, but bot-chat routing skips NIM
links. Use a Claude link if you want a functional third fallback after Codex and OpenAI API.

You can choose another configured chain in Dashboard → Configure.
`CIO_BOT_ENGINE=codex` forces the subscription route for diagnosis; remove the override for
normal fallback routing.

## Thinking level

Each `service: codex` link can set `reasoning_effort`. The dashboard labels this field
**Thinking (Codex)** and offers:

| UI label | Config value | Notes |
|---|---|---|
| Default | field omitted | Uses that model's default |
| Low | `low` | Lowest reasoning latency |
| Medium | `medium` | Common model default |
| High | `high` | Shipped cio.bot setting |
| Extra high | `xhigh` | Deeper reasoning when supported |
| Max | `max` | Maximum single-agent reasoning when supported |
| Ultra | `ultra` | Available only on some models; may enable proactive multi-agent behavior |

Codex advertises supported efforts per model, so not every model accepts every row. cio.bot
passes the configured value to app-server on every turn and reports a clear turn error if the
chosen model rejects it. For a temporary process-wide override:

```bash
CIO_CODEX_REASONING_EFFORT=max .venv/bin/python -m cio.bot
```

For systemd, add the equivalent `Environment=CIO_CODEX_REASONING_EFFORT=max`, then restart.
Changing the dashboard/YAML setting invalidates the cached chat runtime and takes effect on
the next chat message. The replacement starts a fresh transport transcript, so the bot
notifies the user to restate anything important from the previous thread.

## Agent coverage

Every LLM-backed role routed through `committee.engine.ask_role` supports Codex:

- committee specialists and debate rounds;
- moderator and final CIO decision;
- watchlist monitor and its macro snapshot;
- report translation and note sanitization.

Those calls share one app-server process for efficiency. Each call uses a separate ephemeral
thread, receives its role prompt as base instructions, and has no dynamic tools. Alpha Hunter
itself is deterministic and does not make an LLM call; its conversational command uses the
selected bot-chat runtime, and its published watchlist is subsequently analyzed by WMA.

If a Codex call fails or reaches a subscription limit, `codex` is latched and the named chain
continues to its next service. Unknown service names no longer silently route to Claude.

## Behavior

- Bot chat exposes the existing CIO tool handlers as app-server dynamic tools. Portfolio,
  memory, charts, web evidence, and committee delivery therefore use the same implementation.
- Bot-chat Codex thread IDs are persisted in the CIO database and resumed after restart.
- Uploaded images are attached to the Codex turn as local-image inputs.
- Usage and detailed call logs are attributed to `codex`, never `openai`.
- The app-server is started read-only with approval disabled, and its instructions restrict it
  to the CIO tools rather than source-code or shell work.
- Missing CLI/authentication latches `codex`; the next turn can reselect the next chain service.

## Troubleshooting

- `Codex CLI not found`: install it or set `CIO_CODEX_BIN` to the executable path.
- `not logged in with a ChatGPT subscription`: run `codex login` as the service user. An
  API-key-authenticated Codex session is rejected by design because it is not subscription use.
- Works in a terminal but not systemd: confirm `User=`, `HOME=`, and optional
  `CIO_CODEX_HOME` point to the account that ran `codex login`, then restart the service.
- To test routing without changing YAML, temporarily set `CIO_BOT_ENGINE=codex`.

Official references: [OpenAI API quickstart](https://platform.openai.com/docs/quickstart/make-your-first-api-request)
for API-key authentication and the [Codex App Server documentation](https://learn.chatgpt.com/docs/app-server).
