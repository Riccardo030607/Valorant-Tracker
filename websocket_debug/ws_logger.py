"""
VALORANT WebSocket logger.

Captures every WebSocket event emitted by the local Riot client so they can be
inspected offline, mainly to find out which ones carry live match statistics.

Usage:
1. Start VALORANT
2. Run: python ws_logger.py
3. Play a match, or part of one
4. Stop with Ctrl+C
5. Inspect the files written to the 'logs' directory

Note: the log contains your PUUID. The directory is git-ignored; treat its
contents as personal data before sharing them.
"""

import os
import json
import ssl
import base64
from datetime import datetime
from pathlib import Path

try:
    import websocket
except ImportError:
    print("Library 'websocket-client' not found.")
    print("   Install it with: pip install websocket-client")
    exit(1)

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("Library 'requests' not found.")
    print("   Install it with: pip install requests")
    exit(1)


class ValorantWebSocketLogger:
    def __init__(self):
        self.lockfile = None
        self.session = None
        self.events_help = None
        self.log_file = None
        self.event_counts = {}
        self.interesting_events = []

    def read_lockfile(self):
        """Read the lockfile to obtain the local API port and password."""
        local_appdata = os.getenv('LOCALAPPDATA')
        lockfile_path = os.path.join(local_appdata, "Riot Games", "Riot Client",
                                     "Config", "lockfile")

        if not os.path.exists(lockfile_path):
            return None

        with open(lockfile_path, 'r') as f:
            content = f.read().strip()
            parts = content.split(':')
            return {
                'name': parts[0],
                'pid': parts[1],
                'port': parts[2],
                'password': parts[3],
                'protocol': parts[4] if len(parts) > 4 else 'https'
            }

    def make_request(self, endpoint):
        """Perform a request against the local API."""
        url = f"https://127.0.0.1:{self.lockfile['port']}{endpoint}"
        credentials = f"riot:{self.lockfile['password']}"
        encoded = base64.b64encode(credentials.encode()).decode()

        response = requests.get(
            url,
            headers={'Authorization': f'Basic {encoded}'},
            verify=False,
            timeout=5
        )
        return response.json()

    def get_session(self):
        """Current chat session."""
        return self.make_request('/chat/v1/session')

    def get_help(self):
        """List of every event the client can emit."""
        return self.make_request('/help')

    def on_message(self, ws, message):
        """Callback invoked for each incoming message."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        try:
            data = json.loads(message)

            # Format: [opcode, event_name, event_data]
            if isinstance(data, list) and len(data) >= 2:
                event_name = data[1] if len(data) > 1 else "unknown"
                event_data = data[2] if len(data) > 2 else {}

                self.event_counts[event_name] = self.event_counts.get(event_name, 0) + 1

                print(f"[{timestamp}] {event_name} (#{self.event_counts[event_name]})")

                log_entry = {
                    'timestamp': timestamp,
                    'event': event_name,
                    'data': event_data
                }
                self.log_file.write(json.dumps(log_entry) + '\n')
                self.log_file.flush()

                # Flag events that may carry live statistics.
                keywords = ['kill', 'death', 'round', 'score', 'match', 'game',
                            'combat', 'damage']
                if any(kw in event_name.lower() for kw in keywords):
                    print(f"   -> candidate: {json.dumps(event_data)[:200]}...")
                    self.interesting_events.append(log_entry)

        except json.JSONDecodeError:
            print(f"[{timestamp}] non-JSON message: {message[:100]}")

    def on_error(self, ws, error):
        print(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        print(f"\nWebSocket closed (code: {close_status_code})")
        self.save_summary()

    def on_open(self, ws):
        print("Connected to the WebSocket.")
        print("Subscribing to every event...")

        events = self.events_help.get('events', {})
        for event_name in events.keys():
            if event_name == 'OnJsonApiEvent':
                continue  # skip the catch-all event
            ws.send(json.dumps([5, event_name]))

        print(f"Subscribed to {len(events)} events.")
        print("\n" + "=" * 60)
        print("Play now: every event will be recorded.")
        print("   Press Ctrl+C to stop and save.")
        print("=" * 60 + "\n")

    def save_summary(self):
        """Write a summary of the captured events."""
        if self.log_file:
            self.log_file.close()

        summary_path = self.log_file.name.replace('.jsonl', '_summary.txt')
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write("=" * 60 + "\n")
            f.write("WEBSOCKET EVENT SUMMARY\n")
            f.write("=" * 60 + "\n\n")

            f.write("EVENT COUNTS:\n")
            f.write("-" * 40 + "\n")
            for event, count in sorted(self.event_counts.items(), key=lambda x: -x[1]):
                f.write(f"  {event}: {count}\n")

            f.write("\n\nCANDIDATE EVENTS (possible statistics):\n")
            f.write("-" * 40 + "\n")
            for event in self.interesting_events:
                f.write(f"\n[{event['timestamp']}] {event['event']}\n")
                f.write(f"  Data: {json.dumps(event['data'], indent=2)[:500]}\n")

        print(f"\nSummary saved to: {summary_path}")
        print(f"Full log at: {self.log_file.name}")

        print("\n" + "=" * 60)
        print("SESSION STATISTICS")
        print("=" * 60)
        print(f"Total events received: {sum(self.event_counts.values())}")
        print(f"Unique event types: {len(self.event_counts)}")
        print(f"Candidate events: {len(self.interesting_events)}")
        print("\nTop 10 most frequent events:")
        for event, count in sorted(self.event_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:5d}x  {event}")

    def run(self):
        print("=" * 60)
        print("   VALORANT WEBSOCKET LOGGER")
        print("=" * 60)
        print()

        print("Looking for VALORANT...")
        self.lockfile = self.read_lockfile()

        if not self.lockfile:
            print("VALORANT not found. Make sure the game is running.")
            return

        print(f"Found. Port: {self.lockfile['port']}")

        print("Reading the session...")
        try:
            self.session = self.get_session()
        except Exception as e:
            print(f"Session error: {e}")
            return

        print("Reading the event list...")
        try:
            self.events_help = self.get_help()
            events = self.events_help.get('events', {})
            print(f"Found {len(events)} event types")
        except Exception as e:
            print(f"Help error: {e}")
            return

        logs_dir = Path(__file__).parent / 'logs'
        logs_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = logs_dir / f"ws_log_{timestamp}.jsonl"
        self.log_file = open(log_path, 'w', encoding='utf-8')
        print(f"Log file: {log_path}")

        # Record the session context. The lockfile password grants full access
        # to the local API, so it is never written to disk.
        redacted = {k: v for k, v in self.lockfile.items() if k != 'password'}
        self.log_file.write(json.dumps({'type': 'lockfile', 'data': redacted}) + '\n')
        self.log_file.write(json.dumps({'type': 'session', 'data': self.session}) + '\n')
        self.log_file.write(json.dumps({'type': 'events_list',
                                        'data': list(events.keys())}) + '\n')

        print("\nConnecting to the WebSocket...")

        ws_url = f"wss://127.0.0.1:{self.lockfile['port']}"
        credentials = f"riot:{self.lockfile['password']}"
        encoded = base64.b64encode(credentials.encode()).decode()

        ws = websocket.WebSocketApp(
            ws_url,
            header=[f"Authorization: Basic {encoded}"],
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

        try:
            ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})
        except KeyboardInterrupt:
            print("\n\nInterrupted by the user...")
            ws.close()
            self.save_summary()


if __name__ == "__main__":
    logger = ValorantWebSocketLogger()
    logger.run()
