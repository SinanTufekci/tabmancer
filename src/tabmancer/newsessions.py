"""Fresh sessions in several projects at once: the `n` dialog (its rules: newforms.py, shared with
`tabmancer new` and the desktop window).

A long conversation drags its history into every turn. Starting clean is often better,
so this picks folders (the projects you worked in lately, or any folder you type) and
opens a new session in each, all in one go, optionally with the same first message.
"""

from __future__ import annotations

import contextlib
import time
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from . import agy, clis, improve, newforms, picks, router
from .dialogs import Choice, cards, frame, light_rows, pill, title
from .newforms import (  # noqa: F401  (re-exported: the dashboard, cli and the tests use them from here)
    ADVISOR_SHORT,
    AGY_CHAIN,
    AGY_SHORT,
    CAP_SHORT,
    ORDINALS,
    SHORT,
    Project,
    Request,
    ago,
    clip,
    default_cli,
    find,
    fit,
    home_as_tilde,
    launches,
    linked_team,
    match,
    matches,
    projects,
    term_pattern,
    topics,
)
from .sessions import folder_key  # noqa: F401  (re-exported: the tests use it)
from .summarize import SummaryError
from .theme import AMBER, CYAN, GREEN, MUTED, RED, TEXT, keys


class FolderList(OptionList):
    """The folders, each with a [x] or [ ] in front, as in the session list."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("space", "tick", "Tick", show=False)]

    def action_tick(self) -> None:
        if self.highlighted is not None and self.option_count:
            self.screen.toggle(self.get_option_at_index(self.highlighted).id)


class NewSessions(ModalScreen[Request | None]):
    """Tick folders, maybe write a first message, Enter opens a fresh session in each."""

    # Two columns: the folders on the left, in a card of their own; on the right the first message,
    # the model and effort, and what will open. A card is lit (its frame and title in the theme's
    # colour) while the cursor is in it. The help for the box in focus is one line under the cards;
    # the keys are docked at the foot, so nothing above them can push them out of sight. As tall as
    # the window lets it be: with many projects the list scrolls, the rest stays.
    DEFAULT_CSS = frame("NewSessions", "large", height="90%") + cards("NewSessions") + """
    NewSessions > Vertical { width: 95%; max-width: 150; max-height: 36; }
    NewSessions #top { height: 1; margin-bottom: 1; }
    NewSessions #title { width: 1fr; }
    NewSessions #cli { width: auto; }
    NewSessions #new-body { height: 1fr; }
    NewSessions #folders-card { width: 46%; height: 1fr; margin-right: 1; }
    NewSessions #new-side { width: 1fr; height: 1fr; }
    NewSessions #new-find { height: 1; }
    NewSessions #new-find > Label { width: 2; color: $text-muted; }
    NewSessions #new-head { height: 1; margin-top: 1; }
    NewSessions FolderList { height: 1fr; min-height: 3; border: none; padding: 0; background: $surface; }
    NewSessions FolderList:focus { border: none; }
    NewSessions #new-where { height: 1; color: $text-muted; }
    NewSessions #message-card { height: auto; }
    NewSessions #new-first { height: 3; }
    NewSessions #new-link { height: auto; }
    NewSessions #pick-card { height: auto; }
    NewSessions .row { height: auto; }
    NewSessions .row > .name { width: 7; color: $text-muted; }
    NewSessions .row.-lit > .name { color: $text; text-style: bold; }
    NewSessions #new-pick-words { color: $text-muted; height: auto; }
    NewSessions #new-route { height: auto; }
    NewSessions #opens-card { height: 1fr; min-height: 4; }
    NewSessions #new-opens { height: auto; }
    NewSessions #new-note { color: $text-muted; height: auto; dock: bottom; }
    NewSessions #new-help { height: 1; margin-top: 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "start", "Start", priority=True),
        Binding("ctrl+s", "start(True)", "Start", priority=True),
        Binding("ctrl+o", "next_cli", "CLI", priority=True),
        Binding("ctrl+l", "link", "Linked", priority=True),
        Binding("ctrl+r", "route", "Router's pick", priority=True),
        Binding("ctrl+e", "improve", "Improve the first message", priority=True),
    ]
    MESSAGE_NOTE = "optional · Ctrl+E improves it"

    def __init__(self, found: list[Project], ticked: set[str] | None = None) -> None:
        super().__init__()
        # The folders, the CLI, the linking and the router's answers (asked only with Ctrl+R): the rules
        # are newforms.NewForm's, this draws them.
        self.form = newforms.NewForm(found=found, ticked=set(ticked or ()))
        self.improving: str | None = None  # Ctrl+E: the first message being rewritten, as it was sent
        self.improve_note = Improving(self.MESSAGE_NOTE, f"improving with {improve.MODEL}")

    # The form's state by the names the dashboard's tests know it by.
    found = property(lambda self: self.form.found)
    ticked = property(lambda self: self.form.ticked)
    shown = property(lambda self: self.form.shown)
    routes = property(lambda self: self.form.routes)
    routing = property(lambda self: self.form.routing)

    @property
    def cli(self) -> str:
        return self.form.cli

    @cli.setter
    def cli(self, value: str) -> None:
        self.form.cli = value

    def compose(self) -> ComposeResult:
        # Ids of their own: the dashboard's stylesheet hides its own #filter box.
        with Vertical():
            with Horizontal(id="top"):
                yield Label("", id="title")
                yield Label("", id="cli")
            with Horizontal(id="new-body"):
                with Vertical(id="folders-card", classes="card"):
                    with Horizontal(id="new-find"):
                        yield Label("⌕")
                        yield Input(placeholder="Filter, or type a folder path", id="new-filter", compact=True)
                    yield Static("", id="new-head")
                    yield FolderList(id="new-folders")
                    yield Static("", id="new-where")
                with Vertical(id="new-side"):
                    with Vertical(id="message-card", classes="card"):
                        yield TextArea(id="new-first", compact=True,
                                       placeholder="Optional. A line each: a session for each line")
                        yield Label("", id="new-link")
                    with Vertical(id="pick-card", classes="card"):
                        # The card's title names the rows: the model above (agy's own in its place: its
                        # chain, no Claude model), the effort under it, then the context and the advisor.
                        for row in newforms.choices():
                            yield Choice(row.values, value=row.value, id=f"new-{row.name}", labels=row.labels)
                        yield Label("", id="new-pick-words")
                        yield Label("", id="new-route")
                    with Vertical(id="opens-card", classes="card"):
                        yield Static("", id="new-opens")
                        yield Label("These start clean: h in the list carries a session on.", id="new-note")
            yield Label("", id="new-help")
            yield Label(keys(("Space", "tick"), ("Enter/Ctrl+S", "start them"), ("Tab", "next box"),
                             ("Ctrl+R", "router's pick"), ("Ctrl+L", "linked"), ("Ctrl+O", "the CLI"),
                             ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        self.query_one("#folders-card").border_title = "Folders"
        self.query_one("#message-card").border_title = "First message"
        self.query_one("#message-card").border_subtitle = self.MESSAGE_NOTE
        self.query_one("#pick-card").border_title = "Model · Effort · Context · Advisor"
        self.show_folders()
        self.show_cli()
        self.show_link()
        self.folders.focus()
        self.call_after_refresh(self.show_folders)  # laid out: the columns as wide as the list is

    @property
    def message(self) -> TextArea:
        return self.query_one("#new-first", TextArea)

    # --- several topics, linked -------------------------------------------------------------

    def can_link(self) -> str:
        """Why the lines cannot be linked now; empty when they can (newforms.can_link)."""
        return self.form.can_link(self.message.text)

    def show_link(self) -> None:
        """Under the messages, once there are two lines or more: what they open, apart or linked as a
        team of peers. And on the right, what will open."""
        lines = len(topics(self.message.text))
        label = self.query_one("#new-link", Label)
        label.display = lines >= 2
        if lines >= 2:
            why = self.can_link()
            linked = self.form.linked
            out = Text.assemble(pill(f"{lines} sessions apart", not linked or bool(why)), " ",
                                pill("linked, as peers", linked and not why), "  ")
            out.append(f"Ctrl+L ({why})" if why and linked else "Ctrl+L",
                       style=AMBER if why and linked else MUTED)
            label.update(out)
        self.show_opens()

    def show_opens(self) -> None:
        """The card on the right: a line for each tab that will open, the folder and its message."""
        card, opens = self.query_one("#opens-card"), self.query_one("#new-opens", Static)
        what = self.form.opens(self.message.text)
        if not what.rows:
            card.border_title = what.title
            opens.update(Text("Tick folders on the left with Space.", style=MUTED))
            return
        card.border_title = Text(what.title, style=f"bold {GREEN}")
        card.border_subtitle = Text.assemble(clis.label(self.cli), *(f" · {part}" for part in self.pick_parts()))
        room = max(1, (card.content_size.height or 4) - 2)  # under them, the note
        rows = what.rows
        out = Text()
        for index, (folder, line) in enumerate(rows[:room]):
            if index:
                out.append("\n")
            out.append("● ", style=GREEN)
            out.append(folder, style=f"bold {CYAN}")
            out.append(f"  {line}" if line else "  a fresh session", style=TEXT if line else MUTED)
        if len(rows) > room:
            out.append(f"\n  +{len(rows) - room} more", style=MUTED)
        out.no_wrap, out.overflow = True, "ellipsis"
        opens.update(out)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.show_improving()  # how Ctrl+E went stays only while the text is what it went for
        self.show_link()
        self.show_route()  # the router is asked only with Ctrl+R: never half-way through a message

    def action_link(self) -> None:
        self.form.toggle_link()
        self.show_link()

    @property
    def folders(self) -> FolderList:
        return self.query_one(FolderList)

    @property
    def filter_box(self) -> Input:
        return self.query_one("#new-filter", Input)

    # --- the list -------------------------------------------------------------------

    def candidates(self) -> list[Project]:
        return find(self.filter_box.value, self.form.everything())

    def show_folders(self) -> None:
        folders = self.folders
        keep = folders.highlighted or 0
        folders.clear_options()
        self.form.listed(self.filter_box.value)
        folders.add_options([Option(self.prompt_for(key, p), id=key) for key, p in self.shown.items()])
        if self.shown:
            folders.highlighted = min(keep, folders.option_count - 1)
        else:  # say so, rather than show an empty box
            typed = self.filter_box.value.strip()
            folders.add_option(Option(Text(f"Nothing matches “{typed}”. A folder path works too." if typed else
                                           "No recent projects. Type a folder path in the box above.",
                                           style=MUTED), disabled=True))
        everything = len(self.form.everything())
        self.query_one("#folders-card").border_subtitle = (
            f"{len(self.shown)} of {everything}" if self.filter_box.value.strip() else
            f"{everything} project{'s' * (everything != 1)}")
        self.query_one("#new-head", Static).update(self.heading())
        self.show_where()
        self.show_title()

    def columns(self) -> tuple[int, int]:
        """How wide a row's name and its CLI are, from the list's width: the name takes what is left."""
        # Room kept for the scrollbar, which comes only once the rows are in, and a cell free at the end.
        list_ = self.folders
        width = (list_.size.width - list_.styles.scrollbar_size_vertical - 1 if self.is_mounted else 0)
        width = width if width > 24 else 48
        tag = max((len(", ".join(sorted(p.used - {"claude"}))) for p in self.form.everything()), default=0)
        tag = tag + 2 if tag else 0
        return max(8, width - 2 - TAIL - tag), tag

    def heading(self) -> Text:
        name, tag = self.columns()
        return Text("  " + "PROJECT".ljust(name) + "SESSIONS".rjust(10) + "LAST".rjust(10) + " " * tag,
                    style=f"bold {MUTED}")

    def prompt_for(self, key: str, project: Project) -> Text:
        """A folder's row: ✓ when ticked, its name, its sessions, when it was last worked in, and any
        CLI but Claude Code it was worked with. Where it is shows under the list (show_where). What
        the filter found is lit."""
        ticked = key in self.ticked
        name_width, tag = self.columns()
        used = ", ".join(sorted(project.used - {"claude"}))
        row = Text.assemble(("✓ " if ticked else "· ", f"bold {CYAN}" if ticked else MUTED),
                            project_columns(project, name_width, self.filter_box.value.split(), CYAN if ticked else TEXT),
                            (f"  {used}".ljust(tag) if tag else "", CYAN))
        row.no_wrap, row.overflow = True, "ellipsis"  # one line, however narrow the window
        return row

    def show_where(self) -> None:
        """Under the list: where the highlighted folder is, your home as ~, the filter's words lit."""
        folders, where = self.folders, self.query_one("#new-where", Static)
        option = folders.get_option_at_index(folders.highlighted) if folders.highlighted is not None else None
        project = self.shown.get(option.id) if option is not None and option.id else None
        if project is None:
            where.update("")
            return
        path = Text("▸ " + clip(home_as_tilde(project.cwd), max(8, (where.content_size.width or 48) - 2)),
                    style=MUTED)
        for term in self.filter_box.value.split():
            path.highlight_regex(term_pattern(term), style=f"bold underline {AMBER}")
        where.update(path)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if self.is_mounted:
            self.show_where()

    def on_resize(self, event: events.Resize) -> None:
        if self.is_mounted:  # the columns follow the list's width
            self.show_folders()
            self.show_opens()

    def toggle(self, key: str | None) -> None:
        if not self.form.toggle(key):
            return
        self.folders.replace_option_prompt(key, self.prompt_for(key, self.shown[key]))
        self.show_title()
        if self.is_mounted:
            self.show_link()

    def show_title(self) -> None:
        count = len(self.ticked)
        self.query_one("#title", Label).update(title("New sessions", *([f"{count} folder{'s' * (count != 1)} ticked"]
                                                                    if count else [])))

    def show_cli(self) -> None:
        many = len(clis.enabled()) > 1
        self.query_one("#cli", Label).update(Text.assemble(
            ("with ", MUTED), (clis.label(self.cli), f"bold {CYAN}"), ("  Ctrl+O" if many else "", MUTED)))
        for name, (shown, on) in newforms.rows(self.cli).items():
            choice = self.query_one(f"#new-{name}", Choice)
            choice.display = shown
            choice.set_class(not on, "-off")
        if self.form.wants_quota():  # once a dialog: what agy has left, for the chain's line
            self.run_worker(self.read_quota, thread=True, group="agy-quota", exit_on_error=False)
        self.show_pick()

    def read_quota(self) -> None:
        """In a thread: agy's quota (a few seconds, no quota spent), then the line says it."""
        if agy.refresh_quota() and self.is_mounted:
            self.app.call_from_thread(self.show_pick)

    def agy_model(self) -> tuple[str, str]:
        """The model agy's sessions get from its row, and why (newforms.agy_model: never a Claude one)."""
        return newforms.agy_model(self.query_one("#new-agy-model", Choice).value)

    # --- model and effort -----------------------------------------------------------

    def values(self) -> dict[str, str]:
        """Each row's value, by its name (newforms.ROWS)."""
        return {row: self.query_one(f"#new-{row}", Choice).value for row in newforms.ROWS}

    def pick(self) -> picks.Pick:
        """What the rows say (newforms.pick_of)."""
        return newforms.pick_of(self.cli, self.values())

    def pick_parts(self) -> list[str]:
        """The pick as the card of what opens says it: 'sonnet · high', '300k context'."""
        return newforms.pick_parts(self.pick())

    def show_pick(self) -> None:
        """A line on the row in focus, or on what is picked: what it means for the sessions."""
        focused = next((row for row in newforms.ROWS if self.focused is self.query_one(f"#new-{row}")), None)
        self.query_one("#new-pick-words", Label).update(newforms.pick_words(self.cli, self.values(), focused))
        if self.is_mounted:
            self.show_opens()
            self.show_route()

    def show_help(self) -> None:
        """One line under the cards: how to use the box in focus."""
        focused = self.focused
        kind = "message" if focused is self.message else "choice" if isinstance(focused, Choice) else "folders"
        self.query_one("#new-help", Label).update(newforms.help_words(kind))

    def on_choice_changed(self, event: Choice.Changed) -> None:
        self.show_pick()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if self.is_mounted:
            self.show_pick()
            self.show_help()
            light_rows(self)

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        light_rows(self)

    # --- the router -----------------------------------------------------------------

    def route_key(self) -> str:
        """The first message as the router is asked about it: its lines, each one tidied."""
        return newforms.route_key(self.message.text)

    def known(self) -> list[newforms.Project]:
        """The ticked folders' projects, as the list knows them."""
        return self.form.known()

    def chosen(self) -> list[str]:
        return self.form.chosen()

    def chosen_names(self) -> list[str]:
        return self.form.chosen_names()

    def ask_route(self, take: bool = False) -> None:
        """Ask the router about the first message as it is now, unless it was asked already: one
        quick call to a small model, in a thread. With take, its pick goes into the rows when it comes."""
        asked = self.form.ask(self.message.text, take)
        self.put(asked.take)
        if asked.slot is not None:
            slot, folders = asked.slot, self.chosen_names()
            self.run_worker(lambda: self.ask_router(slot, folders), thread=True, group="route", exit_on_error=False)
        self.show_route()

    def ask_router(self, slot: tuple[str, str], folders: list[str]) -> None:
        """In a thread: the router's answer, or why there is none. Whatever goes wrong, the answer
        comes back, so the line never stays at "asking…" and Ctrl+R can ask again."""
        cli, key = slot
        found: router.Route | str = "the router stopped"
        try:
            found = router.suggest(key, folders, cli)
        except Exception as problem:  # noqa: BLE001 - any failure is said on the router's line
            found = str(problem) or type(problem).__name__
        finally:
            with contextlib.suppress(RuntimeError):  # the app has closed meanwhile
                self.app.call_from_thread(self.routed, slot, found)

    def routed(self, slot: tuple[str, str], found: router.Route | str) -> None:
        values = self.form.routed(slot, found, self.form.slot(self.message.text) if self.is_mounted else None)
        if values is None:
            return  # the message or the CLI has changed since: kept, should it come back
        self.put(values)
        self.show_route()

    def take(self, route: router.Route) -> None:
        """The router's pick into the rows: what it named, the rest as it was. An agy pick goes into
        agy's row (Claude Code's stay as they were): as "chain" when it is what the chain picks now,
        so the sessions still follow the quota when they open; another CLI's pick is only said."""
        self.put(self.form.taking(route))

    def put(self, values: dict[str, str]) -> None:
        """Row values (newforms.route_take) into the rows."""
        for row, value in values.items():
            self.query_one(f"#new-{row}", Choice).pick(value)

    def taken(self, route: router.Route) -> bool:
        return newforms.taken(route, self.pick())

    def show_route(self) -> None:
        """The router's line, under the rows: what it suggests and why, that it is asking, or why not."""
        label = self.query_one("#new-route", Label)
        parts = self.form.route_line(self.message.text, self.pick())
        label.display = parts is not None
        if parts is not None:
            label.update(Text.assemble(*((text, TONES[tone]) for text, tone in parts)))

    def action_route(self) -> None:
        """Ctrl+R: the router's pick into the rows, asked for now if it is not there yet."""
        refusal = newforms.route_refusal(self.cli, self.message.text)
        if refusal is not None:
            words, seconds = refusal
            self.notify(words, severity="warning", timeout=seconds)
            return
        self.ask_route(take=True)

    # --- improving the first message -------------------------------------------------

    def action_improve(self) -> None:
        """Ctrl+E: Claude rewrites the first message clearer, line for line (each line is a session).
        It goes into the box as one edit, so Ctrl+Z there puts yours back."""
        if self.improving is not None:
            return
        text = self.message.text
        refusal = newforms.improve_refusal(text)
        if refusal:
            self.notify(refusal, severity="warning", timeout=5)
            return
        self.improving = text
        self.improve_note.start(self, self.message)
        folders = self.chosen_names()
        self.run_worker(lambda: self.ask_improve(text, folders), thread=True, group="improve", exit_on_error=False)

    def ask_improve(self, text: str, folders: list[str]) -> None:
        """In a thread: the rewrite, or why there is none."""
        try:
            better, why = improve.improve_message(text, folders), ""
        except (SummaryError, improve.NotImproved, OSError) as problem:
            better, why = "", str(problem) or type(problem).__name__
        self.app.call_from_thread(self.improved, text, better, why)

    def improved(self, text: str, better: str, why: str) -> None:
        self.improving = None
        if not self.is_mounted:
            return
        if self.improve_note.finish(self, self.message, "#message-card", text, better, why, self.notify):
            self.notify("First message improved: Ctrl+Z in the box puts yours back.", timeout=6)

    def show_improving(self) -> None:
        self.query_one("#message-card").border_subtitle = self.improve_note.note(self.message.text)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input is self.filter_box:
            self.show_folders()

    def on_key(self, event: events.Key) -> None:
        # Typing in the list goes to the filter, so a project is found by its name.
        if self.focused is self.folders and event.is_printable and event.character not in (None, " "):
            box = self.filter_box
            box.focus()
            box.insert_text_at_cursor(event.character)
            event.stop()
        elif self.focused is self.filter_box and event.key == "down":
            self.folders.focus()
            event.stop()

    # --- actions --------------------------------------------------------------------

    def action_next_cli(self) -> None:
        self.form.next_cli()
        self.show_cli()
        self.show_link()

    def action_start(self, now: bool = False) -> None:
        """Enter: start them, but in the message box a new line (a session more); Ctrl+S: start."""
        if self.focused is self.message and not now:
            # Not while Ctrl+E rewrites it: insert writes in a read-only box too, and the rewrite,
            # finding the text changed, was thrown away.
            if not self.message.read_only:
                self.message.insert("\n")
            return
        box, folders = self.filter_box, self.folders
        listed = folders.highlighted is not None and bool(folders.option_count)
        step = self.form.enter(box.value if self.focused is box else None,
                               folders.get_option_at_index(folders.highlighted).id if listed else None, listed)
        if step.kind == newforms.TICK:  # Enter on a typed folder ticks it; the next Enter starts
            if step.key not in self.form.shown:  # typed a moment ago: the list may not have taken it in yet
                self.show_folders()
            self.toggle(step.key)
            box.value = ""
            folders.focus()
            return
        if step.kind == newforms.REFUSE:
            self.notify(step.note, severity="warning")
            return
        if step.key is not None:  # Enter on one folder without ticking anything opens that one
            self.toggle(step.key)
        self.dismiss(self.form.request(self.message.text, self.pick()))

    def action_cancel(self) -> None:
        self.dismiss(None)


SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# The router line's tones (newforms.route_line) in this terminal's colours.
TONES = {"muted": MUTED, "muted-bold": f"bold {MUTED}", "warn": AMBER, "warn-bold": f"bold {AMBER}",
         "pick": f"bold {CYAN}", "ok": GREEN, "ok-bold": f"bold {GREEN}"}
FLASH_SECONDS = 1.5  # the card's frame green, a moment, when a rewrite went in


class Improving:
    """Ctrl+E's word on its card's frame, in the new-session dialog and the new-team form: what the
    key does; while Claude rewrites, that it is at it and for how long (the box takes no typing
    meanwhile, so a key pressed then cannot throw the rewrite away); and then how it went, kept
    until the text changes. The frame used to go back to what it said before Ctrl+E, and the toast
    that said it was done came and went, so a rewrite done looked like one never started."""

    def __init__(self, idle: str, doing: str) -> None:
        self.idle, self.doing = idle, doing
        self.since: float | None = None  # when the rewrite was asked for
        self.outcome: Text | None = None
        self.about = ""  # the text the outcome is about: once the box says otherwise, it goes
        self.ticker = None

    def start(self, screen, box: TextArea) -> None:
        self.since, self.outcome = time.monotonic(), None
        box.read_only = True
        self.ticker = screen.set_interval(0.25, screen.show_improving)
        screen.show_improving()

    def finish(self, screen, box: TextArea, card: str, sent: str, better: str, why: str, notify) -> bool:
        """The rewrite into the box (put_improved), and its outcome on the frame. True when put in."""
        self.since = None
        if self.ticker is not None:
            self.ticker.stop()
            self.ticker = None
        box.read_only = False
        put, self.outcome = put_improved(box, sent, better, why, notify)
        self.about = box.text
        if put:
            frame = screen.query_one(card)
            frame.add_class("-improved")
            screen.set_timer(FLASH_SECONDS, lambda: frame.remove_class("-improved"))
        screen.show_improving()
        return put

    def note(self, text: str) -> Text | str:
        if self.since is not None:
            now = time.monotonic()
            return Text(f"{SPIN[int(now * 8) % len(SPIN)]} {self.doing} · {int(now - self.since)}s",
                        style=f"bold {AMBER}")
        if self.outcome is not None and text == self.about:
            return self.outcome
        return self.idle


def words(text: str) -> int:
    return len(text.split())


def put_improved(box: TextArea, sent: str, better: str, why: str, notify) -> tuple[bool, Text]:
    """The rewrite into its box as one edit (Ctrl+Z puts yours back), unless there is none, it says
    nothing new, or you changed the text while it was asked for. Says why not; (whether it was put
    in, how it went in a few words for the card's frame)."""
    if why:
        notify(f"Not improved: {why}", severity="warning", timeout=8)
        return False, Text(f"✗ not improved: {why}", style=f"bold {RED}")
    if box.text != sent:
        notify("You changed the text meanwhile, so the rewrite was not put in: Ctrl+E asks again.",
               severity="warning", timeout=6)
        return False, Text("✗ the text changed meanwhile · Ctrl+E asks again", style=f"bold {RED}")
    if " ".join(better.split()) == " ".join(sent.split()):
        notify("Claude found it clear as it is: nothing changed.", timeout=5)
        return False, Text("= clear as it was: nothing changed", style=TEXT)
    box.replace(better, (0, 0), box.document.end)
    box.move_cursor((0, 0))  # its start in sight, not its end
    box.scroll_home(animate=False)
    box.focus()
    return True, Text(f"✓ improved ({words(sent)} → {words(better)} words) · Ctrl+Z puts yours back",
                      style=f"bold {GREEN}")


TAIL = 20  # the sessions and the last time, right-aligned at a row's end


def project_columns(project: Project, width: int, terms: list[str], colour: str = TEXT) -> Text:
    """A project as a row shows it: its name (the filter's words lit) in width cells, its sessions,
    when it was last worked in."""
    name = Text(fit(project.name, width).ljust(width), style=f"bold {colour}")
    for term in terms:
        name.highlight_regex(term_pattern(term), style=f"bold underline {AMBER}")
    count = f"{project.sessions}" if project.sessions else "none"
    return Text.assemble(name, (count.rjust(10), TEXT if project.sessions else MUTED),
                         ((ago(project.last) if project.last else "").rjust(10), MUTED))
