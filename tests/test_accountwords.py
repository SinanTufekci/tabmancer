"""What the dashboard says about the Claude accounts, with no UI (accountwords.py): the A dialog's
options and where each page opens, the sign-in it starts, and the words for a session that last ran on
another organisation. test_accounts and test_usage_accounts show them as the terminal draws them;
test_desktop_extra proves the module loads with neither Textual nor Qt."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from tabmancer import accounts, accountwords, browsers, usage
from tabmancer.accounts import Account
from tabmancer.browsers import Place
from tabmancer.sessions import Session

ME = Account("claude", "me@example.com", "Max 5x", "acc1", "orgP", "", "claude_max")
TEAM = Account("claude", "me@example.com", "Team", "acc1", "orgT", "Example Labs", "claude_team")
OTHER = Account("claude", "b@example.com", "Pro", "acc2", "orgB", "", "claude_pro")
PLACES = [Place("", "default browser"), Place("chrome:Profile 3", "Chrome · Work", ("chrome",)),
          Place("edge:private", "Edge · private window", ("edge",))]


def said(parts) -> str:
    return "".join(words for words, _ in parts)


def test_where_each_page_opens():
    seen = [(ME, ""), (TEAM, ""), (OTHER, "")]
    assert accountwords.shared(seen) == {"me@example.com"}
    email, place = accountwords.opens_in(seen, PLACES, {"me@example.com": "edge:private", "b@example.com": "gone",
                                                         accounts.ANY_OTHER: "chrome:Profile 3"})
    assert email == {accountwords.ANOTHER: accounts.ANY_OTHER, ME.key: ME.email, TEAM.key: TEAM.email,
                     OTHER.key: OTHER.email}
    assert place[ME.key] == "edge:private" and place[accountwords.ANOTHER] == "chrome:Profile 3"
    assert place[OTHER.key] == browsers.guess(OTHER.email, PLACES)  # what it picked is gone
    assert accountwords.opens_in([], PLACES, {accounts.ANY_OTHER: "gone"})[1] == {accountwords.ANOTHER: ""}
    ids = [p.id for p in PLACES]
    assert [accountwords.next_place(ids, "edge:private", 1), accountwords.next_place(ids, "", -1),
            accountwords.next_place(ids, "gone", 1)] == ["", "edge:private", "chrome:Profile 3"]
    assert accountwords.place_words(PLACES, "gone") == "default browser"


def test_an_accounts_option():
    now = accountwords.option_parts(ME, "", current=True, shared=True, last=None, place="Chrome · Work")
    assert said(now) == "● me@example.com  personal  Max 5x  signed in now"  # nowhere to open: signed in
    read = (datetime.now() - timedelta(minutes=45), [usage.Limit("session", 12, None, "4pm")])
    other = accountwords.option_parts(OTHER, "2026-11-02T14:05:00", current=False, shared=False, last=read,
                                      place="Edge · private window")
    assert said(other).startswith("  b@example.com  Pro  last on 02 Nov 14:05\n    session 12%")
    assert said(other).endswith("\n    opens in Edge · private window")
    assert said(accountwords.option_parts(OTHER, "bad", current=False, shared=False, last=None)) == \
        "  b@example.com  Pro  "
    assert said(accountwords.another_parts()) == "  Another account  one not listed here"


def test_the_dialogs_words():
    assert accountwords.title_words(None) == "not signed in"
    assert accountwords.note(True, 0).endswith("only your browsers' profile names.")
    assert accountwords.note(False, 1).endswith("1 session is stopped at a usage limit: once you are signed in "
                                                "with another account, c continues it at once.")
    assert "2 sessions are" in accountwords.note(False, 2) and accountwords.note(False, 2).endswith("them at once.")
    assert [k for k, _ in accountwords.key_pairs(True)] == ["Enter", "←/→", "Esc"]
    assert [k for k, _ in accountwords.key_pairs(False)] == ["Enter", "Esc"]


def test_the_sign_in(monkeypatch):
    monkeypatch.setattr(browsers, "launcher", lambda: Path("C:/tm/open-browser.cmd"))
    login = accountwords.sign_in_launch("b@example.com", "chrome:Profile 3")
    assert login.options == ("auth", "login", "--email", "b@example.com") and login.title == "Sign in: b@example.com"
    assert dict(login.env)["BROWSER"].endswith("open-browser.cmd") and not login.session_id
    assert accountwords.where_words(login, PLACES, "chrome:Profile 3") == "Chrome · Work"
    another = accountwords.sign_in_launch("", "")
    assert another.options == ("auth", "login") and another.env == () and another.title == "Sign in"
    assert accountwords.where_words(another, PLACES, "") == "your browser"
    assert accountwords.signing_in_note("", "your browser", []).words.startswith("Signing in in a new tab")
    assert accountwords.signing_in_note("a@b", "x", ["claude auth login"]).seconds == 15
    assert accountwords.open_failed_note(OSError("no wt")).severity == "error"


def test_a_session_that_ran_elsewhere():
    def s(sid, org, sensitive=()):
        return Session(session_id=sid, path=Path(f"C:/nowhere/{sid}.jsonl"), cwd="C:/work/shop", title="t",
                       org=org, sensitive=list(sensitive))
    here, there, unknown = s("here", "orgP"), s("there", "orgB", ["Gmail"]), s("unknown", "")
    assert accountwords.ran_elsewhere(ME, [here, there, unknown]) == [there]
    assert accountwords.ran_elsewhere(Account("claude", "old@example.com"), [there]) == []
    assert accountwords.ran_elsewhere(None, [there]) == []
    assert [accountwords.goes_to(a) for a in (ME, TEAM, None, Account("claude", "x", org_type="claude_team"))] == [
        "your personal account", "the Example Labs organisation", "this account", "this organisation"]
    assert accountwords.elsewhere_note(here, "a digest of it", ME) is None
    note = accountwords.elsewhere_note(there, "a digest of it", ME)
    assert note.words == ("shop ran on another organisation: a digest of it goes to your personal account "
                          "(Gmail content in it).") and note.severity == "warning"


def test_the_other_accounts(monkeypatch):
    read = (datetime.now() - timedelta(minutes=45), [usage.Limit("session", 12, None, "4pm")])
    monkeypatch.setattr(accounts, "known", lambda: [(ME, ""), (OTHER, "")])
    monkeypatch.setattr(accounts, "shared_emails", set)
    monkeypatch.setattr(usage, "last_by_account", lambda: {OTHER.key: read, ME.key: read})
    assert accountwords.other_accounts_words(ME.key) == ["b@example.com · Pro: session 12% · read 45m ago"]
    assert len(accountwords.other_accounts_words(None)) == 2
    toast, line = accountwords.other_accounts_note(ME, ME.key, None)
    assert toast.startswith("me@example.com: limit reached · b@example.com has room") and "A to switch" in line
