"""The new-team form's rules without a screen (teamforms.py): what is missing, what opens, the keys'
choices, a team at work, and that no UI comes with them. The form's own tests (test_new_team,
test_team_at_work, test_roles ...) show the same through its keys."""

from __future__ import annotations

import subprocess
import sys

from tabmancer import picks, teamforms, teams
from tabmancer.teamforms import Fields, JoinTeam, TeamForm, TellTeam


def test_teamforms_load_no_ui():
    code = ("import sys, tabmancer.teamforms; "
            "print(sorted({m.split('.')[0] for m in sys.modules} & {'textual', 'rich', 'PySide6', 'shiboken6'}))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def boxes(tmp_path, **kw) -> Fields:
    return Fields(**{"name": "Shop", "folder": str(tmp_path), "goal": "Add checkout", "members": teamforms.first_lineup(),
                     "most": "4", **kw})


def test_the_boxes_read_as_the_form_reads_them(tmp_path):
    fields = boxes(tmp_path, name="  My Team ", most="x", advisor=picks.NO_ADVISOR)
    name, folder, goal, members = fields.read()
    assert (name, folder, goal) == (teams.clean("  My Team "), str(tmp_path), "Add checkout")
    assert members[0].lead and len(members) == 4
    assert fields.most_n() == 0 and fields.chosen_advisor() == ""
    assert Fields(advisor="opus").chosen_advisor() == "opus"
    assert teamforms.answer("  ") is None and teamforms.answer(" yes ") == "yes"


def test_what_is_missing_then_what_opens(tmp_path):
    form = TeamForm({"taken"})
    assert form.missing(Fields()) == ["a name", "a project folder that exists", "the goal",  # no lead line: peers
                                      "two members at least, as peers (or a lead line)"]
    assert form.missing(boxes(tmp_path, name="taken")) == ["another name: taken exists already"]
    shown = form.preview(boxes(tmp_path))
    assert shown.ready and not shown.missing
    assert shown.tabs == f"Opens 4 tabs in {tmp_path.name}"
    assert shown.head == [(shown.tabs, "strong"), (". The lead starts last and hands out the work.", "muted")]
    assert form.preview(Fields(), asking=True).asking
    # Missing is said louder once Ctrl+S was tried, and Ctrl+S makes nothing then.
    assert form.create(Fields()) is None and form.tried and form.preview(Fields()).loud


def test_the_line_ups_cycle_and_the_lead_decides(tmp_path):
    form = TeamForm(set())
    members = teamforms.first_lineup()
    assert form.shown_lineup(members) == next(iter(teams.TEMPLATES))
    assert form.shown_lineup(members + "\nmore: x") == teamforms.OWN
    while form.picked() != teams.DECIDE:
        members = form.next_lineup(members)
    assert members == teamforms.LEAD_LINE and form.deciding(members) and not form.peers(members)
    fields = boxes(tmp_path, members=members, most="0")
    assert form.missing(fields) == ["how many members at most, 1 to 9"]
    shown = form.preview(boxes(tmp_path, members=members, most="3"))
    assert shown.tabs == f"Opens 1 tab in {tmp_path.name}" and "adds up to 3 members itself" in shown.head[1][0]
    assert form.help("team-members", fields) == teamforms.LEAD_HELP
    assert form.help("team-name", fields) == teamforms.HELP["team-name"]
    assert form.help("", fields) == teamforms.NO_HELP


def test_peers_refuse_the_background_and_the_advisor(tmp_path):
    form = TeamForm(set())
    peers = "one: first part\ntwo: second part"
    assert form.peers(peers)
    assert form.toggle_background(peers).words.startswith("Peers each run in a tab")
    assert not form.background
    assert form.toggle_background(teamforms.first_lineup()) is None and form.background
    assert "a team of peers has none" in form.advisor_help(boxes(tmp_path, members=peers, advisor="opus"))


def test_a_line_up_kept_and_forgotten(tmp_path):
    form = TeamForm(set())
    members = "lead: steer\napi: the server"
    step = form.keep_step(members)
    assert step.ask and step.members == ["api: the server"] and not step.peers
    assert form.keep("feature team", step.members, step.peers).note.severity == "warning"  # a ready-made name
    kept = form.keep("  my   crew ", step.members, step.peers)
    assert kept.changed and form.picked() == "my crew" and teams.saved_lineups()["my crew"] == "api: the server"
    assert form.lineups[-1] == teams.DECIDE
    first = form.keep_step(members)
    assert first.note.words == "Press Ctrl+L again to forget the line-up my crew." and not first.changed
    gone = form.keep_step(members)
    assert gone.changed and "my crew" not in form.lineups and "my crew" not in teams.saved_lineups()
    assert not form.keep_step(teamforms.LEAD_LINE).ask


def test_the_lead_alone_gets_the_advisor(tmp_path):
    form = TeamForm(set())
    team = form.create(boxes(tmp_path, members="lead: steer @opus\napi: the server", advisor="fable",
                             cap="300k"))
    assert isinstance(team, teams.Team) and team.cap == "300k"
    assert [m.advisor for m in team.members] == ["fable", ""]
    weak = form.create(boxes(tmp_path, members="lead: steer @fable\napi: the server", advisor="opus"))
    assert weak.members[0].advisor == ""  # Opus cannot advise a Fable lead: not given
    assert "cannot advise" in form.advisor_help(boxes(tmp_path, members="lead: steer @fable\napi: x",
                                                      advisor="opus"))
    agy_lead = form.create(boxes(tmp_path, members="lead: steer @agy\napi: the server", advisor="fable"))
    assert agy_lead.members[0].advisor == ""  # --advisor is Claude Code's


def test_a_team_at_work_joined_or_told(tmp_path):
    live = teams.new_team("live", str(tmp_path), "the shop", teams.parse_members("lead: steer\napi: a", lead_ok=True))
    form = TeamForm(set(), [(live, {"lead"})])
    assert form.at_work(str(tmp_path)) == "live (1 open)" and form.at_work("") == ""
    before = teamforms.first_lineup()
    add = teams.Suggestion("sound: effects", 1, False, "it adds", teams.ADD_TO, "live", "live-lead", "Sound joins.")
    done = form.suggested(add, "", boxes(tmp_path, members=before))
    assert done.box == "sound: effects" and form.toward is add and done.note.seconds == 15
    view = form.toward_view(boxes(tmp_path, members="sound: effects"))
    assert view.heading == ("Adds 1 to live", "green") and [m.name for m in view.team.members][-1] == "sound"
    assert form.missing(boxes(tmp_path, members="")) == ["a member to add"]
    assert form.create(boxes(tmp_path, members="sound: effects")) == JoinTeam(
        "live", "sound: effects", "live-lead", "Sound joins.", "Add checkout")
    assert not form.toward_gone(str(tmp_path)) and form.toward_gone(str(tmp_path.parent))
    assert form.leave_toward("sound: effects") == before and form.toward is None
    has = teams.Suggestion(kind=teams.HAS_IT, team="live", to="live-lead", message="Do it.")
    assert form.suggested(has, "", boxes(tmp_path)).note.severity == "warning"
    assert form.toward_view(boxes(tmp_path, members="Do it.")).heading == ("live has this goal", "amber")
    assert form.create(boxes(tmp_path, members="  Do   it\nnow ")) == TellTeam("live", "live-lead", "Do it now")


def test_ctrl_r_asks_only_with_a_goal_and_a_folder(tmp_path):
    form = TeamForm(set())
    assert form.suggest_ask(Fields(goal="g")).refusal.severity == "warning"
    ask = form.suggest_ask(boxes(tmp_path))
    assert ask.refusal is None and ask.goal == "Add checkout" and ask.box is None and "Add checkout" in ask.prompt
    failed = form.suggested(None, "boom", boxes(tmp_path))
    assert failed.note.words == "No line-up suggested: boom" and failed.box is None


def test_ctrl_g_finds_the_member_on_the_line(tmp_path):
    form = TeamForm(set())
    assert form.role_of(boxes(tmp_path), "").words.startswith("Put the cursor on a member's line")
    team, member = form.role_of(boxes(tmp_path), "api: the server side")
    said = teamforms.role_words(team, member)
    assert said.head == f"What {team.session_name(member)} is told" and said.role == teams.role(team, member)
    assert form.role_of(boxes(tmp_path), "nobody: here") is None
