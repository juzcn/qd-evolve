"""Memory TUI — browse and search saved conversation memories."""

from __future__ import annotations

import typer
from pydantic import ValidationError
from rich.console import Console

console = Console()

# ── CLI entry ──────────────────────────────────────────────────────────────

memory_app = typer.Typer(help="Memory — browse and search memories", invoke_without_command=True)


@memory_app.callback()
def memory(
    tui: bool = typer.Option(True, "--tui/--no-tui", help="Use Textual TUI (default: on)"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name (from config.json agents list)"),
) -> None:
    """Browse and search saved conversation memories.

    Opens a Textual TUI by default. Use --no-tui for CLI list.
    --agent <name> is required — specifies which agent's memory to browse.
    """
    from qd_evolve.core.config import load_settings
    from qd_evolve.core.memory import MemoryStore

    try:
        settings = load_settings()
    except ValidationError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise SystemExit(1)

    # Find agent entry
    agent_entry = None
    for a in settings.agents_config.agents:
        if a.name == agent:
            agent_entry = a
            break

    if agent_entry is None:
        console.print(f"[red]Agent '{agent}' not found in agents_config.agents[/red]")
        raise SystemExit(1)

    memory_db = agent_entry.memory_db
    if not memory_db:
        console.print(f"[red]Agent '{agent}' has no memory_db configured[/red]")
        raise SystemExit(1)

    # Resolve embeddings backend
    backend_name = settings.memory_search.embeddings_backend
    backend = settings.embeddings_backends.get(backend_name) if backend_name else None
    if backend is None:
        console.print("[red]No embeddings backend configured (memory_search.embeddings_backend)[/red]")
        raise SystemExit(1)

    console.print(f"[dim]Loading memory store ({memory_db})...[/dim]", end="\r")
    try:
        store = MemoryStore(memory_db, backend, list_all_limit=settings.memory_search.list_all_limit)
    except Exception as e:
        console.print(f"[red]Failed to open memory store:[/red] {e}")
        raise SystemExit(1)
    console.print(f"[dim]Memory store loaded ({memory_db})[/dim]    ")

    if tui:
        from qd_evolve.memory_tui import MemoryApp
        MemoryApp(store, agent_name=agent).run()
    else:
        _memory_list(store)


def _memory_list(store) -> None:
    """CLI list of recent memories."""
    entries = store.list_all()
    if not entries:
        console.print("  (no memories saved)")
        return
    for i, e in enumerate(entries, 1):
        user_preview = e.user_msg.replace("\n", " ")[:80]
        asst_preview = e.assistant_msg.replace("\n", " ")[:80]
        console.print(f"[bold cyan]{i}.[/bold cyan] [dim]{e.key}[/dim]")
        console.print(f"  user: {user_preview}")
        console.print(f"  assistant: {asst_preview}")
        if "process:" in e.content:
            proc_start = e.content.index("process:")
            proc_end = e.content.index("assistant:", proc_start)
            process_text = e.content[proc_start:proc_end].strip()
            console.print(f"  [dim]{process_text[:120]}[/dim]")
        console.print()


# ── TUI ────────────────────────────────────────────────────────────────────

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from qd_evolve.core.memory import MemoryStore, MemoryEntry


class SearchScreen(ModalScreen):
    """Modal screen for entering a search query."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Static("Enter search query (semantic search):", id="search-label")
        yield Input(placeholder="e.g. how we fixed the timeout issue...", id="search-input")

    def on_mount(self) -> None:
        self.query_one("#search-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class TimeRangeScreen(ModalScreen):
    """Modal screen for entering a time range filter."""

    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Static(
            "Time range: today, yesterday, this_week, last_week, this_month, last_month, last_Nd, YYYY-MM-DD~YYYY-MM-DD, last_session",
            id="timerange-label",
        )
        yield Input(placeholder="e.g. this_week or last_3d (empty to clear)", id="timerange-input")

    def on_mount(self) -> None:
        self.query_one("#timerange-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)


class MemoryApp(App):
    """Textual TUI for browsing and searching conversation memories."""

    ENABLE_COMMAND_PALETTE = False
    TITLE = "Memory"

    BINDINGS = [
        Binding("up,j", "cursor_up", "Up", show=False),
        Binding("down,k", "cursor_down", "Down", show=False),
        Binding("/", "search", "Search"),
        Binding("t", "time_range", "Time Range"),
        Binding("l", "cycle_limit", "Cycle Limit"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, store: MemoryStore, agent_name: str) -> None:
        super().__init__()
        self._store = store
        self._entries: list[MemoryEntry] = []
        self._query = ""
        self._time_range = ""
        self._limit = 10
        if agent_name:
            self.TITLE = f"Memory (agent: {agent_name})"

    def on_mount(self) -> None:
        self._load_entries()
        self._render_table()

    def on_unmount(self) -> None:
        self._store.close()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Vertical(
                Static("", id="status-bar"),
                DataTable(id="results", cursor_type="row"),
            ),
            RichLog(id="content-panel", highlight=True, markup=True, wrap=True),
        )
        yield Footer()

    def _load_entries(self) -> None:
        if self._query or self._time_range:
            self._entries = self._store.recall(
                query=self._query or None,
                time_range=self._time_range or None,
                limit=self._limit,
            )
        else:
            self._entries = self._store.list_all(limit=self._limit)

    def _render_table(self) -> None:
        table = self.query_one("#results", DataTable)
        saved_row = table.cursor_coordinate.row
        table.clear()

        if not table.columns:
            table.add_column("#", width=4)
            table.add_column("Key", width=20)
            table.add_column("User", width=40)
            table.add_column("Assistant", width=40)

        for i, e in enumerate(self._entries, 1):
            user_preview = e.user_msg.replace("\n", " ")[:60]
            asst_preview = e.assistant_msg.replace("\n", " ")[:60]
            table.add_row(str(i), e.key[:19], user_preview, asst_preview)

        table.styles.width = "50%"
        content_panel = self.query_one("#content-panel", RichLog)
        content_panel.styles.width = "50%"

        status = self.query_one("#status-bar", Static)
        if self._query:
            status.update(f"[bold]Search:[/bold] '{self._query}'  |  {len(self._entries)} results  |  limit={self._limit}  |  time_range={self._time_range or 'all'}")
        elif self._time_range:
            status.update(f"[bold]Browse by time[/bold]  |  {len(self._entries)} entries  |  limit={self._limit}  |  time_range={self._time_range}")
        else:
            status.update(f"[bold]Browse all[/bold]  |  {len(self._entries)} entries  |  limit={self._limit}")

        if table.row_count > 0:
            if saved_row >= table.row_count:
                saved_row = table.row_count - 1
            table.move_cursor(row=saved_row)

    def _show_content(self, row_idx: int) -> None:
        panel = self.query_one("#content-panel", RichLog)
        if row_idx < len(self._entries):
            e = self._entries[row_idx]
            panel.clear()
            panel.write(f"[bold]#{e.id}[/bold] [dim]{e.key}[/dim]")
            panel.write(f"[dim]session: {e.session_id}  access: {e.accessed_at or '-'}  count: {e.access_count}[/dim]")
            panel.write("")
            panel.write(str(e.content))

    # ── actions ──────────────────────────────────────────────────────────

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "results" and event.cursor_row is not None:
            self._show_content(event.cursor_row)

    def action_cursor_up(self) -> None:
        table = self.query_one("#results", DataTable)
        if table.row_count > 0:
            table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#results", DataTable)
        if table.row_count > 0:
            table.action_cursor_down()

    def action_search(self) -> None:
        def on_result(value: str | None) -> None:
            if value is not None:
                self._query = value.strip()
                self._load_entries()
                self._render_table()

        self.push_screen(SearchScreen(), on_result)

    def action_time_range(self) -> None:
        def on_result(value: str | None) -> None:
            if value is not None:
                self._time_range = value.strip()
                self._load_entries()
                self._render_table()

        self.push_screen(TimeRangeScreen(), on_result)

    def action_cycle_limit(self) -> None:
        limits = [5, 10, 20, 50, 100]
        try:
            idx = limits.index(self._limit)
            self._limit = limits[(idx + 1) % len(limits)]
        except ValueError:
            self._limit = 10
        self._load_entries()
        self._render_table()
        self.notify(f"Limit: {self._limit}")

    def action_refresh(self) -> None:
        self._load_entries()
        self._render_table()
