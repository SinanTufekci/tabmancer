"""Who the coding CLIs are signed in as, read from their own files, and shown in the top bar."""

import base64
import json
import os

from textual.widgets import Static

from tabmancer import accounts, clis
from tabmancer.accounts import Account
from tabmancer.app import SessionPicker


def claude_signed_in(folder, email="you@example.com", kind="claude_max", tier="default_claude_max_20x"):
    (folder / ".claude.json").write_text(json.dumps({"numStartups": 9, "oauthAccount": {
        "emailAddress": email, "organizationType": kind, "organizationRateLimitTier": tier}}), encoding="utf-8")


def token(claims):
    middle = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{middle}.signature"


def test_claude_codes_account_and_plan_come_from_its_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert accounts.claude() is None  # nothing signed in (and the home folder's is never read instead)
    claude_signed_in(tmp_path)
    assert accounts.claude() == Account("claude", "you@example.com", "Max 20x")
    claude_signed_in(tmp_path, "other@example.com", "claude_pro", "")  # /login with another account
    assert accounts.claude() == Account("claude", "other@example.com", "Pro")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert accounts.claude_file() == accounts.Path.home() / ".claude.json"


def test_codexs_account_comes_from_its_sign_in(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert accounts.codex_account() is None
    auth = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {"id_token": token(
        {"email": "you@example.com", "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"}})}}
    (tmp_path / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    assert accounts.codex_account() == Account("codex", "you@example.com", "plus")
    (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "sk-x"}),
                                        encoding="utf-8")
    assert accounts.codex_account() == Account("codex", "API key")
    (tmp_path / "auth.json").write_text("{not json", encoding="utf-8")
    assert accounts.codex_account() is None


def test_codex_is_looked_at_only_when_its_sessions_are_listed(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    claude_signed_in(tmp_path)
    monkeypatch.setattr(accounts, "codex_account", lambda: Account("codex", "c@example.com", "free"))
    monkeypatch.setattr(clis, "enabled", lambda: ["claude"])
    assert [a.cli for a in accounts.signed_in()] == ["claude"]
    monkeypatch.setattr(clis, "enabled", lambda: ["claude", "codex"])
    assert [a.cli for a in accounts.signed_in()] == ["claude", "codex"]


def test_an_account_in_little_room_loses_its_plan_then_is_cut_in_the_middle():
    account = Account("claude", "ada.lovelace.21@example.com", "Max 5x")
    assert accounts.words(account, 60) == "ada.lovelace.21@example.com · Max 5x"
    assert accounts.words(account, 28) == "ada.lovelace.21@example.com"  # the plan goes before the email is cut
    assert accounts.words(account, 18) == "ada.l…@example.com"
    assert accounts.words(account, 10) == "ada.lovel…"  # not even the domain: the name, cut
    assert accounts.words(account, 7) == ""
    assert accounts.words(Account("claude", "you@example.com", "Max 5x"), 16) == "you@example.com"  # whole, no "…"
    assert all(len(accounts.words(account, room)) <= room for room in range(60))


async def test_the_top_bar_shows_the_accounts_beside_the_plan_limits(fake_home, monkeypatch):
    claude_signed_in(fake_home, "you@example.com", "claude_max", "default_claude_max_5x")
    app = SessionPicker(days=14)
    async with app.run_test(size=(200, 40)) as pilot:
        await pilot.pause(0.3)
        account = app.topbar.query_one("#account", Static)
        assert str(account.render()) == "you@example.com · Max 5x"
        parts = [child.id for child in app.topbar.children]
        assert parts.index("account") == parts.index("usage") - 1  # right beside the limits
        claude_signed_in(fake_home, "other@example.com", "claude_pro", "")  # /login elsewhere
        os.utime(fake_home / ".claude.json", (1, 2))  # a different time, however fast the test
        app.show_accounts()  # as the dashboard does every few seconds
        await pilot.pause(0.1)
        assert str(account.render()) == "other@example.com · Pro"
        await pilot.resize_terminal(60, 40)  # little room left: it gives way first
        await pilot.pause(0.2)
        assert "Pro" not in str(account.render())


def test_the_demo_is_signed_in_as_no_one_real(tmp_path, monkeypatch):
    from tabmancer import demo

    monkeypatch.setenv("TABMANCER_CONFIG_DIR", str(tmp_path / "config"))
    demo.prepare(tmp_path)
    assert accounts.claude() == Account("claude", "you@example.com", "Max 5x")
    assert accounts.codex_account() is None


def test_doctor_says_who_claude_code_is_signed_in_as(tmp_path, monkeypatch):
    from tabmancer import doctor

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "claude" if name == "claude" else None)
    monkeypatch.setattr(doctor, "_claude_version", lambda claude: "2.1.0")
    monkeypatch.setattr(doctor, "missing_from_cli", lambda claude: None)
    monkeypatch.setattr(doctor, "_agents_json", list)
    said = [message for _, message in doctor.checks()]
    assert any(message.startswith("No account in") for message in said)
    claude_signed_in(tmp_path)
    said = [message for _, message in doctor.checks()]
    assert "Signed in as you@example.com (Max 20x)" in said


def test_the_accounts_you_use_are_remembered_the_latest_first_and_nothing_but_who():
    from tabmancer.config import config_dir

    accounts.remember(Account("claude", "a@example.com", "Max 5x"), "2026-11-02T09:00:00")
    accounts.remember(Account("claude", "b@example.com", "Team"), "2026-11-02T10:00:00")
    accounts.remember(Account("claude", "a@example.com", "Max 5x"), "2026-11-02T11:00:00")
    assert [(a.email, a.plan, when) for a, when in accounts.known()] == [
        ("a@example.com", "Max 5x", "2026-11-02T11:00:00"), ("b@example.com", "Team", "2026-11-02T10:00:00")]
    accounts.remember(Account("claude", "a@example.com", "Max 5x"), "2026-11-02T12:00:00")
    assert accounts.known()[0][1] == "2026-11-02T11:00:00"  # as it was: not written again every few seconds
    accounts.remember(Account("codex", "c@example.com", "plus"), "2026-11-02T12:00:00")  # Claude Code's only
    accounts.remember(Account("claude", "API key"), "2026-11-02T12:00:00")
    assert len(accounts.known()) == 2
    saved = json.loads((config_dir() / accounts.KNOWN).read_text(encoding="utf-8"))
    assert set(saved) == {"claude"} and all(set(entry) == {"email", "plan", "seen"} for entry in saved["claude"])


async def test_signed_in_with_another_account_the_sessions_a_limit_stopped_can_go_on(fake_home, monkeypatch):
    from test_limits import stopped_at, write

    from tabmancer import viewmodel

    claude_signed_in(fake_home, "a@example.com")
    write(fake_home, "lim", stopped_at("You've hit your session limit · resets 11:59pm"))
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        for _ in range(60):
            await pilot.pause(0.05)
            if "lim" in app.sessions and accounts.limited_on():
                break
        assert app.signed_as == "a@example.com" and accounts.limited_on() == {"lim": "a@example.com"}
        assert not app.sessions["lim"].signed_in_again  # stopped on the account signed in now: it waits
        claude_signed_in(fake_home, "b@example.com")  # A, or claude auth login, or /login anywhere
        os.utime(fake_home / ".claude.json", (1, 2))
        app.show_accounts()
        await pilot.pause(0.1)
        assert app.sessions["lim"].signed_in_again and accounts.limited_on() == {"lim": "a@example.com"}
        assert viewmodel.limit_words(app.sessions["lim"])[0] == "◐ signed in again"
        assert any("can go on" in saying.text for saying in app.mascot.bubbles)


async def test_a_signs_in_with_another_account_through_claude_codes_own_sign_in(fake_home, monkeypatch):
    from textual.widgets import OptionList

    from tabmancer import app as app_module
    from tabmancer.accountsview import AccountsDialog

    claude_signed_in(fake_home, "a@example.com")
    accounts.remember(Account("claude", "b@example.com", "Team"), "2026-11-01T10:00:00")
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])
    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("A")
        for _ in range(40):
            await pilot.pause(0.05)
            if isinstance(app.screen, AccountsDialog):
                break
        options = app.screen.query_one(OptionList)
        ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
        assert ids == ["a@example.com", "b@example.com", "+"] and options.get_option_at_index(0).disabled
        assert options.highlighted == 1  # the first one to switch to
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.05)
            if opened:
                break
        assert opened[0].argv == ["claude", "auth", "login", "--email", "b@example.com"]  # its own sign-in
        assert opened[0].env == ()  # no browsers found (as in every test): the page opens as it always did


async def test_a_opens_each_accounts_sign_in_page_where_you_picked_for_it(fake_home, monkeypatch):
    from textual.widgets import OptionList

    from tabmancer import app as app_module
    from tabmancer import browsers
    from tabmancer.accountsview import AccountsDialog
    from tabmancer.browsers import Place

    claude_signed_in(fake_home, "a@example.com")
    accounts.remember(Account("claude", "c@example.com", "Max 5x"), "2026-11-01T09:00:00")
    accounts.remember(Account("claude", "b@example.com", "Team"), "2026-11-01T10:00:00")
    places = [Place(browsers.DEFAULT, "default browser"),
              Place("chrome:Default", "Chrome · Personal", ("chrome", "--profile-directory=Default"), "C@example.com"),
              Place("chrome:Profile 3", "Chrome · Work", ("chrome", "--profile-directory=Profile 3")),
              Place("chrome:private", "Chrome · private window", ("chrome", "--incognito"))]
    monkeypatch.setattr(browsers, "places", lambda: list(places))
    opened = []
    monkeypatch.setattr(app_module, "open_tabs", lambda launches: opened.extend(launches) or [])

    async def open_dialog(pilot):
        await pilot.press("A")
        for _ in range(40):
            await pilot.pause(0.05)
            if isinstance(app.screen, AccountsDialog):
                break
        options = app.screen.query_one(OptionList)
        return options, {options.get_option_at_index(i).id: str(options.get_option_at_index(i).prompt)
                         for i in range(options.option_count)}

    app = SessionPicker(days=14)
    async with app.run_test(size=(150, 44)) as pilot:
        await pilot.pause(0.3)
        options, said = await open_dialog(pilot)
        assert "opens in" not in said["a@example.com"]  # signed in now: nothing to open
        assert said["b@example.com"].endswith("opens in default browser")  # no profile says it is b's
        assert said["c@example.com"].endswith("opens in Chrome · Personal")  # its Google account: a guess
        assert said["+"].endswith("opens in default browser")
        assert "←/→  where it opens" in str(app.screen.query(".hint").first().render())
        assert options.highlighted == 1  # b
        await pilot.press("right", "right")  # Chrome · Personal, then Chrome · Work
        await pilot.pause(0.1)
        assert str(options.get_option_at_index(1).prompt).endswith("opens in Chrome · Work")
        assert accounts.opens_in() == {"b@example.com": "chrome:Profile 3"}  # kept at once, Esc or not
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.05)
            if opened:
                break
        login = opened[0]
        assert login.argv == ["claude", "auth", "login", "--email", "b@example.com"]  # still its own sign-in
        env = dict(login.env)
        assert env["TABMANCER_OPEN_IN"] == "chrome:Profile 3" and env["BROWSER"].endswith(("open-in.cmd", "open-in.sh"))
        await pilot.pause(0.1)
        _, said = await open_dialog(pilot)  # the next time: as picked
        assert said["b@example.com"].endswith("opens in Chrome · Work")
        await pilot.press("left", "left")
        await pilot.pause(0.1)
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause(0.05)
            if len(opened) == 2:
                break
        assert opened[1].env == ()  # back to the browser as links open: nothing set
        assert accounts.opens_in() == {"b@example.com": browsers.DEFAULT}


def test_where_each_account_opens_outlives_the_list_of_accounts_being_written_again():
    accounts.keep_opens_in("a@example.com", "edge:private")
    accounts.keep_opens_in(accounts.ANY_OTHER, "chrome:private")
    accounts.remember(Account("claude", "a@example.com", "Pro"), "2026-11-05T10:00:00")
    accounts.keep_limited({"s1": "a@example.com"})
    assert accounts.opens_in() == {"a@example.com": "edge:private", "*": "chrome:private"}
