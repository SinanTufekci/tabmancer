"""Fresh sessions in several projects at once: the `n` dialog and `tabmancer new`.

A long conversation drags its history into every turn. Starting clean is often better,
so this picks folders (the projects you worked in lately, or any folder you type) and
opens a new session in each, all in one go, optionally with the same first message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, TextArea
from textual.widgets.option_list import Option

from . import clis, picks, teams, workspaces
from .dialogs import Choice, frame, title
from .launch import Launch, Mode
from .sessions import Session, folder_key
from .theme import AMBER, CYAN, MUTED, TEXT, keys


@dataclass
class Project:
    cwd: str
    sessions: int = 0
    last: datetime | None = None
    used: set[str] = field(default_factory=set)  # the CLIs it was worked on with

    @property
    def name(self) -> str:
        return Path(self.cwd).name or self.cwd


def projects(sessions: list[Session]) -> list[Project]:
    """Every folder that has sessions or sits in a workspace, most recently used first."""
    found: dict[str, Project] = {}
    for session in sessions:
        if not session.cwd:
            continue
        project = found.setdefault(folder_key(session.cwd), Project(session.cwd))
        project.sessions += 1
        project.used.add(session.cli)
        if project.last is None or session.last_active > project.last:
            project.last = session.last_active
    for workspace in workspaces.load().values():
        for entry in workspace.entries:
            found.setdefault(folder_key(entry.cwd), Project(entry.cwd))
    alive = [p for p in found.values() if Path(p.cwd).is_dir()]
    return sorted(alive, key=lambda p: (p.last is None, -(p.last.timestamp() if p.last else 0), p.name.casefold()))


def match(name: str, known: list[Project]) -> Project | None:
    """A folder path, or the name of a recent project (its most recent folder of that name)."""
    path = Path(os.path.expanduser(name))
    if path.is_dir():
        return Project(str(path.resolve()))
    return next((p for p in known if p.name.casefold() == name.casefold()), None)


def topics(message: str | None) -> list[str]:
    """The first messages written, one a line: each line its own session."""
    return [" ".join(line.split()) for line in (message or "").split("\n") if line.strip()]


def launches(folders: list[str], cli: str, prompt: str | None, pick: picks.Pick | None = None) -> list[Launch]:
    """A fresh session in each folder for each line of the message (one, with no message); Claude
    Code ones with the model and effort picked (picks.fresh). A tab is titled by its line."""
    out = []
    for cwd in folders:
        for topic in topics(prompt) or [None]:
            about = topic if topic is None or len(topic) <= 32 else topic[:31] + "…"
            out.append(picks.fresh(Launch(title=f"{Path(cwd).name or cwd}: {about or 'new'}", cwd=cwd, mode=Mode.NEW,
                                          session_id="", prompt=topic, cli=cli), pick or picks.Pick()))
    return out


ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth")


def linked_team(folder: str, message: str, pick: picks.Pick, taken: set[str]) -> teams.Team:
    """The lines of the message as a team of peers in one folder: a line "name: topic" is a member of
    that name, any other line one named first, second... in its order."""
    members: list[teams.Member] = []
    for index, line in enumerate(topics(message)):
        named = teams.parse_members(line) if teams.MEMBER_LINE.match(line) else []
        member = named[0] if named else teams.Member(ORDINALS[index % len(ORDINALS)], line)
        while member.name in {m.name for m in members}:
            member = replace(member, name=f"{member.name}{index + 1}")
        members.append(replace(member, model=member.model or pick.model, effort=member.effort or pick.effort))
    base = teams.clean(Path(folder).name)[:18] or "team"
    name = next(n for n in (base if k == 1 else f"{base}-{k}" for k in range(1, 100)) if n not in taken)
    goal = "; ".join(m.focus for m in members)
    return teams.new_team(name, folder, goal, members, peers=True)


@dataclass
class Request:
    """What the dialog asks for: fresh sessions in these folders, or (linked) a team of peers."""
    folders: list[str]
    cli: str
    message: str = ""
    pick: picks.Pick = field(default_factory=picks.Pick)
    linked: bool = False


def default_cli() -> str:
    """Claude Code if it is on, else whichever CLI is on."""
    on = clis.enabled()
    return "claude" if "claude" in on else on[0]


class FolderList(OptionList):
    """The folders, each with a [x] or [ ] in front, as in the session list."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("space", "tick", "Tick", show=False)]

    def action_tick(self) -> None:
        if self.highlighted is not None and self.option_count:
            self.screen.toggle(self.get_option_at_index(self.highlighted).id)


class NewSessions(ModalScreen[Request | None]):
    """Tick folders, maybe write a first message, Enter opens a fresh session in each."""

    # As tall as the window lets it be, the folders taking what the rest leaves: with many projects
    # the list once pushed the message, the picks and the keys off a 120x30 window.
    DEFAULT_CSS = frame("NewSessions", "large", height="90%") + """
    NewSessions #top { height: 1; }
    NewSessions #title { width: 1fr; }
    NewSessions #cli { width: auto; }
    NewSessions Input, NewSessions TextArea { margin-top: 1; background: $panel; }
    NewSessions Input:focus, NewSessions TextArea:focus { background: $panel-lighten-2; }
    NewSessions FolderList { height: 1fr; min-height: 3; margin-top: 1; background: $surface; }
    NewSessions #new-first { height: auto; max-height: 4; }
    NewSessions #new-link { color: $text-muted; }
    NewSessions .pick-row { height: 1; }
    NewSessions #new-model-row { margin-top: 1; }
    NewSessions .pick-row > Label { width: 8; color: $text-muted; }
    NewSessions #new-pick-words { color: $text-muted; }
    NewSessions #new-note { color: $text-muted; margin-top: 1; }
    """
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "start", "Start", priority=True),
        Binding("ctrl+s", "start(True)", "Start", priority=True),
        Binding("ctrl+o", "next_cli", "CLI", priority=True),
        Binding("ctrl+l", "link", "Linked", priority=True),
    ]

    def __init__(self, found: list[Project], ticked: set[str] | None = None) -> None:
        super().__init__()
        self.found = found
        self.ticked: set[str] = set(ticked or ())  # folder keys
        self.extra: list[Project] = []  # folders typed in by hand
        self.shown: dict[str, Project] = {}
        self.cli = default_cli()
        self.linked = False  # Ctrl+L: the lines as a team of peers in the one folder ticked

    def compose(self) -> ComposeResult:
        # Ids of their own: the dashboard's stylesheet hides its own #filter box.
        with Vertical():
            with Horizontal(id="top"):
                yield Label("", id="title")
                yield Label("", id="cli")
            yield Input(placeholder="Filter projects, or type a folder path", id="new-filter", compact=True)
            yield FolderList(id="new-folders")
            yield TextArea(id="new-first", compact=True,
                           placeholder="First message (optional). Enter here adds a line: each line a session of its own")
            yield Label("", id="new-link")
            with Horizontal(classes="pick-row", id="new-model-row"):
                yield Label("Model")
                yield Choice([picks.DEFAULT, *picks.MODELS], id="new-model")
            with Horizontal(classes="pick-row"):
                yield Label("Effort")
                yield Choice([picks.DEFAULT, *picks.EFFORTS], id="new-effort")
            yield Label("", id="new-pick-words")
            yield Label("These start clean. To carry a session's work on in a fresh one, press h on it in the list.",
                        id="new-note")
            yield Label(keys(("Space", "tick"), ("Enter/Ctrl+S", "start them"), ("Tab", "next box"),
                             ("Ctrl+L", "linked"), ("Ctrl+O", "the CLI"), ("Esc", "cancel")), classes="hint")

    def on_mount(self) -> None:
        self.show_folders()
        self.show_cli()
        self.show_link()
        self.folders.focus()

    @property
    def message(self) -> TextArea:
        return self.query_one("#new-first", TextArea)

    # --- several topics, linked -------------------------------------------------------------

    def can_link(self) -> str:
        """Why the lines cannot be linked now; empty when they can."""
        if self.cli != "claude":
            return "linked sessions are Claude Code's"
        if len(topics(self.message.text)) < 2:
            return "write two lines or more"
        if len(self.ticked) != 1:
            return "tick one folder"
        return ""

    def show_link(self) -> None:
        """Under the messages: what the lines open, apart or linked as a team of peers."""
        lines = len(topics(self.message.text))
        label = self.query_one("#new-link", Label)
        if lines < 2:
            label.update(Text("Two lines or more: a session for each; Ctrl+L links them as a team of peers.",
                              style=MUTED))
            return
        why = self.can_link()
        out = Text()
        for on, words in ((not self.linked or bool(why), f"{lines} sessions apart"),
                          (self.linked and not why, "linked: a team of peers that know of each other and message")):
            out.append(("● " if on else "○ ") + words, style=f"bold {TEXT}" if on else MUTED)
            out.append("   ")
        out.append(f"Ctrl+L ({why})" if why and self.linked else "Ctrl+L", style=AMBER if why and self.linked else MUTED)
        label.update(out)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self.show_link()

    def action_link(self) -> None:
        self.linked = not self.linked
        self.show_link()

    @property
    def folders(self) -> FolderList:
        return self.query_one(FolderList)

    @property
    def filter_box(self) -> Input:
        return self.query_one("#new-filter", Input)

    # --- the list -------------------------------------------------------------------

    def candidates(self) -> list[Project]:
        needle = self.filter_box.value.strip()
        everything = self.extra + self.found
        if not needle:
            return everything
        typed = Path(os.path.expanduser(needle))
        hits = [p for p in everything if needle.casefold() in f"{p.name} {p.cwd}".casefold()]
        if typed.is_dir() and folder_key(str(typed)) not in {folder_key(p.cwd) for p in hits}:
            hits.insert(0, Project(str(typed.resolve())))
        return hits

    def show_folders(self) -> None:
        folders = self.folders
        keep = folders.highlighted or 0
        folders.clear_options()
        self.shown = {folder_key(p.cwd): p for p in self.candidates()}
        folders.add_options([Option(self.prompt_for(key, p), id=key) for key, p in self.shown.items()])
        if self.shown:
            folders.highlighted = min(keep, folders.option_count - 1)
        else:  # say so, rather than show an empty box
            typed = self.filter_box.value.strip()
            folders.add_option(Option(Text(f"Nothing matches “{typed}”. A folder path works too." if typed else
                                           "No recent projects. Type a folder path in the box above.",
                                           style=MUTED), disabled=True))
        self.show_title()

    def prompt_for(self, key: str, project: Project) -> Text:
        when = f"{project.sessions} session{'s' * (project.sessions != 1)}" if project.sessions else "no sessions"
        if project.last:
            when += f" · {_ago(project.last)}"
        used = ", ".join(sorted(project.used - {"claude"}))
        mark = "[x] " if key in self.ticked else "[ ] "
        return Text.assemble((mark, f"bold {CYAN}" if key in self.ticked else MUTED),
                             (f"{project.name[:22]:<23}", f"bold {TEXT}"),
                             (f"{_clip(project.cwd, 48):<49}", MUTED), (when, MUTED),
                             (f" · {used}" if used else "", CYAN))

    def toggle(self, key: str | None) -> None:
        if key is None or key not in self.shown:
            return
        self.ticked ^= {key}
        if key in self.ticked and key not in {folder_key(p.cwd) for p in self.found + self.extra}:
            self.extra.insert(0, self.shown[key])  # a typed folder stays once it is ticked
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
        for choice in self.query(Choice):
            choice.set_class(self.cli != "claude", "-off")
        self.show_pick()

    # --- model and effort -----------------------------------------------------------

    def pick(self) -> picks.Pick:
        """What the Model and Effort rows say; nothing for other CLIs."""
        if self.cli != "claude":
            return picks.Pick()
        model, effort = (self.query_one(f"#new-{row}", Choice).value for row in ("model", "effort"))
        return picks.Pick("" if model == picks.DEFAULT else model, "" if effort == picks.DEFAULT else effort)

    def show_pick(self) -> None:
        """A line on the row in focus, or on what is picked: what it means for the sessions."""
        words = self.query_one("#new-pick-words", Label)
        if self.cli != "claude":
            words.update(f"Model and effort are for Claude Code sessions; {clis.label(self.cli)} uses its own settings.")
            return
        model, effort = self.query_one("#new-model", Choice), self.query_one("#new-effort", Choice)
        if self.focused is effort or (self.focused is not model and effort.value != picks.DEFAULT
                                      and model.value == picks.DEFAULT):
            words.update(f"{effort.value}: {picks.EFFORT_WORDS[effort.value]}")
        elif self.focused is model or model.value != picks.DEFAULT:
            words.update(f"{model.value}: {picks.MODEL_WORDS[model.value]}")
        else:
            words.update("Your Claude Code settings pick the model and effort. Tab to a row, then ←/→ for others.")

    def on_choice_changed(self, event: Choice.Changed) -> None:
        self.show_pick()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if self.is_mounted:
            self.show_pick()

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
        on = clis.enabled()
        self.cli = on[(on.index(self.cli) + 1) % len(on)] if self.cli in on else on[0]
        self.show_cli()
        self.show_link()

    def action_start(self, now: bool = False) -> None:
        """Enter: start them, but in the message box a new line (a session more); Ctrl+S: start."""
        if self.focused is self.message and not now:
            self.message.insert("\n")
            return
        box = self.filter_box
        typed = Path(os.path.expanduser(box.value.strip())) if box.value.strip() else None
        if self.focused is box and typed is not None and typed.is_dir():
            key = folder_key(str(typed))
            if key not in self.ticked:  # Enter on a typed folder ticks it; the next Enter starts
                self.toggle(key)
                box.value = ""
                self.folders.focus()
                return
        if not self.ticked:  # Enter on one folder without ticking anything opens that one
            folders = self.folders
            if folders.highlighted is None or not folders.option_count:
                self.notify("Tick the folders first (Space).", severity="warning")
                return
            self.toggle(folders.get_option_at_index(folders.highlighted).id)
        known = {folder_key(p.cwd): p for p in self.extra + self.found} | self.shown
        chosen = [known[key].cwd for key in self.ticked if key in known]
        prompt = "\n".join(topics(self.message.text))
        self.dismiss(Request(chosen, self.cli, prompt, self.pick(), linked=self.linked and not self.can_link()))

    def action_cancel(self) -> None:
        self.dismiss(None)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def _ago(moment: datetime) -> str:
    seconds = max(0.0, (datetime.now() - moment).total_seconds())
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{int(seconds // size)}{unit} ago"
    return "just now"
