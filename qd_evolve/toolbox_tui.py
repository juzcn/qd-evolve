"""Textual TUI for toolbox 鈥?manage tool state interactively."""


from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Static

from qd_evolve.toolbox import get_state, set_state


def _build_data(connect_bridges: bool = True) -> tuple[dict, list, list]:
    """Build category 鈫?[(name, desc, state), ...] and return (data, bridges, entries)."""
    from qd_evolve.tools import get_registry
    from qd_evolve.skills import SkillRegistry
    from qd_evolve.cli_tools import CLIRegistry
    from qd_evolve.config import load_settings
    from tools.bridge import BridgeManager, BridgeEntry

    categories: dict[str, list[tuple[str, str, str]]] = {}
    settings = load_settings()
    bridges: list = []

    # Discover and optionally connect all bridges
    bridge_entries: list[BridgeEntry] = BridgeManager.list_all(settings)
    if connect_bridges:
        bridges = BridgeManager.connect_all(settings)

    # Collect all tools from the global registry
    registry = get_registry()

    # Build tool 鈫?bridge name map from bridges
    tool_bridge: dict[str, str] = {}
    for b in bridges:
        for tname in b.tool_names:
            tool_bridge[tname] = getattr(b.config, "name", "")

    # Builtin tools (not from any bridge)
    builtin = []
    for td in registry.list_tools():
        if td.name not in tool_bridge:
            builtin.append((td.name, td.description or "", get_state("tools", td.name)))
    if builtin:
        categories["Builtin Tools"] = builtin

    # Bridge tools grouped by bridge name
    bridge_tool_groups: dict[str, list[tuple[str, str, str]]] = {}
    for td in registry.list_tools():
        bname = tool_bridge.get(td.name)
        if bname:
            bridge_tool_groups.setdefault(bname, []).append(
                (td.name, td.description or "", get_state("tools", td.name))
            )

    # Bridge entries (with bridge-level state)
    from qd_evolve.toolbox import get_disabled_bridges
    disabled_br = get_disabled_bridges()
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
    cr.discover(settings.cli_tools_dir)
    cli_data = [(t.name, t.description or t.command, get_state("cli", t.name))
                for t in cr._tools.values()]
    if cli_data:
        categories["CLI Tools"] = cli_data

    # Skills
    sr = SkillRegistry()
    sr.discover_skills(settings.skills_dir)
    skills = [(s.name, s.summary or "", get_state("skills", s.name))
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
            "   鈫戔啌 / jk     Move selection\n"
            "   Tab         Switch panels (categories 鈫?tools)\n"
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
            "   鉁?鉁?= enabled/disabled   鈿?= preloaded\n"
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

    def __init__(self, data: dict, bridges: list, bridge_entries: list | None = None) -> None:
        super().__init__()
        self._data = data
        self._bridges = bridges
        self._bridge_entries = bridge_entries or []
        self._categories = list(data.keys())
        self._cat_index = 0
        self._filter = ""
        self._expanded: set[str] = set()

    def on_unmount(self) -> None:
        for bridge in self._bridges:
            try:
                bridge.disconnect()
            except Exception:
                logger.debug("toolbox_tui: bridge disconnect failed", exc_info=True)

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
                    # Server header row 鈥?aggregate state from subtools
                    arrow = "v" if expanded else "+"
                    display_name = name.replace("+", arrow).replace("v", arrow)
                    # Count subtool states
                    tool_states = [s for n, _, s in self._data[cat][1:] if self._filter.lower() in n.lower() or not self._filter]
                    all_enabled = tool_states and all(s != "disabled" for s in tool_states)
                    all_disabled = tool_states and all(s == "disabled" for s in tool_states)
                    if all_disabled:
                        enabled_mark = "鉁?
                    elif all_enabled:
                        enabled_mark = "鉁?
                    else:
                        enabled_mark = "~"
                    preload_mark = ""
                    table.add_row(enabled_mark, preload_mark, display_name, desc[:80])
                elif expanded:
                    enabled_mark = "鉁? if state != "disabled" else "鉁?
                    preload_mark = "鈿? if state == "preload" else ""
                    table.add_row(enabled_mark, preload_mark, f"  {name}", desc[:80])
                # else: collapsed, skip tools
            else:
                enabled_mark = "鉁? if state != "disabled" else "鉁?
                preload_mark = "鈿? if state == "preload" else ""
                table.add_row(enabled_mark, preload_mark, name, desc[:80])

        if table.row_count > saved_row:
            table.move_cursor(row=saved_row)
        elif table.row_count > 0:
            table.move_cursor(row=table.row_count - 1)

    def _current_category_is_bridge(self) -> bool:
        if not self._categories:
            return False
        return self._categories[self._cat_index].startswith("Bridge:")

    # 鈹€鈹€ actions 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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
        """Toggle enabled 鈫?disabled. On bridge row, propagates to all subtools."""
        name, section = self._selected_item()
        if not name:
            return
        if "(bridge)" in name:
            bridge_key = self._bridge_name(name)
            current = get_state("bridge", bridge_key)
            new_state = "disabled" if current != "disabled" else "enabled"
            set_state("bridge", bridge_key, new_state)
            # Propagate to all subtools: find matching category
            cat_name = self._categories[self._cat_index]
            if cat_name in self._data:
                for tool_name, _, _ in self._data[cat_name]:
                    if "(bridge)" in tool_name:
                        continue
                    clean = tool_name.strip()
                    set_state("tools", clean, "disabled" if new_state == "disabled" else "enabled")
            self.notify(f"{bridge_key} 鈫?{new_state} (all tools)")
        else:
            current = get_state(section, name)
            new_state = "enabled" if current == "disabled" else "disabled"
            set_state(section, name, new_state)
            self.notify(f"{name} 鈫?{new_state}")
        self._refresh()

    def action_toggle_preload(self) -> None:
        """Toggle preload on/off. Disabled items become enabled+preloaded."""
        name, section = self._selected_item()
        if not name:
            return
        if "(bridge)" in name:
            self.notify("bridges don't support preload", severity="warning")
            return
        current = get_state(section, name)
        if current == "disabled":
            set_state(section, name, "preload")
        elif current == "preload":
            set_state(section, name, "enabled")
        else:
            set_state(section, name, "preload")
        self._refresh()
        self.notify(f"{name} 鈫?{get_state(section, name)}")

    def action_filter(self) -> None:
        def on_input(result: str) -> None:
            self._filter = result
            self._load_tools()
        self.push_screen(_FilterScreen(), on_input)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    @staticmethod
    def _bridge_name(name: str) -> str:
        """Extract bridge key from display row like '+ oat:boat (bridge)' 鈫?'oat:boat'."""
        name = name.removeprefix("+ ").removeprefix("v ")
        if " (" in name:
            name = name.split(" (")[0]
        return name

    # 鈹€鈹€ helpers 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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
            elif cat_name == "CLI Tools":
                section = "cli"
            elif cat_name == "Skills":
                section = "skills"
            return name, section
        return None, None

    def _refresh(self) -> None:
        """Refresh states from toolbox.json without rebuilding registries."""
        for cat, items in self._data.items():
            for i, (name, desc, _) in enumerate(items):
                if cat.startswith("Bridge:") and i == 0:
                    continue  # server header 鈥?aggregate, not stored
                section = "tools"
                if cat == "CLI Tools":
                    section = "cli"
                elif cat == "Skills":
                    section = "skills"
                elif "(bridge)" in name:
                    section = "bridge"
                items[i] = (name, desc, get_state(section, name))
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
