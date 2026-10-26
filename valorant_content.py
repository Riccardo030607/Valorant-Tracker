"""
Game content provider (agents, maps, modes, queues, skins, client version).

Data is downloaded from https://valorant-api.com, which mirrors the shipped
client, and cached on disk. When the network is unavailable an embedded offline
snapshot is used, so the app always starts.

This removes three recurring sources of breakage:
  * stale agent UUIDs -> instalock and agent recognition fail
  * internal map codenames (Bonsai, Triad) leaking into the interface
  * a hardcoded client version -> Riot APIs reject the requests
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

VALORANT_API_BASE = "https://valorant-api.com/v1"

# On-disk cache, refreshed at most once per TTL seconds.
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "content.json")
SKINS_CACHE_FILE = os.path.join(CACHE_DIR, "skins.json")
CACHE_TTL = 12 * 60 * 60  # 12 hours

# Short timeout: content is helpful but never critical to keep running.
_HTTP_TIMEOUT = 8

# Image CDN used by valorant-api.com
MEDIA = "https://media.valorant-api.com"

# Loadout "socket" UUIDs (stable, documented by the community)
SOCKET_SKIN = "bcef87d6-209b-46c6-8b19-fbe40bd95abc"
SOCKET_SKIN_LEVEL = "e7c63390-eda7-46e0-bb7a-a6abdacd2433"
SOCKET_SKIN_CHROMA = "3ad1b2b2-acdb-4524-852f-954a76ddae0a"
SOCKET_BUDDY = "77258665-71d1-4623-bc72-44db9bd5b3b3"

# Main weapon UUIDs (used for the summary view)
WEAPON_VANDAL = "9c82e19d-4575-0200-1a81-3eacf00cf872"
WEAPON_PHANTOM = "ee8e8d15-496b-07ac-e5f6-8fae5d4c7b1a"

# Preferred display order for the full arsenal
WEAPON_ORDER = [
    "Vandal", "Phantom", "Operator", "Sheriff", "Ghost", "Classic",
    "Guardian", "Marshal", "Outlaw", "Bulldog", "Warden", "Spectre",
    "Stinger", "Frenzy", "Shorty", "Bandit", "Bucky", "Judge",
    "Ares", "Odin", "Melee",
]


# ════════════════════════════════════════════════════════════════════════════
# OFFLINE SNAPSHOT (fallback) — matches release-13.08
# Only used when valorant-api.com is unreachable and no cache exists.
# ════════════════════════════════════════════════════════════════════════════

FALLBACK_CLIENT_VERSION = "release-13.08-shipping-20-5715960"

FALLBACK_AGENTS: Dict[str, str] = {
    "Astra": "41fb69c1-4189-7b37-f117-bcaf1e96f1bf",
    "Breach": "5f8d3a7f-467b-97f3-062c-13acf203c006",
    "Brimstone": "9f0d8ba9-4140-b941-57d3-a7ad57c6b417",
    "Chamber": "22697a3d-45bf-8dd7-4fec-84a9e28c69d7",
    "Clove": "1dbf2edd-4729-0984-3115-daa5eed44993",
    "Cypher": "117ed9e3-49f3-6512-3ccf-0cada7e3823b",
    "Deadlock": "cc8b64c8-4b25-4ff9-6e7f-37b4da43d235",
    "Fade": "dade69b4-4f5a-8528-247b-219e5a1facd6",
    "Gekko": "e370fa57-4757-3604-3648-499e1f642d3f",
    "Harbor": "95b78ed7-4637-86d9-7e41-71ba8c293152",
    "Iso": "0e38b510-41a8-5780-5e8f-568b2a4f2d6c",
    "Jett": "add6443a-41bd-e414-f6ad-e58d267f4e95",
    "KAY/O": "601dbbe7-43ce-be57-2a40-4abd24953621",
    "Killjoy": "1e58de9c-4950-5125-93e9-a0aee9f98746",
    "Miks": "7c8a4701-4de6-9355-b254-e09bc2a34b72",
    "Neon": "bb2a4828-46eb-8cd1-e765-15848195d751",
    "Omen": "8e253930-4c05-31dd-1b6c-968525494517",
    "Phoenix": "eb93336a-449b-9c1b-0a54-a891f7921d69",
    "Raze": "f94c3b30-42be-e959-889c-5aa313dba261",
    "Reyna": "a3bfb853-43b2-7238-a4f1-ad90e9e46bcc",
    "Sage": "569fdd95-4d10-43ab-ca70-79becc718b46",
    "Skye": "6f2a04ca-43e0-be17-7f36-b3908627744d",
    "Sova": "320b2a48-4d9b-a075-30f1-1f93a9b638fa",
    "Tejo": "b444168c-4e35-8076-db47-ef9bf368f384",
    "Veto": "92eeef5d-43b5-1d4a-8d03-b3927a09034b",
    "Viper": "707eab51-4836-f488-046a-cda6bf494859",
    "Vyse": "efba5359-4016-a1e5-7626-b1ae76895940",
    "Waylay": "df1cb487-4902-002e-5c17-d28e83e78588",
    "Yoru": "7f94d92c-4234-0a36-9646-3a87eb8b5c89",
}

FALLBACK_AGENT_ROLES: Dict[str, str] = {
    "Astra": "Controller", "Breach": "Initiator", "Brimstone": "Controller",
    "Chamber": "Sentinel", "Clove": "Controller", "Cypher": "Sentinel",
    "Deadlock": "Sentinel", "Fade": "Initiator", "Gekko": "Initiator",
    "Harbor": "Controller", "Iso": "Duelist", "Jett": "Duelist",
    "KAY/O": "Initiator", "Killjoy": "Sentinel", "Miks": "Controller",
    "Neon": "Duelist", "Omen": "Controller", "Phoenix": "Duelist",
    "Raze": "Duelist", "Reyna": "Duelist", "Sage": "Sentinel",
    "Skye": "Initiator", "Sova": "Initiator", "Tejo": "Initiator",
    "Veto": "Sentinel", "Viper": "Controller", "Vyse": "Sentinel",
    "Waylay": "Duelist", "Yoru": "Duelist",
}

# mapUrl (== the MapID returned by the game) -> display name
FALLBACK_MAPS: Dict[str, str] = {
    "/Game/Maps/Ascent/Ascent": "Ascent",
    "/Game/Maps/Bonsai/Bonsai": "Split",
    "/Game/Maps/Canyon/Canyon": "Fracture",
    "/Game/Maps/Duality/Duality": "Bind",
    "/Game/Maps/Foxtrot/Foxtrot": "Breeze",
    "/Game/Maps/Infinity/Infinity": "Abyss",
    "/Game/Maps/Jam/Jam": "Lotus",
    "/Game/Maps/Juliett/Juliett": "Sunset",
    "/Game/Maps/Pitt/Pitt": "Pearl",
    "/Game/Maps/Plummet/Plummet": "Summit",
    "/Game/Maps/Port/Port": "Icebox",
    "/Game/Maps/Rook/Rook": "Corrode",
    "/Game/Maps/Triad/Triad": "Haven",
    "/Game/Maps/AbilityDraft/AbilityDraft": "Gauntlet",
    "/Game/Maps/Duel/Duel_1/Skirmish_A": "Skirmish A",
    "/Game/Maps/Duel/Duel_2/Skirmish_B": "Skirmish B",
    "/Game/Maps/Duel/Duel_3/Skirmish_C": "Skirmish C",
    "/Game/Maps/Duel/Duel_Platform/Skirmish_D": "Skirmish D",
    "/Game/Maps/Duel/Duel_Heady/Skirmish_E": "Skirmish E",
    "/Game/Maps/HURM/HURM_Alley/HURM_Alley": "District",
    "/Game/Maps/HURM/HURM_Bowl/HURM_Bowl": "Kasbah",
    "/Game/Maps/HURM/HURM_Helix/HURM_Helix": "Drift",
    "/Game/Maps/HURM/HURM_HighTide/HURM_HighTide": "Glitch",
    "/Game/Maps/HURM/HURM_Yard/HURM_Yard": "Piazza",
    "/Game/Maps/NPEV2/NPEV2": "Basic Training",
    "/Game/Maps/Poveglia/Range": "The Range",
    "/Game/Maps/PovegliaV2/RangeV2": "The Range",
}

# Game mode assetPath (== the ModeID returned by the game) -> display name
FALLBACK_GAMEMODES: Dict[str, str] = {
    "ShooterGame/Content/GameModes/Bomb/BombGameMode_PrimaryAsset": "Standard",
    "ShooterGame/Content/GameModes/Deathmatch/DeathmatchGameMode_PrimaryAsset": "Deathmatch",
    "ShooterGame/Content/GameModes/Dodgeball/Dodgeball_GameMode_PrimaryAsset": "Knockout",
    "ShooterGame/Content/GameModes/GunGame/GunGameTeamsGameMode_PrimaryAsset": "Escalation",
    "ShooterGame/Content/GameModes/HURM/HURM_PrimaryAsset": "Team Deathmatch",
    "ShooterGame/Content/GameModes/OneForAll/OneForAll_GameMode_DataAsset_Desktop": "Replication",
    "ShooterGame/Content/GameModes/QuickBomb/QuickBombGameMode_PrimaryAsset": "Spike Rush",
    "ShooterGame/Content/GameModes/_Development/Swiftplay_EndOfRoundCredits/SwiftPlay_GameMode_PrimaryAsset": "Swiftplay",
    "ShooterGame/Content/GameModes/AROS/AROS_GameMode_PrimaryAsset": "All Random One Site",
    "ShooterGame/Content/GameModes/ShootingRange/ShootingRangeGameMode_PrimaryAsset": "The Range",
    "ShooterGame/Content/GameModes/AbilityDraftArena/AbilityDraftArenaGameMode_PrimaryAsset": "Gauntlet: Glitched",
    "ShooterGame/Content/GameModes/Skirmish/SkirmishGameMode_PrimaryAsset": "Skirmish",
    "ShooterGame/Content/GameModes/SkirmishAscension/SkirmishAscensionGameModePrimaryAsset": "Skirmish: Ascension",
    "ShooterGame/Content/GameModes/FortCollins/FortCollins_PrimaryAsset": "Retake",
    "ShooterGame/Content/GameModes/SnowballFight/SnowballFightGameMode_PrimaryAsset": "Snowball Fight",
    "ShooterGame/Content/GameModes/NPEV2/NPEV2_GameMode_PrimaryAsset": "Basic Training",
    "ShooterGame/Content/GameModes/ExamplePlayerTestBot/BotTrainingMatch_GameMode_PrimaryAsset": "Bot Match",
}

# Folder segment of the in-game ModeID (e.g. /Game/GameModes/Bomb/...) -> name.
# Used when no queueId is available, which is the case for custom games.
MODE_FOLDER_NAMES: Dict[str, str] = {
    "bomb": "Standard",
    "deathmatch": "Deathmatch",
    "gungame": "Escalation",
    "hurm": "Team Deathmatch",
    "quickbomb": "Spike Rush",
    "oneforall": "Replication",
    "swiftplay": "Swiftplay",
    "dodgeball": "Knockout",
    "snowballfight": "Snowball Fight",
    "aros": "All Random One Site",
    "abilitydraftarena": "Gauntlet: Glitched",
    "skirmish": "Skirmish",
    "skirmishascension": "Skirmish: Ascension",
    "fortcollins": "Retake",
    "shootingrange": "The Range",
    "npev2": "Basic Training",
    "newplayerexperience": "Onboarding",
    "exampleplayertestbot": "Bot Match",
}

# queueId (from presences / pregame) -> display name. This is the most reliable
# way to tell Competitive from Unrated, which share the same game mode.
#
# The authoritative, always current list is downloaded from /gamemodes/queues;
# these entries are the offline fallback.
QUEUE_NAMES: Dict[str, str] = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "ggteam": "Escalation",
    "hurm": "Team Deathmatch",
    "onefa": "Replication",
    "newmap": "New Map",
    "premier": "Premier",
    "custom": "Custom Game",
    "snowball": "Snowball Fight",
    "dodgeball": "Knockout",
    "valaram": "All Random One Site",
    "abilitydraftarena": "Gauntlet: Glitched",
    "fortcollins": "Retake",
    "skirmish2v2": "Skirmish: 2v2",
    "skirmishascension1v1": "Skirmish: Ascension 1v1",
    "skirmishascension2v2": "Skirmish: Ascension 2v2",
    "": "Unknown",
}


# ════════════════════════════════════════════════════════════════════════════
# PROVIDER
# ════════════════════════════════════════════════════════════════════════════

class ContentProvider:
    """Serves up-to-date game data with a disk cache and an offline fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.client_version: str = FALLBACK_CLIENT_VERSION
        self.agents: Dict[str, str] = dict(FALLBACK_AGENTS)
        self.agent_roles: Dict[str, str] = dict(FALLBACK_AGENT_ROLES)
        self.maps: Dict[str, str] = dict(FALLBACK_MAPS)
        self.gamemodes: Dict[str, str] = dict(FALLBACK_GAMEMODES)
        # queueId -> official name, including limited-time modes
        self.queues: Dict[str, str] = {}
        # tier int -> {"name", "color" (#RRGGBB), "icon" (url)}
        self.tiers: Dict[str, Dict] = {}
        # weapon uuid -> name
        self.weapons: Dict[str, str] = {}
        # chroma/skinlevel uuid -> skin name (lazy loaded, large dataset)
        self.skins: Dict[str, str] = {}
        # UUID of the current competitive act, needed to read the rank from MMR
        self.current_act_id: str = ""
        self._agents_by_uuid: Dict[str, str] = {}
        self._rebuild_indexes()
        # Load any valid cache right away: it is a fast local read.
        self._load_cache()
        self._load_skins_cache()

    # ---- derived indexes -------------------------------------------------

    def _rebuild_indexes(self) -> None:
        self._agents_by_uuid = {v: k for k, v in self.agents.items()}

    # ---- disk cache ------------------------------------------------------

    def _load_cache(self) -> bool:
        try:
            if not os.path.exists(CACHE_FILE):
                return False
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.client_version = data.get("client_version", self.client_version)
            self.agents = data.get("agents") or self.agents
            self.agent_roles = data.get("agent_roles") or self.agent_roles
            self.maps = data.get("maps") or self.maps
            self.gamemodes = data.get("gamemodes") or self.gamemodes
            self.queues = data.get("queues") or self.queues
            self.tiers = data.get("tiers") or self.tiers
            self.weapons = data.get("weapons") or self.weapons
            self.current_act_id = data.get("current_act_id") or self.current_act_id
            self._rebuild_indexes()
            return True
        except Exception:
            return False

    def _load_skins_cache(self) -> None:
        try:
            if os.path.exists(SKINS_CACHE_FILE):
                with open(SKINS_CACHE_FILE, "r", encoding="utf-8") as fh:
                    self.skins = json.load(fh) or {}
        except Exception:
            self.skins = {}

    def _save_skins_cache(self) -> None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(SKINS_CACHE_FILE, "w", encoding="utf-8") as fh:
                json.dump(self.skins, fh, ensure_ascii=False)
        except Exception:
            pass

    def _save_cache(self) -> None:
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            payload = {
                "fetched_at": time.time(),
                "client_version": self.client_version,
                "agents": self.agents,
                "agent_roles": self.agent_roles,
                "maps": self.maps,
                "gamemodes": self.gamemodes,
                "queues": self.queues,
                "tiers": self.tiers,
                "weapons": self.weapons,
                "current_act_id": self.current_act_id,
            }
            with open(CACHE_FILE, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _cache_is_fresh(self) -> bool:
        try:
            if not os.path.exists(CACHE_FILE):
                return False
            with open(CACHE_FILE, "r", encoding="utf-8") as fh:
                fetched_at = json.load(fh).get("fetched_at", 0)
            return (time.time() - fetched_at) < CACHE_TTL
        except Exception:
            return False

    # ---- network refresh -------------------------------------------------

    def _get(self, endpoint: str) -> Optional[object]:
        try:
            resp = requests.get(f"{VALORANT_API_BASE}{endpoint}", timeout=_HTTP_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get("data")
        except Exception:
            pass
        return None

    def refresh(self, force: bool = False) -> bool:
        """Update the data from valorant-api.com if the cache has expired.

        Returns True when a network update actually ran and succeeded.
        """
        with self._lock:
            if not force and self._cache_is_fresh():
                return False

            updated = False

            version = self._get("/version")
            if isinstance(version, dict) and version.get("riotClientVersion"):
                self.client_version = version["riotClientVersion"]
                updated = True

            agents = self._get("/agents?isPlayableCharacter=true")
            if isinstance(agents, list) and agents:
                names, roles = {}, {}
                for a in agents:
                    name = a.get("displayName")
                    uuid = a.get("uuid")
                    if not name or not uuid:
                        continue
                    names[name] = uuid
                    role = a.get("role") or {}
                    roles[name] = role.get("displayName", "")
                if names:
                    self.agents = names
                    self.agent_roles = roles
                    self._rebuild_indexes()
                    updated = True

            maps = self._get("/maps")
            if isinstance(maps, list) and maps:
                mp = {}
                for m in maps:
                    url = m.get("mapUrl")
                    name = m.get("displayName")
                    if url and name:
                        mp[url] = name
                if mp:
                    self.maps = mp
                    updated = True

            modes = self._get("/gamemodes")
            if isinstance(modes, list) and modes:
                gm = {}
                for g in modes:
                    path = g.get("assetPath")
                    name = g.get("displayName")
                    if path and name:
                        gm[path] = name
                if gm:
                    self.gamemodes = gm
                    updated = True

            # Matchmaking queues: the source that immediately covers
            # limited-time modes (Gauntlet, Skirmish, Retake, seasonal events).
            queues = self._get("/gamemodes/queues")
            if isinstance(queues, list) and queues:
                qmap = {}
                for q in queues:
                    qid = q.get("queueId")
                    name = q.get("displayName") or q.get("dropdownText")
                    if qid and name:
                        qmap[qid.lower()] = name
                if qmap:
                    self.queues = qmap
                    updated = True

            tiers = self._get("/competitivetiers")
            if isinstance(tiers, list) and tiers:
                latest = tiers[-1].get("tiers", [])
                tier_map = {}
                for t in latest:
                    tier_map[str(t.get("tier"))] = {
                        "name": (t.get("tierName") or "").title(),
                        "color": "#" + (t.get("color") or "ffffffff")[:6],
                        "icon": t.get("largeIcon") or t.get("smallIcon") or "",
                    }
                if tier_map:
                    self.tiers = tier_map
                    updated = True

            weapons = self._get("/weapons")
            if isinstance(weapons, list) and weapons:
                wmap = {}
                for w in weapons:
                    uuid = w.get("uuid")
                    name = w.get("displayName")
                    if uuid and name:
                        wmap[uuid.lower()] = name
                if wmap:
                    self.weapons = wmap
                    updated = True

            seasons = self._get("/seasons")
            if isinstance(seasons, list) and seasons:
                act_id = self._resolve_current_act(seasons)
                if act_id:
                    self.current_act_id = act_id
                    updated = True

            if updated:
                self._save_cache()
            return updated

    @staticmethod
    def _resolve_current_act(seasons: List[Dict]) -> str:
        """Find the UUID of the competitive act covering the current date."""
        now = datetime.now(timezone.utc)
        best = ""
        for s in seasons:
            if "act" not in (s.get("type") or "").lower():
                continue
            try:
                start = datetime.fromisoformat(s["startTime"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(s["endTime"].replace("Z", "+00:00"))
            except Exception:
                continue
            if start <= now < end:
                return s.get("uuid", "")
        return best

    def ensure_skins(self, force: bool = False) -> bool:
        """Lazily load the chroma/level UUID -> skin name map (large dataset).

        Only needed to display skin NAMES; the images are built straight from
        the UUIDs. Stored in its own cache file.
        """
        if self.skins and not force:
            return True
        with self._lock:
            if self.skins and not force:
                return True
            skins: Dict[str, str] = {}

            def clean(name: Optional[str]) -> str:
                """Collapse a display name onto a single line.

                A displayName may carry a second line holding either the chroma
                variant, which is worth keeping, or the description of the level
                effect, which is not. Variants are the ones in parentheses.
                """
                lines = [" ".join(l.split()) for l in (name or "").splitlines()]
                lines = [l for l in lines if l]
                if not lines:
                    return ""
                label = lines[0]
                if len(lines) > 1 and lines[1].startswith("("):
                    label += " " + lines[1]
                return label

            data = self._get("/weapons/skinchromas")
            if isinstance(data, list):
                for c in data:
                    uuid = c.get("uuid")
                    name = clean(c.get("displayName"))
                    if uuid and name:
                        skins[uuid.lower()] = name
            levels = self._get("/weapons/skinlevels")
            if isinstance(levels, list):
                for lv in levels:
                    uuid = lv.get("uuid")
                    name = clean(lv.get("displayName"))
                    if uuid and name:
                        skins[uuid.lower()] = name
            if skins:
                self.skins = skins
                self._save_skins_cache()
                return True
            return False

    # ---- lookups ---------------------------------------------------------

    def get_agent_uuid(self, name: str) -> Optional[str]:
        if name in self.agents:
            return self.agents[name]
        low = name.lower()
        for n, u in self.agents.items():
            if n.lower() == low:
                return u
        return None

    def get_agent_name(self, uuid: str) -> str:
        if not uuid:
            return ""
        return self._agents_by_uuid.get(uuid.lower(), self._agents_by_uuid.get(uuid, "Unknown"))

    def get_agent_role(self, name: str) -> str:
        return self.agent_roles.get(name, "")

    def list_agents(self) -> List[str]:
        return sorted(self.agents.keys())

    def get_map_name(self, map_id: str) -> str:
        """Turn a MapID (full path or codename) into its display name."""
        if not map_id:
            return "Unknown"
        if map_id in self.maps:
            return self.maps[map_id]
        # Retry matching on the trailing codename only (e.g. "Triad" -> Haven)
        codename = map_id.rstrip("/").split("/")[-1]
        for url, name in self.maps.items():
            if url.rstrip("/").split("/")[-1].lower() == codename.lower():
                return name
        return codename or "Unknown"

    @staticmethod
    def _mode_folder_key(mode_id: str) -> str:
        """Extract the mode folder from an in-game ModeID.

        e.g. '/Game/GameModes/Bomb/BombGameMode.BombGameMode_C' -> 'bomb'
        """
        low = (mode_id or "").lower()
        marker = "gamemodes/"
        idx = low.find(marker)
        if idx == -1:
            return ""
        rest = low[idx + len(marker):]
        return rest.split("/")[0] if rest else ""

    def get_mode_name(self, mode_id: str = "", queue_id: str = "",
                      provisioning: str = "") -> str:
        """Display name of the game mode.

        Resolution order: custom game -> queueId -> ModeID folder -> assetPath.
        """
        if (provisioning or "").lower() == "customgame":
            return "Custom Game"

        q = (queue_id or "").lower()
        # Console queues carry the same names behind a "console_" prefix.
        if q.startswith("console_"):
            q = q[len("console_"):]
        if q:
            # The downloaded list wins: it already names limited-time modes
            # that this file cannot know about.
            if q in self.queues:
                return self.queues[q]
            if q in QUEUE_NAMES:
                return QUEUE_NAMES[q]
        # In-game ModeID (/Game/GameModes/<folder>/...)
        folder = self._mode_folder_key(mode_id)
        if folder in MODE_FOLDER_NAMES:
            return MODE_FOLDER_NAMES[folder]
        # valorant-api assetPath, e.g. the one carried by presences
        if mode_id in self.gamemodes:
            return self.gamemodes[mode_id]
        low = (mode_id or "").lower()
        # Careful: with an empty mode_id a partial match would hit every path
        # and return an arbitrary mode.
        if low:
            for path, name in self.gamemodes.items():
                if path.lower() in low or low in path.lower():
                    return name
        return q or "Unknown"

    def is_deathmatch(self, mode_id: str = "", queue_id: str = "",
                      provisioning: str = "") -> bool:
        """True for free-for-all Deathmatch, where there is no enemy team."""
        name = self.get_mode_name(mode_id, queue_id, provisioning).lower()
        return "deathmatch" in name and "team" not in name

    # ---- ranks -----------------------------------------------------------

    def get_rank_info(self, tier: int) -> Dict:
        """Rank details for a numeric competitive tier (0-27)."""
        info = self.tiers.get(str(tier))
        if info:
            return info
        return {"name": "Unranked" if tier == 0 else "—", "color": "#7a7a7a", "icon": ""}

    def get_rank_name(self, tier: int) -> str:
        return self.get_rank_info(tier).get("name", "—")

    # ---- images ----------------------------------------------------------

    def agent_icon(self, uuid: str) -> str:
        return f"{MEDIA}/agents/{uuid}/displayicon.png" if uuid else ""

    def skin_render(self, chroma_or_level_uuid: str) -> str:
        if not chroma_or_level_uuid:
            return ""
        return f"{MEDIA}/weaponskinchromas/{chroma_or_level_uuid}/fullrender.png"

    def buddy_icon(self, uuid: str) -> str:
        return f"{MEDIA}/buddies/{uuid}/displayicon.png" if uuid else ""

    def player_card_icon(self, uuid: str) -> str:
        return f"{MEDIA}/playercards/{uuid}/smallart.png" if uuid else ""

    def rank_icon(self, tier: int) -> str:
        return self.get_rank_info(tier).get("icon", "")

    # ---- weapons and skins -----------------------------------------------

    def get_weapon_name(self, uuid: str) -> str:
        return self.weapons.get((uuid or "").lower(), "")

    def get_skin_name(self, uuid: str) -> str:
        if not uuid:
            return ""
        return self.skins.get(uuid.lower(), "")


# Shared module-level instance.
content = ContentProvider()
