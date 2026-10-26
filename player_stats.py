"""
Optional extra player statistics via the Henrik API (api.henrikdev.xyz).

Ranks shown by the tracker come from Riot's own MMR endpoint and need no key
(see `match_service.py`). This module only adds the figures the in-game API
does not expose, and therefore requires a free Henrik API key in `.env`.

Request a key at https://docs.henrikdev.xyz
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
from dotenv import load_dotenv

# Load environment variables from the .env file.
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

HENRIK_API_BASE = "https://api.henrikdev.xyz"

# Optional API key, read from .env or the environment.
HENRIK_API_KEY = os.getenv("HENRIK_API_KEY", "")

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "valorant-tracker",
}

# Rank family -> emoji, for the terminal interface.
RANK_EMOJI = {
    "Iron": "🔘",
    "Bronze": "🟤",
    "Silver": "⚪",
    "Gold": "🟡",
    "Platinum": "🔵",
    "Diamond": "💎",
    "Ascendant": "💚",
    "Immortal": "🔴",
    "Radiant": "🌟",
    "Unrated": "❓",
}

# Rank family -> rich colour.
RANK_COLOR = {
    "Iron": "grey58",
    "Bronze": "dark_orange3",
    "Silver": "grey78",
    "Gold": "gold1",
    "Platinum": "cyan",
    "Diamond": "magenta",
    "Ascendant": "green",
    "Immortal": "red",
    "Radiant": "bright_yellow",
    "Unrated": "grey42",
}

# Placeholder names assigned when a player cannot be resolved; they must never
# be looked up against the stats API.
UNRESOLVED_NAMES = {"Unknown", "Anonymous", "Player"}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlayerStats:
    """Statistics of a single player."""

    name: str
    tag: str

    # From the Henrik API
    current_rank: str = "Unknown"
    current_rank_tier: int = 0
    peak_rank: str = "Unknown"
    peak_rank_tier: int = 0
    mmr: int = 0
    elo: int = 0

    # Not exposed by the in-game API; left at zero unless a source provides
    # them, in which case the interface renders them instead of a dash.
    headshot_pct: float = 0.0
    win_rate: float = 0.0
    kd_ratio: float = 0.0

    map_win_rates: Dict[str, float] = field(default_factory=dict)
    error: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# HENRIK API
# ═══════════════════════════════════════════════════════════════════════════════

def get_rank_from_henrik(name: str, tag: str, region: str = "eu") -> Optional[Dict]:
    """Fetch rank and MMR data for a player.

    Args:
        name: player name.
        tag: player tag, without the leading '#'.
        region: eu, na, ap, kr, latam or br.

    Returns:
        The payload with rank info, or None when unavailable.
    """
    if not HENRIK_API_KEY:
        return None

    url = (f"{HENRIK_API_BASE}/valorant/v2/mmr/{region}/"
           f"{quote(name)}/{quote(tag)}")
    headers = {**HEADERS, "Authorization": HENRIK_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == 200:
                return data.get("data", {})
    except requests.RequestException:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def get_player_stats(name: str, tag: str, region: str = "eu") -> PlayerStats:
    """Collect every statistic available for a player."""
    stats = PlayerStats(name=name, tag=tag)

    rank_data = get_rank_from_henrik(name, tag, region)
    if rank_data:
        current = rank_data.get("current_data", {}) or {}
        stats.current_rank = current.get("currenttierpatched", "Unknown")
        stats.current_rank_tier = current.get("currenttier", 0)
        stats.mmr = current.get("ranking_in_tier", 0)
        stats.elo = current.get("elo", 0)

        highest = rank_data.get("highest_rank", {}) or {}
        if highest:
            stats.peak_rank = highest.get("patched_tier", "Unknown")
            stats.peak_rank_tier = highest.get("tier", 0)

    return stats


def get_rank_emoji(rank: str) -> str:
    """Emoji matching a rank name."""
    if not rank:
        return "❓"
    for rank_name, emoji in RANK_EMOJI.items():
        if rank_name.lower() in rank.lower():
            return emoji
    return "❓"


def get_rank_color(rank: str) -> str:
    """rich colour matching a rank name."""
    if not rank:
        return "grey42"
    for rank_name, color in RANK_COLOR.items():
        if rank_name.lower() in rank.lower():
            return color
    return "grey42"


def load_stats_parallel(players: List[Dict], region: str = "eu",
                        max_workers: int = 8,
                        cache: Optional[Dict[str, PlayerStats]] = None,
                        ) -> Dict[str, PlayerStats]:
    """Load statistics for several players concurrently.

    Args:
        players: dicts carrying at least 'name' and 'tag'.
        region: region passed to the Henrik API.
        max_workers: size of the thread pool.
        cache: optional {name#tag: PlayerStats} map, read and updated in place.

    Returns:
        A {name#tag: PlayerStats} map.
    """
    if cache is None:
        cache = {}
    results: Dict[str, PlayerStats] = {}
    to_fetch: Dict[str, Dict] = {}

    for p in players:
        name = p.get("name", "")
        tag = p.get("tag", "")
        if not name or not tag or name in UNRESOLVED_NAMES:
            continue
        key = f"{name}#{tag}"
        if key in cache:
            results[key] = cache[key]
        else:
            to_fetch[key] = p  # keyed by name#tag, so duplicates collapse

    if not to_fetch:
        return results

    def _fetch(key: str, p: Dict):
        return key, get_player_stats(p.get("name", ""), p.get("tag", ""), region)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch, k, p) for k, p in to_fetch.items()]
        for fut in as_completed(futures):
            try:
                key, stats = fut.result()
            except Exception:
                continue
            results[key] = stats
            cache[key] = stats

    return results


def api_is_configured() -> bool:
    """True when a Henrik API key is available."""
    return bool(HENRIK_API_KEY)


# ═══════════════════════════════════════════════════════════════════════════════
# QUICK SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("PLAYER STATS TEST")
    print("=" * 60)

    if not api_is_configured():
        print("\nHenrik API key not configured.")
        print("Copy .env.example to .env and set HENRIK_API_KEY.")
        print("Free keys: https://docs.henrikdev.xyz")
        print("\nRanks do not need this key: they come from Riot's MMR endpoint.")
    else:
        print("\nHenrik API key configured. Extra statistics are available.")
