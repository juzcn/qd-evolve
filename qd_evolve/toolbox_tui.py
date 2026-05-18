"""Toolbox — TUI and CLI for managing tool state interactively."""

from __future__ import annotations

from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from qd_evolve.core.toolbox import get_state, set_state, state_mark

console = Console()


# ── CLI commands (shared by chat_cli and a2a_cli) ────────────────────────

toolbox_app = typer.Typer(help="Toolbox — manage tool state")


@toolbox_app.command()
def toolbox(
    toggle: str = typer.Option("", "--toggle", "-t", help="Quick toggle: --toggle <name>"),
    tui: bool = typer.Option(True, "--tui/--no-tui", help="Use Textual TUI (default: on)"),
    agent: str = typer.Option("", "--agent", help="Per-agent toolbox config (from config.json agents list)"),
) -> None:
    """Interactive tool manager — enable/disable/preload tools, MCP, CLI, skills.

    Opens a Textual TUI by default. Use --no-tui for interactive shell.
    --toggle <name> for quick non-interactive toggle.
    --agent <name> to manage a specific agent's toolbox.
    """
    from qd_evolve.core.toolbox import toggle as tb_toggle
    an = agent or None

    # Quick toggle mode
    if toggle:
        section = _resolve_section(toggle)
        name = _resolve_name(toggle)
        new_state = tb_toggle(section, name, agent_name=an)
        console.print(f"[bold]{toggle}[/bold] → [cyan]{new_state}[/cyan]")
        return

    if tui:
        from qd_evolve.toolbox_tui import _build_data, ToolboxApp
        console.print("Loading tools...", end="\r")
        data, bridges, bridge_entries = _build_data(connect_bridges=True, agent_name=an)
        console.print(f"Loaded {sum(len(v) for v in data.values())} items across {len(data)} categories")
        ToolboxApp(data, bridges, bridge_entries, agent_name=an).run()
    else:
        _toolbox_interactive(an)


def _toolbox_interactive(agent_name: str | None = None) -> None:
    """Interactive toolbox shell."""
    from qd_evolve.core.toolbox import (
        get_state, set_state, toggle as tb_toggle,
    )

    label = f" (agent: {agent_name})" if agent_name else ""
    console.print(f"[bold]Toolbox[/bold]{label} — manage tool state (enabled / preload / disabled)")
    console.print("Type [cyan]help[/cyan] for commands, [cyan]quit[/cyan] to exit\n")

    while True:
        try:
            cmd = console.input("[bold cyan]toolbox>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break

        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        if action in ("q", "quit", "exit"):
            break
        elif action == "help":
            _toolbox_help()
        elif action in ("ls", "list", "show"):
            _toolbox_list(args, agent_name=agent_name)
        elif action == "toggle":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                new = tb_toggle(section, name, agent_name=agent_name)
                console.print(f"  {args[0]} → [cyan]{new}[/cyan]")
            else:
                console.print("  Usage: toggle <name>")
        elif action == "enable":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                set_state(section, name, "enabled", agent_name=agent_name)
                console.print(f"  {args[0]} → [green]enabled[/green]")
            else:
                console.print("  Usage: enable <name>")
        elif action == "disable":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                set_state(section, name, "disabled", agent_name=agent_name)
                console.print(f"  {args[0]} → [red]disabled[/red]")
            else:
                console.print("  Usage: disable <name>")
        elif action == "preload":
            if args:
                section = _resolve_section(args[0])
                name = _resolve_name(args[0])
                if section == "mcp_servers":
                    console.print("  MCP servers don't support preload")
                else:
                    set_state(section, name, "preload", agent_name=agent_name)
                    console.print(f"  {args[0]} → [yellow]preload[/yellow]")
            else:
                console.print("  Usage: preload <name>")
        else:
            console.print(f"  Unknown: {action}. Type [cyan]help[/cyan]")


def _toolbox_help() -> None:
    console.print("""
  [bold]Commands:[/bold]
    [cyan]ls[/cyan] [section]     List tools (tools, mcp, cli, skills, all)
    [cyan]toggle[/cyan] <name>    Cycle state (enabled→preload→disabled→enabled)
    [cyan]enable[/cyan] <name>    Set item to enabled (on-demand loading)
    [cyan]disable[/cyan] <name>   Hide item from the LLM
    [cyan]preload[/cyan] <name>   Load full definition into system prompt
    [cyan]quit[/cyan]             Exit

  [bold]Name prefixes:[/bold]
    builtin/MCP tools: just the name (e.g. [cyan]fetch[/cyan], [cyan]boat__write_file[/cyan])
    MCP servers:       [cyan]mcp:boat[/cyan]
    CLI tools:          [cyan]pandoc[/cyan]
    Skills:             [cyan]baidu-search[/cyan]

  [bold]States:[/bold] [✓] enabled  [P] preload  [✗] disabled
""")


def _toolbox_list(args: list[str], agent_name: str | None = None) -> None:
    from qd_evolve.core.toolbox import get_state, get_disabled_bridges
    from qd_evolve.core.registry import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.core.config import SKILLS_DIR, CLI_TOOLS_DIR, load_settings
    from tools.bridge import BridgeManager

    settings = load_settings()
    PAGE_SIZE = settings.ui.page_size
    section_arg = args[0].lower() if args else "all"

    # Build data
    registry = get_registry()
    builtin: list[tuple[str, str, str]] = []
    bridge_tools: dict[str, list[tuple[str, str, str]]] = {}
    for td in registry.list_tools():
        state = get_state("tools", td.name, agent_name=agent_name)
        desc = td.description or ""
        if desc.startswith("[") and "]" in desc:
            bracket_end = desc.index("]")
            server = desc[1:bracket_end]
            bridge_tools.setdefault(server, []).append((td.name, desc, state))
        else:
            builtin.append((td.name, desc, state))

    # Skills
    sr = SkillRegistry()
    sr.discover_skills(SKILLS_DIR)
    skills_data: list[tuple[str, str, str]] = []
    for s in sr._skills.values():
        skills_data.append((s.name, s.summary or "", get_state("skills", s.name, agent_name=agent_name)))

    # CLI
    cr = CLIRegistry()
    cr.discover(CLI_TOOLS_DIR)
    cli_data: list[tuple[str, str, str]] = []
    for t in cr._tools.values():
        cli_data.append((t.name, t.description or t.command, get_state("cli", t.name, agent_name=agent_name)))

    # Bridge entries
    bridge_entries = BridgeManager.list_all(settings)
    disabled_bridges = get_disabled_bridges(agent_name=agent_name)

    def _print_items(title: str, items: list[tuple[str, str, str]], page: int = 0) -> None:
        if not items:
            return
        start = page * PAGE_SIZE
        chunk = items[start:start + PAGE_SIZE]
        total_pages = (len(items) - 1) // PAGE_SIZE + 1
        active_n = sum(1 for _, _, s in items if s != "disabled")
        console.print(f"\n[bold]{title}[/bold] ({active_n}/{len(items)} active{', page %s/%s' % (page + 1, total_pages) if total_pages > 1 else ''})")
        for name, desc, state in chunk:
            mark = state_mark(state)
            style = "dim" if state == "disabled" else ""
            console.print(f"  {mark} [cyan]{name}[/cyan] {style}—{desc[:70]}")
        if total_pages > 1 and page < total_pages - 1:
            console.print(f"  [dim]... ls {section_arg} page {page + 2} for more[/dim]")

    def _print_bridge_entries() -> None:
        if not bridge_entries:
            return
        console.print("\n[bold]Bridges[/bold]")
        for be in bridge_entries:
            tools = bridge_tools.get(be.name, [])
            srv_state = "disabled" if f"{be.bridge_type}:{be.name}" in disabled_bridges else "enabled"
            mark = state_mark(srv_state)
            style = "dim" if srv_state == "disabled" else ""
            total = len(tools)
            active = sum(1 for _, _, s in tools if s != "disabled")
            summary = f"{active}/{total} tools" if total else "tools visible during chat"
            console.print(f"  {mark} [cyan]{be.bridge_type}:{be.name}[/cyan] {style}—{summary}")
            if total > 0:
                console.print(f"    [dim]ls {be.name} to expand[/dim]")

    page = 0
    if len(args) > 1 and args[1].isdigit():
        page = int(args[1]) - 1

    if section_arg in ("all", "tools"):
        _print_items("Builtin Tools", builtin, page)
    if section_arg in ("all", "cli"):
        _print_items("CLI Tools", cli_data, page)
    if section_arg in ("all", "skills"):
        _print_items("Skills", skills_data, page)
    if section_arg in ("all", "bridge"):
        _print_bridge_entries()
    # Expand a specific bridge's tools
    for be in bridge_entries:
        if section_arg == be.name and be.name in bridge_tools:
            _print_items(f"Bridge: {be.name} ({be.bridge_type})", bridge_tools[be.name], page)
            break


def _resolve_section(name: str) -> str:
    if name.startswith("mcp:") or name.startswith("oat:") or ":" in name:
        return name.split(":")[0] + "_bridge" if not name.startswith("mcp:") and not name.startswith("oat:") else "bridge"
    return "tools"


def _resolve_name(name: str) -> str:
    if name.startswith("mcp:"):
        return name.split(":", 1)[1]
    return name


# ── TUI ──────────────────────────────────────────────────────────────────

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static


def _build_data(connect_bridges: bool = True, agent_name: str | None = None) -> tuple[dict, list, list]:
    """Build category → [(name, desc, state), ...] and return (data, bridges, entries)."""
    from qd_evolve.tools import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.core.config import SKILLS_DIR, CLI_TOOLS_DIR, load_settings
    from tools.bridge import BridgeManager, BridgeEntry

    categories: dict[str, list[tuple[str, str, str]]] = {}
    settings = load_settings()

    # Inject env_vars from config into os.environ (needed for MCP $VAR expansion)
    import os as _os
    for key, value in settings.env_vars.items():
        _os.environ.setdefault(key, value)

    bridges: list = []

    # Discover and optionally connect all bridges
    bridge_entries: list[BridgeEntry] = BridgeManager.list_all(settings)
    if connect_bridges:
        bridges = BridgeManager.connect_all(settings)

    # Collect all tools from the global registry
    registry = get_registry()

    # Build tool → bridge name map from bridges
    tool_bridge: dict[str, str] = {}
    for b in bridges:
        for tname in b.tool_names:
            tool_bridge[tname] = getattr(b.config, "name", "")

    # System tools — from qd_evolve/tools/ (load_func, install_*, register_*, a2a, etc.)
    system = []
    # Func tools — from tools/func/ (run_shell, fetch, etc.)
    func = []
    for td in registry.list_tools():
        if td.name not in tool_bridge:
            # Check if the tool comes from tools/func/ by looking at its module
            mod = getattr(td.handler, "__module__", "") or ""
            if mod.startswith("tools.func."):
                func.append((td.name, td.description or "", get_state("tools", td.name, agent_name=agent_name)))
            else:
                system.append((td.name, td.description or "", get_state("tools", td.name, agent_name=agent_name)))
    if system:
        categories["System Tools"] = system
    if func:
        categories["Func Tools"] = func

    # Bridge tools grouped by bridge name
    bridge_tool_groups: dict[str, list[tuple[str, str, str]]] = {}
    for td in registry.list_tools():
        bname = tool_bridge.get(td.name)
        if bname:
            bridge_tool_groups.setdefault(bname, []).append(
                (td.name, td.description or "", get_state("tools", td.name, agent_name=agent_name))
            )

    # Bridge entries (with bridge-level state)
    from qd_evolve.core.toolbox import get_disabled_bridges
    disabled_br = get_disabled_bridges(agent_name=agent_name)
    for be in bridge_entries:
        bridge_key = f"{be.bridge_type}:{be.name}"
        srv_state = "disabled" if bridge_key in disabled_br else "enabled"
        key = f"Bridge: {be.name} ({be.bridge_type})"
        group = bridge_tool_groups.get(be.name, [])
        display_name = f"+ {be.bridge_type}:{be.name} (bridge)"
        desc = be.description or f"{len(group)} tools"
        categories[key] = [(display_name, desc, srv_state)]
        if group:
            categories[key].extend(group)

    # CLI tools
    cr = CLIRegistry()
    cr.discover(CLI_TOOLS_DIR)
    cli_data = [(t.name, t.description or t.command, get_state("cli", t.name, agent_name=agent_name))
                for t in cr._tools.values()]
    if cli_data:
        categories["CLI Tools"] = cli_data

    # Skills
    sr = SkillRegistry()
    sr.discover_skills(SKILLS_DIR)
    skills = [(s.name, s.summary or "", get_state("skills", s.name, agent_name=agent_name))
              for s in sr._skills.values()]
    if skills:
        categories["Skills"] = skills

    return categories, bridges, bridge_entries


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(
            " [bold]Toolbox TUI[/bold]\n\n"
            " [bold]Navigation[/bold]\n"
            "   ↑↓ / jk     Move selection\n"
            "   Tab         Switch panels (categories → tools)\n"
            "   Space       Expand / collapse bridge\n"
            "   /           Filter tools\n"
            "\n"
            " [bold]Actions[/bold]\n"
            "   e           Toggle enable / disable\n"
            "   p           Toggle preload on / off\n"
            "   Space       Expand / collapse bridge\n"
            "   s           Shrink all bridges\n"
            "\n"
            " [bold]Columns[/bold]\n"
            "   ✓/✗ = enabled/disabled   ◆ = preloaded\n"
            "   +/v = collapsed/expanded (bridges)\n"
            "\n"
            " [bold]Other[/bold]\n"
            "   ?           This help\n"
            "   q           Quit\n"
            "\n"
            " Press ESC to close",
            id="help-text",
        )


class ToolboxApp(App):
    _rebuilding = False  # guard against RowHighlighted during rebuild
    ENABLE_COMMAND_PALETTE = False

    TITLE = "Toolbox"
    BINDINGS = [
        Binding("up,j", "cursor_up", "Up", show=False),
        Binding("down,k", "cursor_down", "Down", show=False),
        Binding("e", "toggle_enabled", "Toggle enable/disable"),
        Binding("p", "toggle_preload", "Toggle preload"),
        Binding("space", "expand", "Expand/Collapse"),
        Binding("s", "shrink_all", "Shrink all"),
        Binding("/", "filter", "Filter"),
        Binding("tab", "switch_panel", "Switch panel"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, data: dict, bridges: list, bridge_entries: list | None = None,
                 agent_name: str | None = None) -> None:
        super().__init__()
        self._data = data
        self._bridges = bridges
        self._bridge_entries = bridge_entries or []
        self._categories = list(data.keys())
        self._cat_index = 0
        self._filter = ""
        self._expanded: set[str] = set()
        self.agent_name = agent_name
        if agent_name:
            self.TITLE = f"Toolbox (agent: {agent_name})"

    def on_unmount(self) -> None:
        for bridge in self._bridges:
            try:
                bridge.disconnect()
            except Exception:
                pass

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            DataTable(id="categories", cursor_type="row"),
            DataTable(id="tools", cursor_type="row"),
        )
        yield Footer()

    def on_mount(self) -> None:
        cat_table = self.query_one("#categories", DataTable)
        cat_table.add_column("Category")
        cat_table.styles.width = "25%"
        tools_table = self.query_one("#tools", DataTable)
        tools_table.add_column("Enabled", width=7)
        tools_table.add_column("Preload", width=7)
        tools_table.add_column("Name", width=35)
        tools_table.add_column("Description")
        tools_table.styles.width = "75%"

        self._load_categories()
        self._load_tools()
        cat_table.focus()

    def _load_categories(self) -> None:
        self._rebuilding = True
        cat_table = self.query_one("#categories", DataTable)
        saved_row = cat_table.cursor_coordinate.row
        cat_table.clear()
        for cat in self._categories:
            items = self._data[cat]
            active = sum(1 for _, _, s in items if s != "disabled")
            total = len(items)
            if cat.startswith("Bridge:"):
                total -= 1
                active = max(0, active - 1)
            expanded = "v " if cat in self._expanded else ""
            cat_table.add_row(f"{expanded}{cat} ({active}/{total})")
        cat_table.move_cursor(row=saved_row)
        self._rebuilding = False

    def _load_tools(self) -> None:
        table = self.query_one("#tools", DataTable)
        saved_row = table.cursor_coordinate.row
        table.clear()
        if not self._categories:
            return
        cat = self._categories[self._cat_index]
        is_bridge = cat.startswith("Bridge:")
        expanded = cat in self._expanded

        for i, (name, desc, state) in enumerate(self._data[cat]):
            if self._filter and self._filter.lower() not in name.lower():
                continue

            # bridge category: header always shown, tools only when expanded
            if is_bridge:
                if i == 0:
                    # Server header row — aggregate state from subtools
                    arrow = "v" if expanded else "+"
                    display_name = name.replace("+", arrow).replace("v", arrow)
                    # Count subtool states
                    tool_states = [s for n, _, s in self._data[cat][1:] if self._filter.lower() in n.lower() or not self._filter]
                    all_enabled = tool_states and all(s != "disabled" for s in tool_states)
                    all_disabled = tool_states and all(s == "disabled" for s in tool_states)
                    if all_disabled:
                        enabled_mark = "✗"
                    elif all_enabled:
                        enabled_mark = "✓"
                    else:
                        enabled_mark = "~"
                    preload_mark = ""
                    table.add_row(enabled_mark, preload_mark, display_name, desc[:80])
                elif expanded:
                    enabled_mark = "✓" if state != "disabled" else "✗"
                    preload_mark = "◆" if state == "preload" else ""
                    table.add_row(enabled_mark, preload_mark, f"  {name}", desc[:80])
                # else: collapsed, skip tools
            else:
                enabled_mark = "✓" if state != "disabled" else "✗"
                preload_mark = "◆" if state == "preload" else ""
                table.add_row(enabled_mark, preload_mark, name, desc[:80])

        if table.row_count > saved_row:
            table.move_cursor(row=saved_row)
        elif table.row_count > 0:
            table.move_cursor(row=table.row_count - 1)


    # ── actions ──────────────────────────────────────────────────────

    def action_cursor_up(self) -> None:
        focused = self.focused
        if focused and hasattr(focused, "cursor_up"):
            focused.action_cursor_up()

    def action_cursor_down(self) -> None:
        focused = self.focused
        if focused and hasattr(focused, "cursor_down"):
            focused.action_cursor_down()

    def action_switch_panel(self) -> None:
        cat = self.query_one("#categories", DataTable)
        tools = self.query_one("#tools", DataTable)
        if cat.has_focus:
            tools.focus()
        else:
            cat.focus()

    def action_expand(self) -> None:
        """Expand/collapse the bridge on the currently selected row."""
        if not self._categories:
            return
        cat = self._categories[self._cat_index]
        # Only works for bridge categories
        if not cat.startswith("Bridge:"):
            return
        # If focus is on tools table, only expand if on the server header row
        tools = self.query_one("#tools", DataTable)
        if tools.has_focus:
            name, _ = self._selected_item()
            if not name or "(bridge)" not in name:
                return  # not on server header row
        if cat in self._expanded:
            self._expanded.discard(cat)
        else:
            self._expanded.add(cat)
        self._load_categories()
        self._load_tools()

    def action_shrink_all(self) -> None:
        """Collapse all expanded bridges."""
        self._expanded.clear()
        self._load_categories()
        self._load_tools()

    def action_toggle_enabled(self) -> None:
        """Toggle enabled → disabled. On bridge row, propagates to all subtools."""
        name, section = self._selected_item()
        if not name:
            return
        an = self.agent_name
        if "(bridge)" in name:
            bridge_key = self._bridge_name(name)
            current = get_state("bridge", bridge_key, agent_name=an)
            new_state = "disabled" if current != "disabled" else "enabled"
            set_state("bridge", bridge_key, new_state, agent_name=an)
            # Propagate to all subtools: find matching category
            cat_name = self._categories[self._cat_index]
            if cat_name in self._data:
                for tool_name, _, _ in self._data[cat_name]:
                    if "(bridge)" in tool_name:
                        continue
                    clean = tool_name.strip()
                    set_state("tools", clean, "disabled" if new_state == "disabled" else "enabled", agent_name=an)
            self.notify(f"{bridge_key} → {new_state} (all tools)")
        else:
            current = get_state(section, name, agent_name=an)
            new_state = "enabled" if current == "disabled" else "disabled"
            set_state(section, name, new_state, agent_name=an)
            self.notify(f"{name} → {new_state}")
        self._refresh()

    def action_toggle_preload(self) -> None:
        """Toggle preload on/off. Disabled items become enabled+preloaded."""
        name, section = self._selected_item()
        if not name:
            return
        an = self.agent_name
        if "(bridge)" in name:
            self.notify("bridges don't support preload", severity="warning")
            return
        current = get_state(section, name, agent_name=an)
        if current == "disabled":
            set_state(section, name, "preload", agent_name=an)
        elif current == "preload":
            set_state(section, name, "enabled", agent_name=an)
        else:
            set_state(section, name, "preload", agent_name=an)
        self._refresh()
        self.notify(f"{name} → {get_state(section, name, agent_name=an)}")

    def action_filter(self) -> None:
        def on_input(result: str) -> None:
            self._filter = result
            self._load_tools()
        self.push_screen(_FilterScreen(), on_input)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @staticmethod
    def _bridge_name(name: str) -> str:
        """Extract bridge key from display row like '+ oat:boat (bridge)' → 'oat:boat'."""
        name = name.removeprefix("+ ").removeprefix("v ")
        if " (" in name:
            name = name.split(" (")[0]
        return name

    # ── helpers ──────────────────────────────────────────────────────

    def _selected_item(self) -> tuple[str, str] | tuple[None, None]:
        """Get currently selected (name, section)."""
        cat_name = self._categories[self._cat_index]
        cat = self._data.get(cat_name, [])
        table = self.query_one("#tools", DataTable)
        if table.row_count == 0:
            return None, None

        row_idx = table.cursor_coordinate.row

        # Rebuild the visible filtered list (same logic as _load_tools)
        is_bridge = cat_name.startswith("Bridge:")
        expanded = cat_name in self._expanded
        visible: list[tuple[str, str, str]] = []
        for i, item in enumerate(cat):
            name = item[0]
            if self._filter and self._filter.lower() not in name.lower():
                continue
            if is_bridge:
                if i == 0:
                    visible.append(item)
                elif expanded:
                    visible.append(item)
            else:
                visible.append(item)

        if row_idx < len(visible):
            name = visible[row_idx][0]
            section = "tools"
            if "(bridge)" in name:
                section = "bridge"
            elif cat_name in ("CLI Tools",):
                section = "cli"
            elif cat_name in ("Skills",):
                section = "skills"
            return name, section
        return None, None

    def _refresh(self) -> None:
        """Refresh states from toolbox.json without rebuilding registries."""
        for cat, items in self._data.items():
            for i, (name, desc, _) in enumerate(items):
                if cat.startswith("Bridge:") and i == 0:
                    continue  # server header — aggregate, not stored
                section = "tools"
                if cat == "CLI Tools":
                    section = "cli"
                elif cat == "Skills":
                    section = "skills"
                elif "(bridge)" in name:
                    section = "bridge"
                items[i] = (name, desc, get_state(section, name, agent_name=self.agent_name))
        self._load_categories()
        self._load_tools()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._rebuilding:
            return
        if event.data_table.id == "categories":
            if event.cursor_row is not None:
                self._cat_index = event.cursor_row
                self._filter = ""
                self._load_tools()


class _FilterScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter tools...", id="filter-input")

    def on_mount(self) -> None:
        self.query_one("#filter-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)
