"""Textual TUI for toolbox — manage tool state interactively."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from qd_evolve.toolbox import get_state, toggle as tb_toggle, set_state


def _build_data(connect_mcp: bool = True) -> tuple[dict, list]:
    """Build category → [(name, desc, state), ...] and return (data, mcp_bridges)."""
    from qd_evolve.tools import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.config import load_settings
    from qd_evolve.tools._mcp_client import connect_mcp_servers, discover_mcp_servers

    categories: dict[str, list[tuple[str, str, str]]] = {}
    settings = load_settings()
    bridges = []

    # Discover MCP servers (always) and optionally connect
    mcp_configs = discover_mcp_servers()
    if connect_mcp:
        bridges = connect_mcp_servers(mcp_configs)

    # Now collect all tools from the registry
    registry = get_registry()

    # Builtin tools (no __ prefix)
    builtin = []
    for td in registry.list_tools():
        if "__" not in td.name:
            builtin.append((td.name, td.description or "", get_state("tools", td.name)))
    if builtin:
        categories["Builtin Tools"] = builtin

    # MCP tools grouped by server (have __ prefix)
    mcp_tools: dict[str, list[tuple[str, str, str]]] = {}
    for td in registry.list_tools():
        if "__" in td.name:
            server = td.name.split("__")[0]
            mcp_tools.setdefault(server, []).append(
                (td.name, td.description or "", get_state("tools", td.name))
            )

    # MCP servers (with server-level state)
    from qd_evolve.toolbox import get_disabled_mcp_servers
    disabled_srv = get_disabled_mcp_servers()
    for cfg in mcp_configs:
        srv_state = "disabled" if cfg.name in disabled_srv else "enabled"
        key = f"MCP: {cfg.name}"
        categories[key] = [(f"▶ mcp:{cfg.name} (server)", cfg.command, srv_state)]
        if cfg.name in mcp_tools:
            categories[key].extend(mcp_tools[cfg.name])

    # CLI tools
    cr = CLIRegistry()
    cr.discover(settings.cli_tools_dir)
    cli_data = [(t.name, t.description or t.command, get_state("cli", t.name))
                for t in cr._tools.values()]
    if cli_data:
        categories["CLI Tools"] = cli_data

    # Skills
    sr = SkillRegistry()
    sr.discover_skills(settings.skills_dir, preload_skills=settings.preload_skills)
    skills = [(s.name, s.summary or "", get_state("skills", s.name))
              for s in sr._skills.values()]
    if skills:
        categories["Skills"] = skills

    return categories, bridges


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(
            " [bold]Toolbox TUI[/bold]\n\n"
            " [bold]Navigation[/bold]\n"
            "   ↑↓ / jk     Move selection\n"
            "   Tab         Switch panels (categories ↔ tools)\n"
            "   Space       Expand / collapse MCP server\n"
            "   /           Filter tools\n"
            "\n"
            " [bold]Actions[/bold]\n"
            "   Enter / t   Cycle: disabled → enabled → preload\n"
            "   e           Toggle enable / disable\n"
            "   p           Toggle preload on / off\n"
            "   Space       Expand / collapse MCP server\n"
            "   s           Shrink all MCP servers\n"
            "\n"
            " [bold]Columns[/bold]\n"
            "   ✓/✗ = enabled/disabled   ⚡ = preloaded\n"
            "   ▶/▼ = collapsed/expanded (MCP servers)\n"
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

    TITLE = "Toolbox"
    BINDINGS = [
        Binding("up,j", "cursor_up", "Up", show=False),
        Binding("down,k", "cursor_down", "Down", show=False),
        Binding("enter,t", "cycle", "Cycle state"),
        Binding("e", "toggle_enabled", "Toggle enable/disable"),
        Binding("p", "toggle_preload", "Toggle preload"),
        Binding("space", "expand", "Expand/Collapse"),
        Binding("s", "shrink_all", "Shrink all"),
        Binding("/", "filter", "Filter"),
        Binding("tab", "switch_panel", "Switch panel"),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._mcp_bridges: list = []
        self._data, self._mcp_bridges = _build_data(connect_mcp=True)
        self._categories = list(self._data.keys())
        self._cat_index = 0
        self._filter = ""
        self._expanded: set[str] = set()

    def on_unmount(self) -> None:
        for bridge in self._mcp_bridges:
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
        self._rebuilding = True
        cat_table = self.query_one("#categories", DataTable)
        cat_table.add_column("Category")
        cat_table.styles.width = "25%"
        self._load_categories()
        self._rebuilding = False

        tools_table = self.query_one("#tools", DataTable)
        tools_table.add_column("Enabled", width=7)
        tools_table.add_column("Preload", width=7)
        tools_table.add_column("Name", width=35)
        tools_table.add_column("Description")
        tools_table.styles.width = "75%"

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
            if cat.startswith("MCP:"):
                total -= 1
                active = max(0, active - 1)
            expanded = "▼ " if cat in self._expanded else ""
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
        is_mcp = cat.startswith("MCP:")
        expanded = cat in self._expanded

        for i, (name, desc, state) in enumerate(self._data[cat]):
            if self._filter and self._filter.lower() not in name.lower():
                continue

            # MCP category: server header always shown, tools only when expanded
            if is_mcp:
                if i == 0:
                    # Server header row — aggregate state from subtools
                    arrow = "▼" if expanded else "▶"
                    display_name = name.replace("▶", arrow).replace("▼", arrow)
                    # Count subtool states
                    tool_states = [s for n, d, s in self._data[cat][1:] if self._filter.lower() in n.lower() or not self._filter]
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
                    preload_mark = "⚡" if state == "preload" else ""
                    table.add_row(enabled_mark, preload_mark, f"  {name}", desc[:80])
                # else: collapsed, skip tools
            else:
                enabled_mark = "✓" if state != "disabled" else "✗"
                preload_mark = "⚡" if state == "preload" else ""
                table.add_row(enabled_mark, preload_mark, name, desc[:80])

        if table.row_count > saved_row:
            table.move_cursor(row=saved_row)
        elif table.row_count > 0:
            table.move_cursor(row=table.row_count - 1)

    def _current_category_is_mcp(self) -> bool:
        if not self._categories:
            return False
        return self._categories[self._cat_index].startswith("MCP:")

    # ── actions ──────────────────────────────────────────────

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
        """Expand/collapse the MCP server on the currently selected row."""
        if not self._categories:
            return
        cat = self._categories[self._cat_index]
        # Only works for MCP categories
        if not cat.startswith("MCP:"):
            return
        # If focus is on tools table, only expand if on the server header row
        tools = self.query_one("#tools", DataTable)
        if tools.has_focus:
            name, _ = self._selected_item()
            if not name or "(server)" not in name:
                return  # not on server header row
        if cat in self._expanded:
            self._expanded.discard(cat)
        else:
            self._expanded.add(cat)
        self._load_categories()
        self._load_tools()

    def action_shrink_all(self) -> None:
        """Collapse all expanded MCP servers."""
        self._expanded.clear()
        self._load_categories()
        self._load_tools()

    def action_cycle(self) -> None:
        """Cycle: disabled → enabled → preload → disabled."""
        name, section = self._selected_item()
        if not name:
            return
        if name.startswith("mcp:") and "(server)" in name:
            section = "mcp_servers"
            name = name.split(":")[1].split(" ")[0]
        new = tb_toggle(section, name)
        self._refresh()
        self.notify(f"{name} → {new}")

    def action_toggle_enabled(self) -> None:
        """Toggle enabled ↔ disabled. On server row, propagates to all subtools."""
        name, section = self._selected_item()
        if not name:
            return
        if name.startswith("mcp:") and "(server)" in name:
            # Toggle server + all its subtools
            server = name.split(":")[1].split(" ")[0]
            current = get_state("mcp_servers", server)
            new_state = "disabled" if current != "disabled" else "enabled"
            set_state("mcp_servers", server, new_state)
            # Propagate to all subtools
            for tool_name, _, _ in self._data[f"MCP: {server}"]:
                if "(server)" in tool_name:
                    continue
                clean = tool_name.strip()
                if new_state == "disabled":
                    set_state("tools", clean, "disabled")
                else:
                    set_state("tools", clean, "enabled")
            self.notify(f"mcp:{server} → {new_state} (all tools)")
        else:
            # Individual tool toggle — server becomes neutral
            current = get_state(section, name)
            new_state = "enabled" if current == "disabled" else "disabled"
            set_state(section, name, new_state)
            # Remove server-level state (mixed)
            cat = self._categories[self._cat_index]
            if cat.startswith("MCP:"):
                server = cat.split(": ", 1)[1]
                set_state("mcp_servers", server, "enabled")  # clear key
            self.notify(f"{name} → {new_state}")
        self._refresh()

    def action_toggle_preload(self) -> None:
        """Toggle preload on/off. Disabled items become enabled+preloaded."""
        name, section = self._selected_item()
        if not name:
            return
        if "(server)" in name:
            self.notify("MCP servers don't support preload", severity="warning")
            return
        current = get_state(section, name)
        if current == "disabled":
            set_state(section, name, "preload")
        elif current == "preload":
            set_state(section, name, "enabled")
        else:
            set_state(section, name, "preload")
        self._refresh()
        self.notify(f"{name} → {get_state(section, name)}")

    def action_filter(self) -> None:
        def on_input(result: str) -> None:
            self._filter = result
            self._load_tools()
        self.push_screen(_FilterScreen(), on_input)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    # ── helpers ──────────────────────────────────────────────

    def _selected_item(self) -> tuple[str, str] | tuple[None, None]:
        """Get currently selected (name, section)."""
        cat_name = self._categories[self._cat_index]
        cat = self._data.get(cat_name, [])
        table = self.query_one("#tools", DataTable)
        if table.row_count == 0:
            return None, None

        row_idx = table.cursor_coordinate.row

        # Rebuild the visible filtered list (same logic as _load_tools)
        is_mcp = cat_name.startswith("MCP:")
        expanded = cat_name in self._expanded
        visible: list[tuple[str, str, str]] = []
        for i, item in enumerate(cat):
            name = item[0]
            if self._filter and self._filter.lower() not in name.lower():
                continue
            if is_mcp:
                if i == 0:
                    visible.append(item)
                elif expanded:
                    visible.append(item)
            else:
                visible.append(item)

        if row_idx < len(visible):
            name = visible[row_idx][0]
            section = "tools"
            if name.startswith("mcp:") or name.startswith("▶"):
                section = "mcp_servers"
            elif cat_name == "CLI Tools":
                section = "cli"
            elif cat_name == "Skills":
                section = "skills"
            return name, section
        return None, None

    def _refresh(self) -> None:
        self._data, _ = _build_data(connect_mcp=False)
        self._categories = list(self._data.keys())
        self._expanded &= set(self._categories)
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
