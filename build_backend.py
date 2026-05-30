import os
import subprocess
import sys

def build():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    # We must include uvicorn standard hidden imports so the server can boot
    hidden_imports = [
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ]
    
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name", "backend",
        "--onefile",
        "--clean",
        "--collect-data", "litellm",
        "--collect-all", "tiktoken",
        "--collect-all", "tiktoken_ext",
    ]
    
    for imp in hidden_imports:
        cmd.extend(["--hidden-import", imp])
        
    cmd.append("src/nextgen_voice_agent/main.py")
    
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    build()
