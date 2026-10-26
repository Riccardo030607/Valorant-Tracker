# Valorant Tracker

A Python companion app for VALORANT that reads the game's own local and remote
APIs to show who you are playing with: real names (including players in
Incognito mode), official ranks, agents, and the weapon skins every player has
equipped. It ships with a terminal interface built on
[rich](https://github.com/Textualize/rich) and a local web UI for loadouts.

## Features

- **Live match detection** — automatically follows the transition between menu,
  agent select, and an active match.
- **Anonymous name reveal** — resolves real display names through Riot's
  official Name Service, which returns them even for players using Incognito.
- **Official ranks** — current rank, peak rank, and RR pulled from Riot's MMR
  endpoint for every player. No API key and no rate limits.
- **Loadout web UI** — all players with agent portrait, rank badge, and their
  Vandal and Phantom skins; click a player for their full arsenal.
- **Per-match instalock** — at agent select the tracker asks which agent to
  lock, then selects and locks it immediately.
- **Correct maps and modes** — real display names instead of internal codenames
  such as *Triad* or *Bonsai*, including recent maps (*Corrode*, *Summit*,
  *Abyss*) and limited-time queues (*Gauntlet: Glitched*, *Skirmish*,
  *Skirmish: Ascension*, *Retake*, *Knockout*). Console queues (`console_*`) are
  recognised as well.
- **Self-updating content** — agents, maps, game modes, matchmaking queues,
  weapons, skins, competitive tiers, and the client version are downloaded from
  [valorant-api.com](https://valorant-api.com) and cached locally, with an
  offline fallback. New agents, maps, and modes appear without code changes.

## Requirements

- Python 3.10 or newer
- VALORANT installed, running, and at least at the main menu

## Installation

```bash
# 1. (recommended) create a virtual environment
python -m venv valenv
valenv\Scripts\activate        # Windows
# source valenv/bin/activate   # Linux/macOS

# 2. install the dependencies
pip install -r requirements.txt
```

Optionally, copy `.env.example` to `.env` to enable the extra statistics
described below. Ranks work without it.

## Usage

### Terminal tracker and web UI together

```bash
python app.py
```

1. Start VALORANT and reach the main menu.
2. Run `python app.py`. The loadout web UI starts alongside it at
   `http://127.0.0.1:5000`.
3. Queue for a match. At agent select the tracker asks which agent to instalock.
4. During the match the browser shows every player with agent, rank, and their
   Vandal and Phantom skins. Click a player to open their full arsenal.

Players using Incognito are flagged in the interface, while their real name is
still displayed.

### Web UI only

```bash
python web_app.py
```

Starts just the web server and opens `http://127.0.0.1:5000`.

> Loadouts are only exposed by the game API **during a match**; they are not
> available at agent select. Ranks are available at all times.

## Ranks and statistics

Current rank, peak rank, and RR come from Riot's official MMR endpoint
(`/mmr/v1/players`) for all players, with no API key and no rate limit.

Headshot percentage, win rate, and K/D are not exposed by the in-game API.
They can optionally be retrieved from the Henrik API:

1. Request a free key at [henrikdev.xyz](https://docs.henrikdev.xyz/).
2. Copy `.env.example` to `.env` and set `HENRIK_API_KEY`.

## Project structure

```
Valorant-Tracker/
├── app.py                  # Terminal tracker (rich UI, state loop, instalock)
├── web_app.py              # Flask server: JSON API and background poller
├── web/                    # Loadout frontend (index.html, style.css, app.js)
├── match_service.py        # Match snapshot: players, ranks, loadouts with images
├── valorant_remote_api.py  # Local and remote API client (auth, state, names, MMR)
├── valorant_content.py     # Dynamic game content and image URLs
├── player_stats.py         # Optional extra stats (Henrik API), loaded in parallel
├── agents.py               # Thin convenience wrapper around the content provider
├── websocket_debug/        # Standalone tool to capture local websocket events
├── requirements.txt
├── .env.example
└── README.md
```

## Loadout web UI

At `http://127.0.0.1:5000`, during a match, the page shows:

- every player split by team (allies and enemies), or a single grid in
  free-for-all modes such as Deathmatch;
- agent portrait, real name, rank icon with RR, and peak rank;
- the equipped Vandal and Phantom skins with their renders;
- the complete arsenal with every equipped skin when a player is clicked.

The frontend polls `GET /api/match`, served by `web_app.py`.

## How it works

- **Lockfile** — the Riot Client writes
  `%LOCALAPPDATA%\Riot Games\Riot Client\Config\lockfile`, which holds the
  credentials for the local API.
- **Local API** (`127.0.0.1`) — provides authentication tokens, presences, and
  the game state (`matchPresenceData.sessionLoopState`).
- **Remote APIs** (`glz-*` and `pd.*.a.pvp.net`) — pregame and coregame data,
  agent select and lock, loadouts, MMR, and the Name Service
  (`/name-service/v2/players`) used to resolve real names.
- **Dynamic client version** — the `X-Riot-ClientVersion` header is aligned with
  the current build. A stale value is the usual cause of rejected requests.
- **Region and shard** — the chat region (`jp`, `oce`, `eu2`, …) is more
  granular than the game region and must be normalised to a valid game affinity
  (`ap`, `eu`, `na`, `kr`, `latam`, `br`); otherwise the `glz` and `pd` hosts do
  not exist and every request fails.
- **State detection** — the local presence decides the state, while remote calls
  only resolve the `MatchID`. When those lag behind, which happens in the first
  seconds of agent select, the tracker waits instead of incorrectly falling back
  to "menu".

## Troubleshooting

- **Cannot connect** — VALORANT is not fully started; the launcher alone is not
  enough.
- **Extra statistics are empty** — `HENRIK_API_KEY` is missing or invalid in
  `.env`. Ranks do not depend on it.
- **Instalock fails** — the agent was already taken, or it is not unlocked on
  the account.
- **Content looks outdated** — the cache refreshes every 12 hours; delete the
  `.cache/` directory to force an update.
- **The web UI shows stale data** — an older server instance is still listening
  on port 5000. The tracker now reports this at startup: stop the leftover
  Python processes and restart.

## Notes

This project only reads the APIs the VALORANT client already exposes on the
local machine. It does not modify game files and does not inject code. It is not
affiliated with or endorsed by Riot Games. Use of third-party tools is at your
own risk.

## License

Released under the [MIT License](LICENSE).
