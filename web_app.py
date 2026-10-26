"""
Local web server for the Valorant Tracker.

Runs a small Flask backend that:
  * connects to VALORANT and polls the match state on a background thread
  * exposes /api/match with the current snapshot (players, ranks, loadouts)
  * serves the web UI from the web/ directory

Usage:
    python web_app.py
Then open http://127.0.0.1:5000 in a browser.
"""

from __future__ import annotations

import logging
import threading
import time
import webbrowser
from typing import Dict, Optional

import flask.cli
from flask import Flask, jsonify, send_from_directory

from match_service import MatchService

HOST = "127.0.0.1"
PORT = 5000
POLL_INTERVAL = 3.0  # seconds between snapshot updates


def silence_flask_logs() -> None:
    """Silence werkzeug request logs and the Flask startup banner.

    Essential when the server shares a terminal with the tracker: one log line
    per HTTP request interrupts the in-place rendering of rich.Live, which then
    reprints the whole panel on every poll.
    """
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("werkzeug").propagate = False
    flask.cli.show_server_banner = lambda *args, **kwargs: None


app = Flask(__name__, static_folder="web", static_url_path="")
app.logger.setLevel(logging.ERROR)


class Poller:
    """Keeps the match snapshot up to date on a background thread."""

    def __init__(self):
        self.service = MatchService()
        self.snapshot: Dict = {"state": "DISCONNECTED", "players": []}
        self.error: Optional[str] = None
        self._lock = threading.Lock()

    def _connect_loop(self):
        while not self.service.connected:
            try:
                if self.service.connect():
                    self.error = None
                    return
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
            with self._lock:
                self.snapshot = {"state": "DISCONNECTED", "players": [],
                                 "error": "VALORANT not detected. Start the game and wait."}
            time.sleep(3)

    def run(self):
        self._connect_loop()
        while True:
            try:
                snap = self.service.build_snapshot()
                state = snap.get("state")
                # Back in the menu: drop the caches before the next match.
                if state == "MENU":
                    self.service.clear_caches()
                if state == "DISCONNECTED":
                    snap["error"] = ("VALORANT is not running. "
                                     "Start the game and wait for the main menu.")
                    # Tokens die with the game, so reconnect from scratch.
                    if not self.service.api.is_client_reachable():
                        self.service.connected = False
                with self._lock:
                    self.snapshot = snap
                    self.error = None
                if not self.service.connected:
                    self._connect_loop()
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                if not self.service.api.is_client_reachable():
                    self.service.connected = False
                    self._connect_loop()
            time.sleep(POLL_INTERVAL)

    def get(self) -> Dict:
        with self._lock:
            data = dict(self.snapshot)
        if self.error:
            data["error"] = self.error
        return data


poller = Poller()


@app.route("/")
def index():
    return send_from_directory("web", "index.html")


@app.route("/api/match")
def api_match():
    return jsonify(poller.get())


@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "connected": poller.service.connected})


def url() -> str:
    return f"http://{HOST}:{PORT}"


def port_in_use() -> bool:
    """True when something already listens on the port, e.g. an old server."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, PORT)) == 0


def _start_poller():
    threading.Thread(target=poller.run, daemon=True).start()


def start_background(open_browser: bool = True) -> str:
    """Start the poller and the Flask server on daemon threads.

    Returns the UI url and does not block the caller.

    Raises when the port is already taken: without this check the new server
    would die silently and the browser would open the stale instance.
    """
    if port_in_use():
        raise RuntimeError(
            f"port {PORT} is already in use: stop the old server and restart")
    silence_flask_logs()
    _start_poller()
    threading.Thread(
        target=lambda: app.run(host=HOST, port=PORT, threaded=True,
                               debug=False, use_reloader=False),
        daemon=True,
    ).start()
    if open_browser:
        try:
            webbrowser.open(url())
        except Exception:
            pass
    return url()


def main():
    silence_flask_logs()
    _start_poller()
    print("=" * 60)
    print("  VALORANT TRACKER - WEB UI")
    print("=" * 60)
    print(f"  Server running at {url()}")
    print("  Opening the browser... (Ctrl+C to quit)")
    print("=" * 60)
    try:
        webbrowser.open(url())
    except Exception:
        pass
    app.run(host=HOST, port=PORT, threaded=True, debug=False)


if __name__ == "__main__":
    main()
