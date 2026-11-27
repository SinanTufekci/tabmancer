"""A saved team deleted (teamkeys.TeamKeys.delete), as the terminal's T list deletes one: d twice, its sessions
kept; the first press says what the second does, a held teams.json says why nothing changed. No UI."""

from tabmancer import teamkeys, teams
from tabmancer.actions import Note
from tabmancer.config import Busy


class Surface:
    """What the keys say, kept; the teams' handovers not taken yet."""

    def __init__(self) -> None:
        self.said: list[Note] = []
        self.kept: dict = {}

    def say(self, note: Note) -> None:
        self.said.append(note)

    def kept_handovers(self) -> dict:
        return self.kept


def saved(*names: str) -> None:
    teams.save({name: teams.new_team(name, f"C:/{name}", "a goal", teams.parse_members("api: x")) for name in names})


def keys_at(surface: Surface, clock: list[float]) -> teamkeys.TeamKeys:
    return teamkeys.TeamKeys(surface, lambda: clock[0], {})


def test_d_twice_deletes_the_team_and_keeps_the_others():
    saved("shop", "docs")
    surface, clock = Surface(), [100.0]
    keys = keys_at(surface, clock)
    assert not keys.delete("shop")
    assert surface.said[-1].words == "Press d again to delete the team shop. Its sessions stay."
    assert set(teams.load()) == {"shop", "docs"}  # nothing yet
    surface.kept["shop"] = ("lead", "written", "line-up", False)
    clock[0] += 2
    assert keys.delete("shop")
    assert set(teams.load()) == {"docs"}
    assert surface.said[-1].words == teamkeys.deleted("shop") and surface.kept == {}  # its handover goes with it


def test_a_second_press_too_late_or_on_another_team_asks_again():
    saved("shop", "docs")
    surface, clock = Surface(), [100.0]
    keys = keys_at(surface, clock)
    keys.delete("shop")
    assert not keys.delete("docs")  # another team: a first press on that one
    clock[0] += teamkeys.DELETE_AGAIN_SECONDS + 1
    assert not keys.delete("docs")  # too late: asked again
    assert set(teams.load()) == {"shop", "docs"}
    assert [note.words for note in surface.said] == [teamkeys.delete_again(n) for n in ("shop", "docs", "docs")]


def test_a_held_teams_file_deletes_nothing_and_says_why(monkeypatch):
    saved("shop")
    surface, clock = Surface(), [100.0]
    keys = keys_at(surface, clock)
    keys.delete("shop")

    def held(name, tries=None):
        raise Busy("held")

    monkeypatch.setattr(teams, "drop", held)
    assert not keys.delete("shop")
    note = surface.said[-1]
    assert note.severity == "warning" and note.words.startswith("shop is not deleted:")
    assert set(teams.load()) == {"shop"}
