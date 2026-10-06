"""Standalone desktop application.

Runs the full WaferGuard server on 127.0.0.1 (SQLite database, in-process batch
queue, per-user data folder) and shows the web UI in a native window via
pywebview; falls back to the default browser if pywebview is unavailable.

    python -m waferguard.desktop.launcher            # from source
    WaferGuard.exe / WaferGuard.AppImage             # packaged (scripts/build_desktop_*.{sh,ps1})
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


def resource_root() -> str:
    """Folder containing models/ and deploy/ both from source and inside a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    d = os.path.join(base, "WaferGuard")
    os.makedirs(d, exist_ok=True)
    return d


def free_port(preferred: int = 8765) -> int:
    # Keep the port (and so the browser origin, which owns localStorage) stable between runs:
    # try a small fixed range before falling back to any free port.
    for port in (*range(preferred, preferred + 10), 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def configure_env() -> dict:
    root, data = resource_root(), data_dir()
    secret_file = os.path.join(data, ".secret")
    if not os.path.exists(secret_file):
        with open(secret_file, "w") as fh:
            fh.write(os.urandom(32).hex())
    env = {
        "WG_CONFIG": os.path.join(root, "deploy", "configs", "desktop.yaml"),
        "WG_DATABASE_URL": "sqlite:///" + os.path.join(data, "waferguard.db").replace("\\", "/"),
        "WG_STORAGE_DIR": os.path.join(data, "images"),
        "WG_MODELS__CHAMPION_DIR": os.path.join(root, "models", "wafer-ensemble"),
        "WG_JWT_SECRET": open(secret_file).read().strip(),
    }
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def wait_ready(url: str, timeout: float = 60) -> bool:
    import urllib.request
    t = time.time()
    while time.time() - t < timeout:
        try:
            with urllib.request.urlopen(url + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def main():  # pragma: no cover - needs a display
    configure_env()
    import uvicorn

    from waferguard.api.main import create_app
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    url = f"http://127.0.0.1:{port}"
    if not wait_ready(url):
        print("WaferGuard server failed to start", file=sys.stderr)
        sys.exit(1)
    try:
        import webview
        webview.create_window("WaferGuard", url, width=1360, height=880, min_size=(900, 640))
        # pywebview starts in private mode by default, which wipes localStorage (session, lot/equipment IDs)
        # every time the app closes. Keep the web profile in the per-user data folder instead.
        webview.start(private_mode=False, storage_path=os.path.join(data_dir(), "webview"))
    except Exception:  # noqa: BLE001 - no GUI toolkit: use the browser and keep serving
        webbrowser.open(url)
        print(f"WaferGuard running at {url} (Ctrl+C to quit)")
        try:
            while th.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    server.should_exit = True


if __name__ == "__main__":
    main()
