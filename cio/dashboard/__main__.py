"""
python -m cio.dashboard [host] [port]

Launch the localhost developer dashboard (token usage, Telegram history, full
committee sent/returned transcript). Defaults to 127.0.0.1:8787; override via
args or CIO_DASH_HOST / CIO_DASH_PORT.
"""
import sys

from dotenv import load_dotenv

from ..logsetup import configure_logging
from .server import serve


def main() -> None:
    # Same .env the bot loads (bot.py does this too) — the dashboard is its own
    # process, so without this, env-gated features (CIO_IBKR_TWS, CIO_DASH_TOKEN,
    # …) silently read as unset here while working in the bot.
    load_dotenv()
    configure_logging()   # console + optional date-based file (CIO_LOG_TO_FILE / Configure tab)
    host = sys.argv[1] if len(sys.argv) > 1 else None
    port = int(sys.argv[2]) if len(sys.argv) > 2 else None
    serve(host, port)


if __name__ == "__main__":
    main()
