import argparse
import socket
import sys
import webbrowser

import uvicorn

from nextgen_voice_agent.config import Settings, get_settings
from nextgen_voice_agent.launcher.managed_backend import (
    ManagedBackendStartupError,
    wait_for_backend_health,
)
from nextgen_voice_agent.server.app import create_app
from nextgen_voice_agent.server.runtime_mode import apply_runtime_modes


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Neuge Voice local backend and browser UI.")
    parser.add_argument("--host", default=None, help="Host interface to bind. Defaults from runtime policy.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind when --dynamic-port is not used.")
    parser.add_argument(
        "--dynamic-port",
        action="store_true",
        help="Bind an available localhost port and print PORT:<port> to stdout.",
    )
    parser.add_argument("--open", action="store_true", help="Open the browser after backend readiness.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    policy = apply_runtime_modes(settings)
    host = args.host or policy.bind_host_default
    port = args.port
    if args.dynamic_port:
        port = get_free_port()
        print(f"PORT:{port}", flush=True)

    url = f"http://{host}:{port}/#/"
    print(f"URL:{url}", flush=True)

    should_open = args.open or (not args.no_open and policy.should_auto_open_browser and not args.dynamic_port)
    if args.dynamic_port:
        should_open = args.open and not args.no_open

    app = create_app(settings=settings)

    if args.dynamic_port:
        import threading
        import time

        def _serve() -> None:
            uvicorn.run(app, host=host, port=port, log_level="info")

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        time.sleep(0.3)
        try:
            wait_for_backend_health(host, port)
            print(f"Backend ready at http://{host}:{port}/health", file=sys.stderr, flush=True)
        except ManagedBackendStartupError as exc:
            print(f"Backend failed readiness check: {exc}", file=sys.stderr, flush=True)
            sys.exit(1)
        if should_open:
            webbrowser.open(url)
        print(f"Open {url}", flush=True)
        thread.join()
        return

    if should_open:
        webbrowser.open(url)
    print(f"Open {url}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
