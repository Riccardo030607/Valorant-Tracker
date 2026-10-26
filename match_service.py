"""
High-level service producing a complete snapshot of the current match:
players, agents, official ranks (Riot MMR), and loadouts with image URLs.

The snapshot is a plain JSON-serialisable dict, consumed by the web UI and,
in part, by the terminal tracker.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from valorant_content import (
    SOCKET_BUDDY,
    SOCKET_SKIN,
    SOCKET_SKIN_CHROMA,
    SOCKET_SKIN_LEVEL,
    WEAPON_ORDER,
    content,
)
from valorant_remote_api import ValorantRemoteAPI


def _parse_mmr(mmr: Optional[Dict]) -> Dict:
    """Extract current rank, RR, and peak from a /mmr/v1/players response."""
    result = {"tier": 0, "rr": 0, "peak_tier": 0, "leaderboard": 0}
    if not mmr:
        return result

    queue_skills = mmr.get("QueueSkills", {}) or {}
    competitive = queue_skills.get("competitive", {}) or {}
    seasonal = competitive.get("SeasonalInfoBySeasonID", {}) or {}

    # Current rank: the current act, falling back to the latest competitive update
    act_id = content.current_act_id
    current = seasonal.get(act_id) if act_id else None
    if current:
        result["tier"] = current.get("CompetitiveTier", 0)
        result["rr"] = current.get("RankedRating", 0)
        result["leaderboard"] = current.get("LeaderboardRank", 0)
    else:
        latest = mmr.get("LatestCompetitiveUpdate", {}) or {}
        result["tier"] = latest.get("TierAfterUpdate", 0)
        result["rr"] = latest.get("RankedRatingAfterUpdate", 0)

    # Peak: highest tier ever reached across every act. WinsByTier is included
    # because the end-of-act tier alone misses a peak that was later lost.
    peak = 0
    for info in seasonal.values():
        peak = max(peak, info.get("CompetitiveTier", 0))
        for tier_str in (info.get("WinsByTier") or {}).keys():
            try:
                peak = max(peak, int(tier_str))
            except (TypeError, ValueError):
                pass
    result["peak_tier"] = max(peak, result["tier"])
    return result


def _rank_block(tier: int, rr: int = 0) -> Dict:
    info = content.get_rank_info(tier)
    return {
        "tier": tier,
        "name": info.get("name", "—"),
        "color": info.get("color", "#7a7a7a"),
        "icon": info.get("icon", ""),
        "rr": rr,
    }


class MatchService:
    """Builds match snapshots and owns the MMR cache."""

    def __init__(self, api: Optional[ValorantRemoteAPI] = None):
        self.api = api or ValorantRemoteAPI()
        self.connected = False
        self.match_id: Optional[str] = None
        self._mmr_cache: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    # ---- connection ------------------------------------------------------

    def connect(self) -> bool:
        content.refresh()
        self.connected = self.api.connect()
        return self.connected

    # ---- state -----------------------------------------------------------

    def detect_state(self) -> str:
        state, self.match_id = self.api.detect_state()
        return state

    # ---- MMR (ranks) -----------------------------------------------------

    def _get_rank(self, puuid: str) -> Dict:
        with self._lock:
            cached = self._mmr_cache.get(puuid)
        if cached:
            return cached
        parsed = _parse_mmr(self.api.get_player_mmr(puuid))
        with self._lock:
            self._mmr_cache[puuid] = parsed
        return parsed

    def _load_ranks(self, puuids: List[str]):
        with ThreadPoolExecutor(max_workers=8) as pool:
            pool.map(self._get_rank, [p for p in puuids if p])

    # ---- loadouts --------------------------------------------------------

    def _parse_loadouts(self, loadouts_resp: Optional[Dict]) -> Dict[str, Dict]:
        """Map puuid -> parsed loadout."""
        result: Dict[str, Dict] = {}
        if not loadouts_resp:
            return result
        loadouts = loadouts_resp.get("Loadouts", []) or []
        for entry in loadouts:
            loadout = entry.get("Loadout", entry)
            subject = loadout.get("Subject", "")
            if subject:
                result[subject] = self._parse_single_loadout(loadout)
        return result

    def _parse_single_loadout(self, loadout: Dict) -> Dict:
        items = loadout.get("Items", {}) or {}
        weapons: List[Dict] = []

        for weapon_uuid, item in items.items():
            sockets = (item or {}).get("Sockets", {}) or {}

            def sock(socket_id: str) -> str:
                node = sockets.get(socket_id, {}) or {}
                return (node.get("Item", {}) or {}).get("ID", "")

            chroma_id = sock(SOCKET_SKIN_CHROMA)
            level_id = sock(SOCKET_SKIN_LEVEL)
            skin_id = sock(SOCKET_SKIN)
            buddy_id = sock(SOCKET_BUDDY)

            weapon_name = content.get_weapon_name(weapon_uuid) or "?"
            # The level name is the most specific one, the base skin the least.
            skin_name = (content.get_skin_name(level_id)
                         or content.get_skin_name(chroma_id)
                         or content.get_skin_name(skin_id)
                         or "Default")
            image = content.skin_render(chroma_id or level_id)

            weapons.append({
                "weapon": weapon_name,
                "weapon_uuid": weapon_uuid,
                "skin": skin_name,
                "image": image,
                "buddy_image": content.buddy_icon(buddy_id) if buddy_id else "",
            })

        # Sort by the preferred display order, then alphabetically.
        order = {n.lower(): i for i, n in enumerate(WEAPON_ORDER)}
        weapons.sort(key=lambda w: (order.get(w["weapon"].lower(), 999), w["weapon"]))

        def find(name: str) -> Optional[Dict]:
            for w in weapons:
                if w["weapon"].lower() == name:
                    return w
            return None

        return {
            "weapons": weapons,
            "vandal": find("vandal"),
            "phantom": find("phantom"),
        }

    # ---- full snapshot ---------------------------------------------------

    def build_snapshot(self) -> Dict:
        """Build the complete snapshot of the current state."""
        state = self.detect_state()
        snap = {
            "state": state,
            "region": (self.api.region or "").upper(),
            "mode": "",
            "map": "",
            "players": [],
            "has_loadouts": False,
        }

        # detect_state already resolved the MatchID, so do not ask again.
        match_id = self.match_id

        if state == "PREGAME":
            match_info = self.api.get_pregame_match(match_id) if match_id else None
            data = (self.api.get_match_players_with_names(match_info, is_pregame=True)
                    if match_info else None)
            loadouts = {}  # loadouts are not exposed during agent select
        elif state == "INGAME":
            match_info = self.api.get_coregame_match(match_id) if match_id else None
            data = (self.api.get_match_players_with_names(match_info, is_pregame=False)
                    if match_info else None)
            content.ensure_skins()
            loadouts = self._parse_loadouts(self.api.get_coregame_loadouts(match_id)
                                            if match_id else None)
            snap["has_loadouts"] = bool(loadouts)
        else:
            return snap

        if not data:
            return snap

        snap["mode"] = data.get("mode", "")
        snap["map"] = data.get("map_name", "")

        allies = [dict(p, team="ally") for p in data.get("allies", [])]
        enemies = [dict(p, team="enemy") for p in data.get("enemies", [])]
        all_players = allies + enemies

        # Official ranks, fetched in parallel.
        self._load_ranks([p["puuid"] for p in all_players])

        for p in all_players:
            rank = self._get_rank(p["puuid"])
            agent_uuid = p.get("agent", "")
            agent_name = content.get_agent_name(agent_uuid) if agent_uuid else ""
            loadout = loadouts.get(p["puuid"])
            snap["players"].append({
                "puuid": p["puuid"],
                "name": p.get("name", "Unknown"),
                "tag": p.get("tag", ""),
                "is_me": p.get("is_me", False),
                "is_private": p.get("is_private", False),
                "team": p.get("team", "ally"),
                "agent": {
                    "name": agent_name if agent_name and agent_name != "Unknown" else "",
                    "icon": content.agent_icon(agent_uuid) if agent_uuid else "",
                },
                "rank": _rank_block(rank["tier"], rank["rr"]),
                "peak": _rank_block(rank["peak_tier"]),
                "loadout": loadout,
            })

        return snap

    def clear_caches(self):
        with self._lock:
            self._mmr_cache.clear()
