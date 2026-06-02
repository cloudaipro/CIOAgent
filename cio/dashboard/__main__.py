"""
python -m cio.dashboard [host] [port]

Launch the localhost developer dashboard (token usage, Telegram history, full
committee sent/returned transcript). Defaults to 127.0.0.1:8787; override via
args or CIO_DASH_HOST / CIO_DASH_PORT.
"""
import logging
import sys

from .server import serve


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host = sys.argv[1] if len(sys.argv) > 1 else None
    port = int(sys.argv[2]) if len(sys.argv) > 2 else None
    serve(host, port)


if __name__ == "__main__":
    main()
