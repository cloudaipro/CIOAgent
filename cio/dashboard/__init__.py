"""Developer dashboard — a localhost-only, read-only web view over the data the
CIO agent already records, so the operator can verify it behaves correctly:

  • token usage per backend per day (OpenAI / Claude / NIM)
  • Telegram conversation history
  • every committee LLM call: content SENT and content RETURNED

No web framework — Python stdlib ``http.server`` only. Bound to 127.0.0.1.
Launch: ``python -m cio.dashboard`` (host/port via CIO_DASH_HOST/CIO_DASH_PORT).
"""
from .server import serve

__all__ = ["serve"]
