import socket
import sys
import uvicorn
from nextgen_voice_agent.server.app import app

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]

def main():
    # If a specific port is passed (e.g. for dev mode), use it, otherwise get random
    port = 8000
    if len(sys.argv) > 1 and sys.argv[1] == "--dynamic-port":
        port = get_free_port()
        # Tauri expects to see this exact string on stdout to establish IPC
        print(f"PORT:{port}", flush=True)
    
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

if __name__ == "__main__":
    main()
