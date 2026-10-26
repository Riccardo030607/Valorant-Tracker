"""
Client for the local and remote VALORANT APIs.

Handles reading the lockfile, authentication, game state detection, agent
select and lock, and name resolution, including revealing players who are in
Incognito mode through Riot's Name Service.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Dict, List, Optional, Tuple

import requests
import urllib3

from valorant_content import content

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# The region returned by /chat/v1/session is the CHAT affinity, which is more
# granular than the GAME regions (e.g. "jp", "oce", "eu2"). It has to be
# normalised to a valid game affinity or the glz/pd hosts simply do not exist.
# Valid game affinities: na, eu, ap, kr, latam, br.
CHAT_REGION_TO_AFFINITY = {
    "na": "na", "us": "na", "use": "na", "usw": "na",
    "latam": "latam", "la": "latam",
    "br": "br",
    "eu": "eu", "euw": "eu", "eun": "eu", "ru": "eu", "tr": "eu", "me": "eu",
    "kr": "kr",
    "ap": "ap", "jp": "ap", "oce": "ap", "oc": "ap", "sea": "ap",
    "as": "ap", "apac": "ap", "sa": "ap", "ph": "ap", "th": "ap", "vn": "ap",
}

# Game affinity -> shard. For most affinities shard == affinity, but latam and
# br both live on the "na" shard. The wrong shard makes every request fail.
REGION_TO_SHARD = {
    "eu": "eu",
    "na": "na",
    "latam": "na",
    "br": "na",
    "ap": "ap",
    "kr": "kr",
}


def resolve_affinity(region_raw: str):
    """Map a chat region (e.g. 'jp', 'eu2') to (game_affinity, shard).

    Trailing digits are stripped (eu1 -> eu) and granular chat regions are
    folded into the matching game affinity. Falls back to EU when unknown.
    """
    r = (region_raw or "").lower().strip()
    base = r.rstrip("0123456789")
    affinity = CHAT_REGION_TO_AFFINITY.get(base) or CHAT_REGION_TO_AFFINITY.get(r) or "eu"
    shard = REGION_TO_SHARD.get(affinity, affinity)
    return affinity, shard


class ValorantAPIError(Exception):
    """Generic VALORANT API error."""


class ValorantRemoteAPI:
    """Client for the local and remote VALORANT APIs."""

    def __init__(self) -> None:
        self.access_token: Optional[str] = None
        self.entitlement_token: Optional[str] = None
        self.puuid: Optional[str] = None
        self.region: Optional[str] = None
        self.shard: Optional[str] = None
        self.chat_region: Optional[str] = None
        self.lockfile: Optional[Dict] = None
        self.client_version: str = content.client_version
        self.glz_base_url: Optional[str] = None
        self.pd_base_url: Optional[str] = None
        self.local_base_url: Optional[str] = None
        self._session = requests.Session()
        self._session.verify = False
        self._client_platform = base64.b64encode(json.dumps({
            "platformType": "PC",
            "platformOS": "Windows",
            "platformOSVersion": "10.0.19042.1.256.64bit",
            "platformChipset": "Unknown",
        }).encode()).decode()

    # ════════════════════════════════════════════════════════════════════
    # CONNECTION / AUTHENTICATION
    # ════════════════════════════════════════════════════════════════════

    def _read_lockfile(self) -> Optional[Dict]:
        """Read the Riot Client lockfile holding the local API credentials."""
        local_appdata = os.getenv("LOCALAPPDATA")
        if not local_appdata:
            return None
        lockfile_path = os.path.join(
            local_appdata, "Riot Games", "Riot Client", "Config", "lockfile"
        )
        try:
            with open(lockfile_path, "r") as fh:
                parts = fh.read().strip().split(":")
            if len(parts) < 5:
                return None
            return {
                "name": parts[0],
                "pid": parts[1],
                "port": parts[2],
                "password": parts[3],
                "protocol": parts[4],
            }
        except (FileNotFoundError, OSError):
            return None

    def _local_headers(self) -> Dict[str, str]:
        credentials = f"riot:{self.lockfile['password']}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/json",
        }

    def _make_local_request(self, endpoint: str, method: str = "GET",
                            data: Optional[Dict] = None) -> Optional[Dict]:
        """Call the Riot Client local API on 127.0.0.1."""
        if not self.lockfile:
            return None
        url = f"{self.lockfile['protocol']}://127.0.0.1:{self.lockfile['port']}{endpoint}"
        try:
            resp = self._session.request(
                method, url, headers=self._local_headers(), json=data, timeout=5
            )
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        return None

    def _remote_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "X-Riot-Entitlements-JWT": self.entitlement_token,
            "X-Riot-ClientPlatform": self._client_platform,
            "X-Riot-ClientVersion": self.client_version,
            "Content-Type": "application/json",
        }

    def _make_remote_request(self, base_url: str, method: str, endpoint: str,
                             data=None, retries: int = 2) -> Optional[Dict]:
        """Call a remote Riot endpoint (glz/pd) with a light retry policy."""
        if not (self.access_token and self.entitlement_token):
            return None
        url = f"{base_url}{endpoint}"
        for attempt in range(retries + 1):
            try:
                resp = self._session.request(
                    method, url, headers=self._remote_headers(), json=data, timeout=10
                )
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError:
                        return None
                if resp.status_code == 404:
                    return None  # not in agent select or a match: expected
                if resp.status_code in (401, 403):
                    # Stale token or client version: retry once after a refresh
                    if attempt == 0 and self._refresh_tokens():
                        continue
                    return None
                # Any other status: short retry
            except requests.RequestException:
                pass
            if attempt < retries:
                time.sleep(0.4)
        return None

    def _glz(self, method: str, endpoint: str, data=None) -> Optional[Dict]:
        return self._make_remote_request(self.glz_base_url, method, endpoint, data)

    def _pd(self, method: str, endpoint: str, data=None) -> Optional[Dict]:
        return self._make_remote_request(self.pd_base_url, method, endpoint, data)

    def _refresh_tokens(self) -> bool:
        """Re-read the tokens from the local API, in case they expired."""
        token_data = self._make_local_request("/entitlements/v1/token")
        if not token_data:
            return False
        access = token_data.get("accessToken")
        entitlement = token_data.get("token")
        if access and entitlement:
            self.access_token = access
            self.entitlement_token = entitlement
            return True
        return False

    def connect(self) -> bool:
        """Connect and acquire every token and endpoint needed."""
        self.lockfile = self._read_lockfile()
        if not self.lockfile:
            return False

        if not self._refresh_tokens():
            return False

        session = self._make_local_request("/chat/v1/session")
        if not session:
            return False

        self.puuid = session.get("puuid")
        self.chat_region = (session.get("region") or "").lower()
        self.region, self.shard = resolve_affinity(self.chat_region)

        self.glz_base_url = f"https://glz-{self.region}-1.{self.shard}.a.pvp.net"
        self.pd_base_url = f"https://pd.{self.shard}.a.pvp.net"
        self.local_base_url = f"{self.lockfile['protocol']}://127.0.0.1:{self.lockfile['port']}"

        # Align the client version with the most recent build available.
        self.client_version = content.client_version
        return bool(self.puuid)

    # ════════════════════════════════════════════════════════════════════
    # GAME STATE
    # ════════════════════════════════════════════════════════════════════

    def _own_valorant_private(self) -> Optional[Dict]:
        """Decoded private data of our own VALORANT presence, or None."""
        presences = self.get_presences()
        if not presences:
            return None
        for presence in presences:
            if presence.get("puuid") == self.puuid and presence.get("product") == "valorant":
                return self._decode_private_data(presence.get("private", ""))
        return None

    def is_client_reachable(self) -> bool:
        """True when the Riot Client local API responds."""
        return self._make_local_request("/chat/v1/session") is not None

    def is_game_running(self) -> bool:
        """True when VALORANT itself is running, not just the Riot Client.

        Detected through our own presence with product 'valorant': with the
        game closed only the 'riot_client' one remains.
        """
        return self._own_valorant_private() is not None

    def detect_state(self) -> Tuple[str, Optional[str]]:
        """Current state and MatchID.

        Returns ('DISCONNECTED' | 'MENU' | 'PREGAME' | 'INGAME', match_id).

        The local presence is the primary source because it is immediate and
        reliable. Remote calls only resolve the MatchID: when they fail, which
        happens during the first seconds of agent select, the state still
        reflects the presence. Otherwise the interface would stay stuck on the
        waiting screen even after a match had started.
        """
        local = self.get_session_loop_state()

        if local is None:
            # No VALORANT presence: game closed, or client unreachable.
            if not self.is_client_reachable():
                return "DISCONNECTED", None
            # Client up, game possibly still starting: try the remote anyway.
            core = self.get_coregame_player()
            if core and core.get("MatchID"):
                return "INGAME", core["MatchID"]
            pre = self.get_pregame_player()
            if pre and pre.get("MatchID"):
                return "PREGAME", pre["MatchID"]
            return "DISCONNECTED", None

        if local == "PREGAME":
            pre = self.get_pregame_player()
            return "PREGAME", (pre or {}).get("MatchID")

        if local == "INGAME":
            core = self.get_coregame_player()
            return "INGAME", (core or {}).get("MatchID")

        return "MENU", None

    def get_session_loop_state(self) -> Optional[str]:
        """State from the local presence: 'MENUS' | 'PREGAME' | 'INGAME'.

        This is a fast LOCAL call, ideal for continuous polling without
        hammering the remote APIs. In the current presence format the state
        lives inside `matchPresenceData`.
        """
        private = self._own_valorant_private()
        if not private:
            return None
        match_data = private.get("matchPresenceData", {})
        state = match_data.get("sessionLoopState") or private.get("sessionLoopState")
        return state.upper() if state else None

    def get_pregame_player(self) -> Optional[Dict]:
        return self._glz("GET", f"/pregame/v1/players/{self.puuid}")

    def get_pregame_match(self, match_id: str) -> Optional[Dict]:
        return self._glz("GET", f"/pregame/v1/matches/{match_id}")

    def get_coregame_player(self) -> Optional[Dict]:
        return self._glz("GET", f"/core-game/v1/players/{self.puuid}")

    def get_coregame_match(self, match_id: str) -> Optional[Dict]:
        return self._glz("GET", f"/core-game/v1/matches/{match_id}")

    def get_coregame_loadouts(self, match_id: str) -> Optional[Dict]:
        """Loadouts (skins, sprays, buddies) of every player in the match."""
        return self._glz("GET", f"/core-game/v1/matches/{match_id}/loadouts")

    def get_player_mmr(self, puuid: str) -> Optional[Dict]:
        """Official MMR of a player: current rank, peak, and RR.

        GET pd.{shard}.a.pvp.net/mmr/v1/players/{puuid}
        """
        if not puuid:
            return None
        return self._pd("GET", f"/mmr/v1/players/{puuid}")

    # ════════════════════════════════════════════════════════════════════
    # AGENT SELECTION
    # ════════════════════════════════════════════════════════════════════

    def select_agent(self, match_id: str, agent_id: str) -> bool:
        result = self._glz("POST", f"/pregame/v1/matches/{match_id}/select/{agent_id}")
        return result is not None

    def lock_agent(self, match_id: str, agent_id: str) -> bool:
        result = self._glz("POST", f"/pregame/v1/matches/{match_id}/lock/{agent_id}")
        return result is not None

    # ════════════════════════════════════════════════════════════════════
    # PLAYER NAMES
    # ════════════════════════════════════════════════════════════════════

    def get_presences(self) -> Optional[List[Dict]]:
        """Player presences (friends and party) from the local API."""
        response = self._make_local_request("/chat/v4/presences")
        if response:
            return response.get("presences", [])
        return None

    def get_player_names_by_puuids(self, puuids: List[str]) -> Dict[str, Dict]:
        """Resolve names through Riot's official Name Service.

        PUT pd.{shard}.a.pvp.net/name-service/v2/players

        This endpoint returns the real GameName and TagLine even for players in
        Incognito mode, since Incognito only hides them in the game's own UI.
        """
        puuids = [p for p in puuids if p]
        if not puuids:
            return {}
        result = self._pd("PUT", "/name-service/v2/players", puuids)
        if not isinstance(result, list):
            return {}
        names = {}
        for player in result:
            puuid = player.get("Subject", "")
            game_name = player.get("GameName", "")
            tag_line = player.get("TagLine", "")
            if puuid and game_name:
                names[puuid] = {"name": game_name, "tag": tag_line}
        return names

    def _decode_private_data(self, private_data) -> Dict:
        """Decode the base64+JSON 'private' field of a presence."""
        if isinstance(private_data, dict):
            return private_data
        if isinstance(private_data, str) and private_data:
            try:
                return json.loads(base64.b64decode(private_data))
            except Exception:
                try:
                    return json.loads(private_data)
                except Exception:
                    pass
        return {}

    def get_player_names_from_presences(self) -> Dict[str, Dict]:
        """Player names taken from presences (friends and party).

        Note: the current presence format no longer exposes name and tag in
        clear text, so this is only a best-effort fallback. Reliable resolution
        goes through the Name Service (`get_player_names_by_puuids`).
        """
        players: Dict[str, Dict] = {}
        presences = self.get_presences()
        if not presences:
            return players
        for presence in presences:
            if presence.get("product") != "valorant":
                continue
            puuid = presence.get("puuid", "")
            name = presence.get("game_name", "")
            tag = presence.get("game_tag", "")
            if puuid and name:
                players[puuid] = {"name": name, "tag": tag}
        return players

    def get_my_queue_id(self) -> str:
        """Current queueId from our own presence, inside matchPresenceData."""
        private = self._own_valorant_private()
        if not private:
            return ""
        match_data = private.get("matchPresenceData", {})
        return (match_data.get("queueId") or private.get("queueId") or "").lower()

    # ════════════════════════════════════════════════════════════════════
    # MATCH PLAYER AGGREGATION
    # ════════════════════════════════════════════════════════════════════

    def get_match_players_with_names(self, match_info: Dict,
                                     is_pregame: bool = True) -> Dict:
        """Extract the players of a match with resolved names and metadata.

        Returns a dict with 'allies', 'enemies', 'my_team_id', 'mode',
        'queue_id', 'map_id' and 'map_name'.
        """
        result = {
            "allies": [],
            "enemies": [],
            "my_team_id": None,
            "mode": "unknown",
            "queue_id": "",
            "map_id": "",
            "map_name": "Unknown",
        }
        if not match_info:
            return result

        mode_id = match_info.get("ModeID", "")
        queue_id = (match_info.get("QueueID") or self.get_my_queue_id() or "").lower()
        provisioning = match_info.get("ProvisioningFlow", "") or match_info.get("ProvisioningFlowID", "")
        map_id = match_info.get("MapID", "")

        result["mode"] = content.get_mode_name(mode_id, queue_id, provisioning)
        result["queue_id"] = queue_id
        result["provisioning"] = provisioning
        result["map_id"] = map_id
        result["map_name"] = content.get_map_name(map_id)
        is_deathmatch = content.is_deathmatch(mode_id, queue_id, provisioning)

        if is_pregame:
            raw_players = match_info.get("AllyTeam", {}).get("Players", [])
            my_team_id = match_info.get("AllyTeam", {}).get("TeamID", "")
        else:
            raw_players = match_info.get("Players", [])
            my_team_id = ""
            for player in raw_players:
                if player.get("Subject", "") == self.puuid:
                    my_team_id = player.get("TeamID", "")
                    break
        result["my_team_id"] = my_team_id

        # A single Name Service call covers every puuid at once.
        all_puuids = [p.get("Subject", "") for p in raw_players if p.get("Subject")]
        riot_names = self.get_player_names_by_puuids(all_puuids)
        presence_names = self.get_player_names_from_presences()

        for player in raw_players:
            puuid = player.get("Subject", "")
            agent_id = player.get("CharacterID", "")
            team_id = player.get("TeamID", "")
            identity = player.get("PlayerIdentity", {})
            is_incognito = identity.get("Incognito", False)

            # 1) Official Name Service, which also resolves Incognito players
            name, tag = "", ""
            info = riot_names.get(puuid)
            if info:
                name, tag = info["name"], info["tag"]
            # 2) Fallback: local presences (friends and party)
            if not name:
                info = presence_names.get(puuid)
                if info:
                    name, tag = info["name"], info["tag"]
            # 3) Last resort
            if not name:
                name = "Anonymous" if is_incognito else "Player"

            player_data = {
                "puuid": puuid,
                "name": name,
                "tag": tag,
                "agent": agent_id,
                "agent_locked": player.get("CharacterSelectionState", "") == "locked",
                "is_private": is_incognito,
                "is_me": puuid == self.puuid,
                "is_captain": player.get("IsCaptain", False),
            }

            if is_pregame or is_deathmatch:
                result["allies"].append(player_data)
            elif team_id == my_team_id:
                result["allies"].append(player_data)
            else:
                result["enemies"].append(player_data)

        return result


# ════════════════════════════════════════════════════════════════════════════
# QUICK SELF-TEST
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=" * 60)
    print("VALORANT REMOTE API TEST")
    print("=" * 60)

    content.refresh()
    api = ValorantRemoteAPI()

    if not api.connect():
        print("Connection failed. Is VALORANT running?")
        sys.exit(1)

    print(f"Connected. PUUID: {api.puuid[:8]}...  Region: {api.region}  Shard: {api.shard}")
    print(f"Client version: {api.client_version}")
    print(f"State: {api.get_session_loop_state()}")
    print(f"Queue: {api.get_my_queue_id() or 'n/a'}")
