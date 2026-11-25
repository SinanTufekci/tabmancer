"""The n form's state without a screen (newforms.NewForm): ticking, a folder typed in, the CLI and agy's
quota read once, linking, the router's answers (asked, taken, kept for a CLI no longer picked, a guess
asked again, a failure), and what Enter does; and the new-team form's where (teamforms.TeamForm.where)."""

from __future__ import annotations

from tabmancer import newforms, router, teamforms
from tabmancer.newforms import NewForm, Project
from tabmancer.picks import Pick
from tabmancer.sessions import folder_key


def form(tmp_path, *names: str, **kwargs) -> tuple[NewForm, dict[str, str]]:
    keys = {}
    found = []
    for name in names:
        folder = tmp_path / name
        folder.mkdir(exist_ok=True)
        found.append(Project(str(folder)))
        keys[name] = folder_key(str(folder))
    made = NewForm(found=found, **kwargs)
    made.listed("")
    return made, keys


def test_space_ticks_what_is_shown_and_a_typed_folder_stays_once_ticked(tmp_path):
    made, keys = form(tmp_path, "api", "web")
    assert made.toggle(keys["api"]) and made.ticked == {keys["api"]}
    assert not made.toggle(None) and not made.toggle("not shown")
    other = tmp_path / "other"
    other.mkdir()
    shown = made.listed(str(other))  # a path typed in the box lists that folder
    key = folder_key(str(other))
    assert list(shown) == [key] and made.extra == []
    made.toggle(key)
    assert [p.name for p in made.extra] == ["other"]
    made.listed("")
    assert sorted(made.chosen_names()) == ["api", "other"]
    made.toggle(key)  # unticked: it stays listed
    assert [p.name for p in made.everything()] == ["other", "api", "web"] and made.chosen_names() == ["api"]


def test_agys_quota_is_read_once_and_only_for_agy(tmp_path):
    made, _ = form(tmp_path, "api", cli="claude")
    assert not made.wants_quota()
    made.cli = "agy"
    assert made.wants_quota() and not made.wants_quota()


def test_linking_needs_one_folder_and_two_lines(tmp_path):
    made, keys = form(tmp_path, "api", "web", cli="claude")
    made.toggle(keys["api"])
    made.toggle_link()
    assert made.linked and not made.can_link("plan it\ntest it")
    assert made.opens("plan it\ntest it").title.endswith(", linked")
    assert made.request("plan it\ntest it", Pick()).linked
    made.toggle(keys["web"])
    assert made.can_link("plan it\ntest it") == "tick one folder"  # two folders: it says why
    assert not made.opens("plan it\ntest it").title.endswith(", linked")
    request = made.request("plan it\ntest it", Pick())
    assert not request.linked and sorted(request.folders) == sorted(made.chosen())


def route(model: str, cli: str = "claude", guessed: bool = False) -> router.Route:
    return router.Route(Pick(model, "low", "200k") if cli == "claude" else Pick(model), "why", cli, guessed)


def test_the_router_is_asked_once_a_message_and_its_answer_is_taken_with_ctrl_r(tmp_path):
    made, _ = form(tmp_path, "api", cli="claude")
    assert made.ask("too short") == newforms.RouteAsk()  # not worth asking
    message = "write the tests for picks"
    asked = made.ask(message, take=True)
    slot = ("claude", message)
    assert asked.slot == slot and made.routing == {slot} and made.take_route
    assert made.ask(message, take=True) == newforms.RouteAsk()  # being asked: not again
    assert made.routed(slot, route("haiku"), slot) == {"model": "haiku", "effort": "low", "cap": "200k"}
    assert not made.routing and not made.take_route
    assert made.ask(message) == newforms.RouteAsk()  # there already, no take: nothing to do
    assert made.ask(message, take=True).take == {"model": "haiku", "effort": "low", "cap": "200k"}


def test_an_answer_for_another_cli_or_message_is_kept_not_taken(tmp_path):
    made, _ = form(tmp_path, "api", cli="claude")
    message = "what does the config file hold"
    slot = made.ask(message, take=True).slot
    made.cli = "agy"
    assert made.routed(slot, route("haiku"), made.slot(message)) is None  # agy is picked now
    assert made.routes[slot] == route("haiku")
    assert made.routed(slot, route("haiku"), None) is None  # the form has closed meanwhile
    made.cli = "claude"
    assert made.ask(message, take=True).take["model"] == "haiku"  # kept: taken without asking again


def test_a_guess_is_asked_again_and_a_failure_is_said_then_asked_again(tmp_path):
    made, _ = form(tmp_path, "api", cli="claude")
    message = "refactor the session list"
    slot = made.ask(message, take=True).slot
    assert made.routed(slot, route("opus", guessed=True), slot) == {"model": "opus", "effort": "low", "cap": "200k"}
    assert made.ask(message).slot is None  # shown, not asked again without Ctrl+R
    assert made.ask(message, take=True).slot == slot  # Ctrl+R: a guess is no answer
    assert made.routed(slot, "the router stopped", slot) == {}
    line = made.route_line(message, Pick())
    assert any("the router stopped" in text for text, _ in line)
    assert made.ask(message, take=True).slot == slot and slot not in made.routes


def test_an_agy_pick_goes_into_agys_row_only(fake_home, tmp_path):
    made, _ = form(tmp_path, "api", cli="agy")
    message = "refactor the session list"
    slot = made.ask(message, take=True).slot
    taken = made.routed(slot, route("gemini-4-hydrogen-high", cli="agy"), slot)
    assert set(taken) <= {"agy-model"}  # never Claude Code's rows


def test_enter_ticks_a_typed_folder_starts_the_highlighted_or_says_to_tick(tmp_path):
    made, keys = form(tmp_path, "api", "web")
    other = tmp_path / "other"
    other.mkdir()
    assert made.enter(str(other), None, True) == newforms.Enter(newforms.TICK, folder_key(str(other)))
    assert made.enter(str(tmp_path / "missing"), None, False) == newforms.Enter(newforms.REFUSE,
                                                                                 note=newforms.TICK_FIRST)
    assert made.enter(None, keys["web"], True) == newforms.Enter(newforms.START, keys["web"])
    made.toggle(keys["api"])
    assert made.enter(None, keys["web"], True) == newforms.Enter(newforms.START)  # ticked: those start
    made.listed(str(other))
    made.toggle(folder_key(str(other)))
    assert made.enter(str(other), None, True) == newforms.Enter(newforms.START)  # typed, ticked already


def test_where_the_team_runs_tabs_background_peers_and_worktrees():
    team = teamforms.TeamForm(taken=set())
    assert team.where(False) == teamforms.Where([("each in a tab", True), ("the members in the background", False)],
                                                key="Ctrl+B")
    team.background, team.worktrees = True, True
    where = team.where(False)
    assert where.runs[1] == ("the members in the background", True) and where.worktrees
    peers = team.where(True)  # peers each in a tab, whatever Ctrl+B said: no key to switch it
    assert peers.runs == [("each in a tab", True)] and not peers.key and "peers" in peers.note
    assert (peers.worktrees_words, peers.worktrees_key) == ("own git worktrees", "Ctrl+O")
