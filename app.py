"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                           VALORANT TRACKER                                   ║
║                     Automatic match monitoring                               ║
╚══════════════════════════════════════════════════════════════════════════════╝

Features:
  • Per-match instalock of the agent of your choice
  • Real names, including players in Incognito mode
  • Team, map, and mode info, kept current from valorant-api.com
  • Player statistics (rank, peak, K/D, HS%, win rate)
"""

import os
import sys
import time
from typing import Dict, List, Optional

# Force UTF-8 on Windows, needed for emoji and box drawing characters.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    os.system("chcp 65001 > nul 2>&1")

from rich.align import Align
from rich.box import HEAVY, SIMPLE_HEAD
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.prompt import Prompt
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from valorant_content import content
from valorant_remote_api import ValorantRemoteAPI
from player_stats import (
    HENRIK_API_KEY,
    PlayerStats,
    get_rank_color,
    load_stats_parallel,
)

console = Console()

REFRESH_RATE = 1.0          # game state polling interval, in seconds
AGENTS_PER_ROW = 3          # columns in the agent selection grid

ROLE_STYLES = {
    "Duelist": "bold red",
    "Initiator": "bold yellow",
    "Controller": "bold magenta",
    "Sentinel": "bold cyan",
    "": "white",
}


# ════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ════════════════════════════════════════════════════════════════════════════

def banner() -> Panel:
    title = Text("VALORANT TRACKER", style="bold white")
    subtitle = Text("Match monitoring • Instalock • Name reveal", style="dim")
    return Panel(
        Align.center(Group(Align.center(title), Align.center(subtitle))),
        box=HEAVY,
        border_style="red",
        padding=(0, 2),
    )


def clear():
    console.clear()


def rank_text(stats: Optional[PlayerStats]) -> Text:
    if not stats or stats.current_rank in ("", "Unknown"):
        return Text("—", style="grey42")
    color = get_rank_color(stats.current_rank)
    label = stats.current_rank
    if stats.mmr:
        label += f" ({stats.mmr})"
    return Text(label, style=color)


def player_table(title: str, players: List[Dict], stats: Dict[str, PlayerStats],
                 border: str) -> Table:
    table = Table(title=title, box=SIMPLE_HEAD, border_style=border,
                  title_style=f"bold {border}", expand=True, padding=(0, 1))
    table.add_column("Player", style="white", no_wrap=True)
    table.add_column("Agent", style="cyan")
    table.add_column("Rank", justify="left")
    table.add_column("Peak", justify="left")
    table.add_column("K/D", justify="right")
    table.add_column("HS%", justify="right")
    table.add_column("WR%", justify="right")

    for p in players:
        name = p.get("name", "Unknown")
        tag = p.get("tag", "")
        st = stats.get(f"{name}#{tag}")

        # Name with its markers
        name_str = Text()
        if p.get("is_me"):
            name_str.append("★ ", style="bold yellow")
        if p.get("is_captain"):
            name_str.append("👑 ", style="yellow")
        if p.get("is_private"):
            name_str.append("👻 ", style="magenta")
        full = f"{name}#{tag}" if tag else name
        name_str.append(full, style="bold white" if p.get("is_me") else "white")

        # Agent
        agent_name = content.get_agent_name(p.get("agent", "")) or "—"
        agent_cell = Text(agent_name if agent_name != "Unknown" else "—")
        if p.get("agent_locked"):
            agent_cell.append(" 🔒")

        # Statistics
        peak = Text("—", style="grey42")
        kd = hs = wr = Text("—", style="grey42")
        if st:
            if st.peak_rank not in ("", "Unknown"):
                peak = Text(st.peak_rank, style=get_rank_color(st.peak_rank))
            if st.kd_ratio > 0:
                kd = Text(f"{st.kd_ratio:.2f}")
            if st.headshot_pct > 0:
                hs = Text(f"{st.headshot_pct:.0f}")
            if st.win_rate > 0:
                wr = Text(f"{st.win_rate:.0f}")

        table.add_row(name_str, agent_cell, rank_text(st), peak, kd, hs, wr)

    return table


def deathmatch_table(players: List[Dict], stats: Dict[str, PlayerStats]) -> Table:
    table = Table(title="💀 PLAYERS (Deathmatch)", box=SIMPLE_HEAD,
                  border_style="yellow", title_style="bold yellow", expand=True)
    table.add_column("Player", style="white")
    table.add_column("Agent", style="cyan")
    table.add_column("Rank")
    table.add_column("Peak")
    for p in players:
        name = p.get("name", "Unknown")
        tag = p.get("tag", "")
        st = stats.get(f"{name}#{tag}")
        name_str = Text()
        if p.get("is_me"):
            name_str.append("★ ", style="bold yellow")
        if p.get("is_private"):
            name_str.append("👻 ", style="magenta")
        name_str.append(f"{name}#{tag}" if tag else name)
        peak = Text("—", style="grey42")
        if st and st.peak_rank not in ("", "Unknown"):
            peak = Text(st.peak_rank, style=get_rank_color(st.peak_rank))
        agent_name = content.get_agent_name(p.get("agent", "")) or "—"
        table.add_row(name_str, agent_name, rank_text(st), peak)
    return table


# ════════════════════════════════════════════════════════════════════════════
# APP
# ════════════════════════════════════════════════════════════════════════════

class ValorantTrackerApp:

    def __init__(self):
        self.api = ValorantRemoteAPI()
        self.running = True
        self.current_match_id: Optional[str] = None
        self.show_stats = bool(HENRIK_API_KEY)
        self.stats_cache: Dict[str, PlayerStats] = {}
        self.last_agent_choice: Optional[str] = None  # remembered as a default
        self.locked_match_id: Optional[str] = None    # avoids a double lock

    # ---- connection ------------------------------------------------------

    def connect(self) -> bool:
        clear()
        console.print(banner())
        with console.status("[cyan]Updating game data (agents, maps, modes)...", spinner="dots"):
            content.refresh()
        with console.status("[cyan]Connecting to VALORANT...", spinner="dots"):
            ok = self.api.connect()

        if not ok:
            console.print(Panel(
                "[bold red]Could not connect to VALORANT.[/]\n\n"
                "Make sure the game is fully started, not just the launcher, "
                "and that you have reached the main menu.",
                title="❌ Connection error", border_style="red"))
            return False

        info = Table.grid(padding=(0, 2))
        info.add_column(style="dim")
        info.add_column(style="bold white")
        info.add_row("Region / Shard", f"{self.api.region.upper()} / {self.api.shard.upper()}")
        info.add_row("Client version", self.api.client_version)
        info.add_row("Extra statistics", "[green]Enabled[/]" if self.show_stats
                     else "[yellow]Disabled (no HENRIK_API_KEY)[/]")
        console.print(Panel(info, title="✅ Connected", border_style="green"))
        return True

    # ---- agent selection -------------------------------------------------

    def _agents_sorted(self) -> List[str]:
        return content.list_agents()

    def _render_agents_grid(self):
        agents = self._agents_sorted()
        grid = Table.grid(padding=(0, 2))
        for _ in range(AGENTS_PER_ROW):
            grid.add_column()
        row = []
        for i, name in enumerate(agents, 1):
            role = content.get_agent_role(name)
            style = ROLE_STYLES.get(role, "white")
            cell = Text(f"{i:2}. ", style="dim")
            cell.append(name, style=style)
            row.append(cell)
            if len(row) == AGENTS_PER_ROW:
                grid.add_row(*row)
                row = []
        if row:
            while len(row) < AGENTS_PER_ROW:
                row.append("")
            grid.add_row(*row)

        legend = Text()
        for role, style in [("Duelist", "bold red"), ("Initiator", "bold yellow"),
                            ("Controller", "bold magenta"), ("Sentinel", "bold cyan")]:
            legend.append(f"● {role}  ", style=style)
        console.print(Panel(Group(grid, Text(), Align.center(legend)),
                            title="🎮 Agents", border_style="blue"))

    def _resolve_agent(self, user_input: str) -> Optional[str]:
        agents = self._agents_sorted()
        user_input = user_input.strip()
        # by number
        if user_input.isdigit():
            idx = int(user_input)
            if 1 <= idx <= len(agents):
                return content.get_agent_uuid(agents[idx - 1])
            return None
        # by exact name
        uuid = content.get_agent_uuid(user_input)
        if uuid:
            return uuid
        # by unambiguous partial match
        matches = [a for a in agents if user_input.lower() in a.lower()]
        if len(matches) == 1:
            return content.get_agent_uuid(matches[0])
        return None

    def _do_lock(self, agent_uuid: str) -> bool:
        """Select and lock an agent, retrying a few times."""
        agent_name = content.get_agent_name(agent_uuid)
        for attempt in range(5):
            if self.api.select_agent(self.current_match_id, agent_uuid):
                time.sleep(0.25)
                if self.api.lock_agent(self.current_match_id, agent_uuid):
                    console.print(f"[bold green]🔒 {agent_name} LOCKED[/]")
                    return True
            time.sleep(0.4)
        console.print(f"[yellow]Could not lock {agent_name}. Already taken?[/]")
        return False

    # ---- state handlers --------------------------------------------------

    def handle_pregame(self):
        clear()
        console.print(banner())

        match_info = self.api.get_pregame_match(self.current_match_id)
        mode_id = match_info.get("ModeID", "") if match_info else ""
        provisioning = (match_info.get("ProvisioningFlow", "") if match_info else "")
        queue_id = self.api.get_my_queue_id()
        mode_name = content.get_mode_name(mode_id, queue_id, provisioning)
        map_name = content.get_map_name(match_info.get("MapID", "") if match_info else "")

        console.print(Panel(
            f"[bold]Mode:[/] {mode_name}    [bold]Map:[/] {map_name}",
            title="🎯 AGENT SELECT", border_style="red"))

        is_dm = content.is_deathmatch(mode_id, queue_id, provisioning)

        if is_dm:
            console.print("[dim]Deathmatch: no agent selection required.[/]")
        elif self.locked_match_id == self.current_match_id:
            console.print("[dim]Agent already handled for this match.[/]")
        else:
            # Ask every match, so the choice always fits the situation.
            self._render_agents_grid()
            default = self.last_agent_choice or ""
            choice = Prompt.ask(
                "[bold red]➤ Agent to instalock[/] (name or number, empty to skip)",
                default=default).strip()
            self.locked_match_id = self.current_match_id
            if choice:
                self.last_agent_choice = choice
                uuid = self._resolve_agent(choice)
                if uuid:
                    self._do_lock(uuid)
                else:
                    console.print("[yellow]Agent not recognised, selection skipped.[/]")
            else:
                console.print("[dim]Skipped: pick your agent in game.[/]")

        # Team info and statistics
        if match_info:
            data = self.api.get_match_players_with_names(match_info, is_pregame=True)
            self._render_match(data)

        console.print("\n[dim]⏳ Waiting for the match to start...[/]")
        self._wait_while(lambda s: s == "PREGAME")

    def handle_ingame(self):
        clear()
        console.print(banner())

        match_info = (self.api.get_coregame_match(self.current_match_id)
                      if self.current_match_id else None)

        if not match_info:
            console.print(Panel("[yellow]Could not read the match details.[/]",
                                border_style="yellow"))
            self._wait_while(lambda s: s == "INGAME")
            return

        data = self.api.get_match_players_with_names(match_info, is_pregame=False)
        console.print(Panel(
            f"[bold]Mode:[/] {data['mode']}    [bold]Map:[/] {data['map_name']}",
            title="⚔️ MATCH IN PROGRESS", border_style="green"))
        self._render_match(data)

        console.print("\n[dim]⏳ Monitoring the match...[/]")
        self._wait_while(lambda s: s == "INGAME")

    def _render_match(self, data: Dict):
        allies = data.get("allies", [])
        enemies = data.get("enemies", [])
        is_dm = content.is_deathmatch(queue_id=data.get("queue_id", ""),
                                      provisioning=data.get("provisioning", ""))

        stats: Dict[str, PlayerStats] = {}
        if self.show_stats:
            all_players = allies + enemies
            with console.status("[cyan]Loading player statistics...", spinner="dots"):
                stats = load_stats_parallel(
                    all_players, region=self.api.region or "eu",
                    cache=self.stats_cache)

        if is_dm:
            console.print(deathmatch_table(allies + enemies, stats))
            return

        console.print(player_table("🟢 YOUR TEAM", allies, stats, border="green"))
        if enemies:
            console.print(player_table("🔴 ENEMY TEAM", enemies, stats, border="red"))

    def handle_menu(self):
        self.stats_cache.clear()
        self.locked_match_id = None
        self._wait_menu()

    # ---- waiting loops ---------------------------------------------------

    def _detect_state(self) -> str:
        """Current state (DISCONNECTED/MENU/PREGAME/INGAME), tracking MatchID."""
        state, match_id = self.api.detect_state()
        if match_id:
            self.current_match_id = match_id
        return state

    def _wait_while(self, predicate):
        """Block while predicate(state) holds, then return."""
        while self.running:
            state = self._detect_state()
            if not predicate(state):
                return
            time.sleep(REFRESH_RATE)

    def _wait_screen(self, title: str, border: str, spinner_text: str,
                     lines: List[str], stay_while) -> None:
        """Animated waiting screen, drawn once.

        The panel is deliberately compact: once the content outgrows the
        terminal height, rich.Live can no longer update in place and reprints
        everything on each refresh, which looks like duplicated text.
        """
        clear()
        spinner = Spinner("dots", text=Text(f" {spinner_text}", style="cyan"))
        body = Group(spinner, Text(), *[Text(l, style="dim") for l in lines])
        screen = Group(banner(), Panel(body, border_style=border, title=title))
        with Live(screen, console=console, refresh_per_second=4,
                  transient=True, vertical_overflow="crop"):
            while self.running:
                if not stay_while(self._detect_state()):
                    return
                time.sleep(REFRESH_RATE)

    def _wait_menu(self):
        """Waiting screen shown while in the VALORANT menu."""
        self._wait_screen(
            title="🔍 Monitoring",
            border="blue",
            spinner_text="Waiting for a match...",
            lines=[
                "Start a queue in VALORANT.",
                "At agent select you will be asked which agent to instalock.",
                "Ctrl+C to quit.",
            ],
            stay_while=lambda s: s == "MENU",
        )

    def handle_disconnected(self):
        """VALORANT is closed: wait for it to come back."""
        self._wait_screen(
            title="⏸️  VALORANT not detected",
            border="yellow",
            spinner_text="Waiting for VALORANT to start...",
            lines=[
                "Start the game and reach the main menu.",
                "The connection resumes on its own.",
                "Ctrl+C to quit.",
            ],
            stay_while=lambda s: s == "DISCONNECTED",
        )
        # Tokens change on every game launch, so reconnect.
        self.api.connect()

    def _await_match_id(self, expected_state: str, timeout: float = 15.0) -> bool:
        """Wait for the MatchID of a freshly detected agent select or match.

        Returns False when the state changes or the MatchID never arrives, so
        the main loop re-evaluates instead of blocking.
        """
        deadline = time.time() + timeout
        while self.running and time.time() < deadline:
            state, match_id = self.api.detect_state()
            if state != expected_state:
                return False
            if match_id:
                self.current_match_id = match_id
                return True
            time.sleep(0.5)
        return False

    # ---- main loop -------------------------------------------------------

    def _maybe_start_web(self):
        """Start the loadout web UI in the background, best effort."""
        try:
            import web_app
            web_url = web_app.start_background(open_browser=True)
            console.print(Panel(
                f"Loadout web UI running at [bold cyan]{web_url}[/]\n"
                "[dim]Shows agent, rank, and Vandal/Phantom skins for every player. "
                "Click a player for the full arsenal.[/]",
                title="🌐 Loadout Web UI", border_style="cyan"))
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]Web UI not started ({exc}).[/]")

    def run(self):
        if not self.connect():
            return
        self._maybe_start_web()
        console.print("\n[green]Tracker started.[/] [dim]Monitoring...[/]")
        time.sleep(1.5)

        try:
            # Each handler draws, then blocks until the state changes; the loop
            # then dispatches to the handler for the new state.
            while self.running:
                state = self._detect_state()
                if state == "PREGAME":
                    # The MatchID is not published in the first moments of
                    # agent select, so wait for it to show up.
                    if not self._await_match_id("PREGAME"):
                        continue
                    self.handle_pregame()
                elif state == "INGAME":
                    if not self._await_match_id("INGAME"):
                        continue
                    self.handle_ingame()
                elif state == "DISCONNECTED":
                    self.handle_disconnected()
                else:
                    self.handle_menu()
        except KeyboardInterrupt:
            console.print("\n[green]Tracker closed. See you.[/] 👋")
        except Exception as exc:  # noqa: BLE001
            console.print_exception()
            console.print(f"[red]Unexpected error: {exc}[/]")


def main():
    app = ValorantTrackerApp()
    app.run()


if __name__ == "__main__":
    main()
