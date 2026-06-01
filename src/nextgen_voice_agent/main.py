import argparse
import socket
import webbrowser

import uvicorn
from nextgen_voice_agent.server.app import app


def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Neuge Voice local backend and browser UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface to bind. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind when --dynamic-port is not used.")
    parser.add_argument("--dynamic-port", action="store_true", help="Bind an available localhost port and print PORT:<port>.")
    parser.add_argument("--open", action="store_true", help="Open the browser after printing the frontend URL.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the browser.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    port = args.port
    if args.dynamic_port:
        port = get_free_port()
        print(f"PORT:{port}", flush=True)

    url = f"http://{args.host}:{port}/#/"
    print(f"URL:{url}", flush=True)
    print(f"Open {url}", flush=True)

    should_open = args.open or (not args.no_open and not args.dynamic_port)
    if should_open:
        webbrowser.open(url)

    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
