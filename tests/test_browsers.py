"""Where A's sign-in page opens: the browsers' profiles, the guess for an account, the launcher Claude
Code is given in BROWSER, and the address opened at the place picked, never anything else."""

from __future__ import annotations

import json
import os
import sys

import pytest

from tabmancer import browsers
from tabmancer.browsers import DEFAULT, Place
from tabmancer.browsers import places as real_places  # conftest puts an empty list in its place


def local_state(folder, cache, order=None):
    folder.mkdir(parents=True, exist_ok=True)
    profile = {"info_cache": cache, **({"profiles_order": order} if order is not None else {})}
    (folder / "Local State").write_text(json.dumps({"profile": profile, "browser": {"x": 1}}), encoding="utf-8")


def test_the_profiles_are_read_in_the_browsers_own_order_with_their_google_accounts(tmp_path):
    local_state(tmp_path, {
        "Default": {"name": "Personal", "user_name": "me@gmail.com", "gaia_id": "123"},
        "Profile 3": {"name": "Work", "user_name": "me@work.example.com"},
        "Profile 1": {"name": "Shopping", "user_name": ""},
        "Bad\" --flag": {"name": "odd"},  # a folder name that could carry a flag is never passed on
    }, order=["Profile 3", "Default"])
    assert browsers.profiles(tmp_path) == [
        ("Profile 3", "Work", "me@work.example.com"), ("Default", "Personal", "me@gmail.com"),
        ("Profile 1", "Shopping", "")]
    local_state(tmp_path, {"Profile 2": {"name": "B"}, "Default": {"name": "A"}})  # no order: Default first
    assert [name for name, _, _ in browsers.profiles(tmp_path)] == ["Default", "Profile 2"]
    (tmp_path / "Local State").write_text("{not json", encoding="utf-8")
    assert browsers.profiles(tmp_path) == []
    assert browsers.profiles(tmp_path / "missing") == []


def test_the_places_are_the_systems_way_then_each_profile_then_a_private_window(tmp_path, monkeypatch):
    monkeypatch.setattr(browsers, "places", real_places)
    local_state(tmp_path / "chrome", {"Default": {"name": "Personal", "user_name": "me@gmail.com"},
                                      "Profile 3": {"name": "Work", "user_name": ""}})
    programs = {"chrome": "/bin/chrome", "edge": "/bin/edge"}
    monkeypatch.setattr(browsers, "program", lambda kind: programs.get(kind.key))
    monkeypatch.setattr(browsers, "data_folder", lambda kind: tmp_path / kind.key)  # Edge's lists nothing
    found = browsers.places()
    assert [(p.id, p.words) for p in found] == [
        (DEFAULT, "default browser"),
        ("chrome:Default", "Chrome · Personal"), ("chrome:Profile 3", "Chrome · Work"),
        ("chrome:private", "Chrome · private window"),
        ("edge", "Edge"), ("edge:private", "Edge · private window")]
    assert found[1].argv == ("/bin/chrome", "--profile-directory=Default") and found[1].email == "me@gmail.com"
    assert found[3].argv == ("/bin/chrome", "--incognito") and found[5].argv == ("/bin/edge", "--inprivate")
    assert found[0].argv == ()  # nothing to run: the system opens it as it opens links


def test_an_account_opens_in_the_profile_that_syncs_with_the_same_google_account():
    among = [Place(DEFAULT, "default browser"), Place("chrome:Default", "Chrome · Me", ("c",), "Me@Gmail.com"),
             Place("chrome:private", "Chrome · private window", ("c", "--incognito"))]
    assert browsers.guess("me@gmail.com", among) == "chrome:Default"  # whatever the case
    assert browsers.guess("me@work.example.com", among) == DEFAULT  # no profile says: as links open
    assert browsers.guess("", among) == DEFAULT


def test_the_sign_in_tab_gets_the_launcher_the_place_and_the_python(monkeypatch):
    from tabmancer.config import config_dir

    assert browsers.login_environment(DEFAULT) == ()  # as before: Claude Code opens it its own way
    env = dict(browsers.login_environment("chrome:Profile 3"))
    assert env[browsers.PLACE] == "chrome:Profile 3" and env[browsers.PYTHON] == sys.executable
    launcher = config_dir() / ("open-in.cmd" if sys.platform == "win32" else "open-in.sh")
    assert env["BROWSER"] == str(launcher)
    text = launcher.read_bytes()  # only ASCII: the Python's path comes from the environment
    # -P where Python has it (3.11): not the folder it runs in for modules. Never -I, which drops your
    # own site-packages, where `pip install --user` puts tabmancer.
    safe = b"-P " if sys.version_info >= (3, 11) else b""
    if sys.platform == "win32":
        assert text == b'@echo off\r\n"%TABMANCER_PYTHON%" ' + safe + b"-m tabmancer.browsers %*\r\n"
    else:
        assert text == b'#!/bin/sh\nexec "$TABMANCER_PYTHON" ' + safe + b'-m tabmancer.browsers "$1"\n'
        assert os.access(launcher, os.X_OK)
    written = launcher.stat().st_mtime_ns
    long_ago = 1_000_000_000  # a second after 1970: whole steps of every file system's clock
    os.utime(launcher, ns=(long_ago, long_ago))
    browsers.login_environment("edge:private")
    assert launcher.stat().st_mtime_ns == long_ago != written  # the same words: not written again


def test_no_launcher_when_tabmancers_folder_cannot_be_written(monkeypatch):
    def refuse():
        raise OSError("read-only")

    monkeypatch.setattr(browsers, "launcher", refuse)
    assert browsers.login_environment("chrome:Default") == ()  # the page still opens, as it used to


@pytest.mark.skipif(sys.platform == "win32", reason="runs the sh launcher")
def test_the_launcher_hands_the_address_to_the_module_whole(tmp_path):
    import subprocess

    from tabmancer.config import config_dir

    browsers.login_environment("chrome:Default")
    stand_in = tmp_path / "python"  # in place of the Python: says what it was asked to run
    stand_in.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="ascii")
    stand_in.chmod(0o755)
    address = "https://claude.com/cai/oauth/authorize?code=true&scope=a+b&login_hint=me%40x.com&s=$(x)`y`"
    result = subprocess.run([str(config_dir() / "open-in.sh"), address], capture_output=True, text=True,
                            env=dict(os.environ, TABMANCER_PYTHON=str(stand_in)), check=True)
    safe = ["-P"] if sys.version_info >= (3, 11) else []
    assert result.stdout.splitlines() == [*safe, "-m", "tabmancer.browsers", address]


def test_the_address_opens_at_the_place_picked(monkeypatch):
    started, opened = [], []
    monkeypatch.setattr(browsers, "places", lambda: [
        Place(DEFAULT, "default browser"), Place("chrome:Profile 3", "Chrome · Work", ("chrome", "--profile-directory=Profile 3"))])
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda argv, **kw: started.append((argv, kw)))
    monkeypatch.setattr(browsers, "system_open", lambda address: opened.append(address) or True)
    address = "https://claude.com/cai/oauth/authorize?code=true&login_hint=me%40work.example.com"
    assert browsers.open_address(address, "chrome:Profile 3")
    (argv, kw), = started
    assert argv == ["chrome", "--profile-directory=Profile 3", address] and not opened
    assert kw["stdout"] == browsers.subprocess.DEVNULL  # the browser's chatter stays out of Claude Code's screen
    assert browsers.open_address(address, "chrome:Profile 9")  # a profile gone since: the system's own way
    assert browsers.open_address(address, DEFAULT)  # or none came with the tab
    assert opened == [address, address] and len(started) == 1


def test_only_web_addresses_are_opened(monkeypatch):
    started, opened = [], []
    monkeypatch.setattr(browsers, "places", lambda: [Place("chrome:Default", "Chrome · Me", ("chrome",))])
    monkeypatch.setattr(browsers.subprocess, "Popen", lambda argv, **kw: started.append(argv))
    monkeypatch.setattr(browsers, "system_open", lambda address: opened.append(address) or True)
    monkeypatch.delenv(browsers.OPENING, raising=False)
    for address in ("C:\\Windows\\System32\\calc.exe", "file:///etc/passwd", "javascript:alert(1)", "--incognito"):
        assert not browsers.open_address(address, "chrome:Default")
    assert started == [] and opened == []
    monkeypatch.setenv(browsers.PLACE, "chrome:Default")
    assert browsers.main(["https://claude.com/x"]) == 0 and started == [["chrome", "https://claude.com/x"]]
    monkeypatch.delenv(browsers.OPENING)
    assert browsers.main([]) == 2
    monkeypatch.delenv(browsers.OPENING)
    assert browsers.main(["a", "b"]) == 2


def test_the_launcher_never_runs_itself_again(monkeypatch):
    """In the sign-in tab BROWSER is the launcher. A fallback that asked BROWSER (Python's
    webbrowser.open did, xdg-open does) ran the launcher, which fell back again: thousands of
    processes in a minute, until they were stopped by hand."""
    seen = []
    monkeypatch.setenv("BROWSER", "C:/tabmancer/open-in.cmd")
    monkeypatch.delenv(browsers.OPENING, raising=False)
    monkeypatch.setattr(browsers, "open_address",
                        lambda address, place: seen.append((os.environ.get("BROWSER"), os.environ.get(browsers.OPENING))))
    browsers.main(["https://claude.com/x"])
    assert seen == [(None, "1")]  # nothing it starts sees the launcher, and all of it knows where it came from
    assert browsers.main(["https://claude.com/x"]) == 1 and len(seen) == 1  # run inside itself: it stops
    assert not hasattr(browsers, "webbrowser")  # its fallback is the system's, which never asks BROWSER


def test_the_launcher_leaves_your_own_site_packages_on():
    """No flag that turns off the user site (-I, -s): with pip install --user, tabmancer is there, and
    under -I the launcher could not import it, so no page opened at all."""
    from tabmancer.config import config_dir

    browsers.login_environment("chrome:Default")
    launcher = (config_dir() / ("open-in.cmd" if sys.platform == "win32" else "open-in.sh")).read_text("ascii")
    flags = launcher.split("-m tabmancer.browsers")[0].split()[-1:]
    assert not {"-I", "-s", "-E"} & set(flags) and "-I" not in launcher and " -s " not in launcher


def test_the_module_run_inside_itself_stops_at_once(tmp_path):
    import subprocess

    env = dict(os.environ, **{browsers.OPENING: "1", browsers.PLACE: "chrome:Default",
                              "BROWSER": str(tmp_path / "never-run")})
    result = subprocess.run([sys.executable, "-I", "-m", "tabmancer.browsers", "https://claude.com/x"],
                            env=env, capture_output=True, timeout=60, check=False)
    assert result.returncode == 1 and result.stdout == b""
