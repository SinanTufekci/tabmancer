"""A team's messages with no UI (viewmodel): which fly since the last look (new_flights) and the
sequence diagram's lanes (lanes_of). The team view (test_team_tree, test_monitor) and the desktop's
orbit draw them."""

from __future__ import annotations

from datetime import datetime

from tabmancer import teams, teamtree, teamview
from tabmancer import viewmodel as VM


def msg(sender, receiver, text):
    return teams.Message(datetime(2026, 11, 25, 9, 0), sender, receiver, text)


MEMBERS = {"lead", "api", "ui"}


def test_the_first_look_flies_nothing():
    said = [msg("lead", "api", "build"), msg("you", "lead", "go")]
    seen, flights = VM.new_flights(None, said, MEMBERS)
    assert flights == [] and seen == {("lead", "api", "build"), ("you", "lead", "go")}


def test_new_messages_between_members_fly_and_yours_on_asking():
    first = [msg("lead", "api", "build")]
    seen, _ = VM.new_flights(None, first, MEMBERS)
    later = [*first, msg("api", "lead", "done"), msg("you", "api", "faster"), msg("tabmancer", "ui", "start"),
             msg("lead", "outsider", "x")]
    now, flights = VM.new_flights(seen, later, MEMBERS)
    assert [m.text for m in flights] == ["done"]
    assert now == {(m.sender, m.receiver, m.text) for m in later}
    assert seen == {("lead", "api", "build")}  # the seen given is not changed
    _, mine = VM.new_flights(seen, later, MEMBERS, you=True)
    assert [m.text for m in mine] == ["done", "faster"]
    assert VM.new_flights(now, later, MEMBERS, you=True)[1] == []  # seen already: nothing flies again


def test_the_lanes_you_first_then_the_team_then_anyone_else():
    team = teams.new_team("shop", "C:/work/shop", "g", teams.parse_members("lead: steer\napi: a", lead_ok=True))
    assert VM.lanes_of(team, []) == ["lead", "api"]
    assert VM.lanes_of(team, [msg("tabmancer", "api", "x"), msg("stranger", "lead", "y")]) == [
        teams.YOU, "lead", "api", VM.OTHER]


def test_one_copy_named_where_it_was():
    assert teamtree.lanes_of is VM.lanes_of and teamtree.OTHER == VM.OTHER
    assert teamtree.FLIGHT_SECONDS == teamview.FLIGHT_SECONDS == VM.FLIGHT_SECONDS
