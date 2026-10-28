"""Start the app and open a browser on Windows, macOS, or Linux."""

from __future__ import annotations

import argparse
import importlib.util
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from threading import Event
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener


PROJECT_ROOT = Path(__file__).resolve().parent


def project_python(root: Path, *, windows: bool | None = None) -> Path:
    """Prefer the clone's virtual environment when no environment is active."""
    if getattr(sys, "frozen", False) or sys.prefix != sys.base_prefix:
        return Path(sys.executable)
    windows = os.name == "nt" if windows is None else windows
    candidate = root / ".venv" / ("Scripts/python.exe" if windows else "bin/python")
    return candidate if candidate.is_file() else Path(sys.executable)


def available_port() -> int:
    """Avoid an existing service without assuming a fixed machine-specific port."""
    for port in range(8501, 8521):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            try:
                connection.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError("No free port from 8501 to 8520. Stop an unused local app and retry.")


def wait_for_server(process: subprocess.Popen, url: str, timeout: float = 30) -> bool:
    """Open the browser only after Streamlit reports that it is healthy."""
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    while process.poll() is None and time.monotonic() < deadline:
        try:
            with opener.open(url + "/_stcore/health", timeout=1) as response:
                if response.status == 200 and response.read().strip() == b"ok":
                    return True
        except (URLError, OSError):
            pass
        Event().wait(0.2)
    return False


def stop_server(process: subprocess.Popen) -> None:
    """Clean up the child server on exit, including on Windows."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def server_command(port: int) -> list[str]:
    """A frozen executable must launch itself, not pretend to be Python."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve", "--port", str(port)]
    return [
        sys.executable, "-m", "streamlit", "run", str(PROJECT_ROOT / "ui" / "streamlit_app.py"),
        "--server.address", "127.0.0.1", "--server.port", str(port),
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
    ]


def run_server(port: int) -> int:
    """Run bundled Streamlit inside the executable's internal server process."""
    from main import configure_logging
    from streamlit.web import bootstrap

    options = {
        "server.address": "127.0.0.1", "server.port": port,
        "server.headless": True, "browser.gatherUsageStats": False,
        "server.fileWatcherType": "none", "global.developmentMode": False,
    }
    bootstrap.load_config_options(options)
    configure_logging()
    bootstrap.run(str(PROJECT_ROOT / "ui" / "streamlit_app.py"), False, [], options)
    return 0


def prepare_desktop_settings() -> None:
    """Create private per-user settings and retain logs for double-click launches."""
    directory = Path.home() / ".ai-testcase-generator"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings = directory / ".env"
    try:
        with settings.open("x", encoding="utf-8") as target:
            target.write((PROJECT_ROOT / ".env.example").read_text(encoding="utf-8"))
        settings.chmod(0o600)
    except FileExistsError:
        pass
    output = (directory / "app.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = output
    sys.stderr = output


def main(argv: list[str] | None = None) -> int:
    """Use the same launch command on every OS; never install software silently."""
    parser = argparse.ArgumentParser(description="Launch QA Intelligence Agent and open your browser.")
    parser.add_argument("--no-browser", action="store_true", help="Start without opening a browser")
    parser.add_argument("--port", type=int, help="Optional local port; otherwise select an available one")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if getattr(sys, "frozen", False):
        prepare_desktop_settings()
    if args.serve:
        return run_server(args.port if args.port is not None else available_port())

    interpreter = project_python(PROJECT_ROOT)
    if interpreter.absolute() != Path(sys.executable).absolute():
        try:
            return subprocess.call([str(interpreter), str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])])
        except KeyboardInterrupt:
            return 130
        except OSError:
            print("Cannot start the project environment. Recreate .venv using the README setup steps.", file=sys.stderr)
            return 1

    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required. Install Python, then follow README.md.", file=sys.stderr)
        return 1

    modules = ("streamlit", "langgraph", "langchain_openai", "langchain_ollama", "dotenv", "pydantic")
    if any(importlib.util.find_spec(module) is None for module in modules):
        print("Dependencies are missing. Run: python -m pip install -r requirements.txt", file=sys.stderr)
        return 1

    process = None
    try:
        port = args.port if args.port is not None else available_port()
        url = f"http://127.0.0.1:{port}"
        process = subprocess.Popen(server_command(port), cwd=PROJECT_ROOT)
        if not wait_for_server(process, url):
            print("The app could not start. Check the server output above and retry.", file=sys.stderr)
            return 1
        print(f"QA Intelligence Agent: {url}\nPress Ctrl+C in this terminal to stop.", flush=True)
        if not args.no_browser and not webbrowser.open(url):
            print(f"A browser could not be opened automatically. Open {url} manually.", flush=True)
        return process.wait()
    except KeyboardInterrupt:
        return 130
    except OSError:
        print("Cannot start the app. Check your Python installation and whether the port is available.", file=sys.stderr)
        return 1
    finally:
        if process is not None:
            stop_server(process)


if __name__ == "__main__":
    raise SystemExit(main())