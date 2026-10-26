# VALORANT WebSocket debug tool

Captures every WebSocket event emitted by the local Riot client, so they can be
inspected offline to find out which ones carry live match data such as kills,
deaths, and round transitions.

This tool is not used by the tracker; it exists to explore the local API.

## Setup

```powershell
cd websocket_debug
pip install websocket-client
```

## Usage

1. Start VALORANT and sign in.
2. Run the logger:
   ```powershell
   python ws_logger.py
   ```
3. Play a match, or at least a few rounds.
4. Press `Ctrl+C` to stop.
5. Inspect the files in `logs/`.

## Output

Two files are written to `logs/`:

### `ws_log_YYYYMMDD_HHMMSS.jsonl`

Every event, one JSON object per line:

```json
{"timestamp": "12:34:56.789", "event": "OnJsonApiEvent_xxx", "data": {}}
```

### `ws_log_YYYYMMDD_HHMMSS_summary.txt`

A summary containing the count of each event type and the events flagged as
candidates, meaning their name contains `kill`, `death`, `round`, `score`,
`match`, `game`, `combat`, or `damage`.

## Privacy

The log includes your PUUID and the names of players in your matches. The
lockfile password is redacted, as it grants full access to the local API.
`logs/` is git-ignored; review the contents before sharing them.
