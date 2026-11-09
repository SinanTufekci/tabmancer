import json
import sys
from datetime import datetime, timezone

from tabmancer import doctor, sessions


def write(home, name, records):
    project = home / "projects" / "-work-demo"
    project.mkdir(parents=True, exist_ok=True)
    (project / f"{name}.jsonl").write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")


def good(prompt, usage=True):
    reply = {"type": "assistant", "timestamp": datetime.now(timezone.utc).isoformat(),
             "message": {"id": f"msg_{prompt}", "content": [{"type": "text", "text": "ok"}],
                         "stop_reason": "end_turn"}}
    if usage:
        reply["message"]["usage"] = {"input_tokens": 3, "output_tokens": 40}
    return [{"type": "user", "cwd": "/work/demo", "entrypoint": "cli", "message": {"content": prompt}},
            reply, {"type": "ai-title", "aiTitle": prompt}, {"type": "cost-state"}]


def levels(results):
    return [level for level, _ in results]


def test_healthy_transcripts_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):
        write(tmp_path, f"s{i}", good(f"task {i}"))
    results = doctor._transcript_checks(set())
    assert "fail" not in levels(results) and "warn" not in levels(results)


def test_unreadable_endings_point_at_a_format_change(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):  # a prompt, then only record types tabmancer does not know
        write(tmp_path, f"s{i}", [good(f"task {i}")[0], {"type": "brand-new-record"}])
    results = doctor._transcript_checks(set())
    assert "fail" in levels(results)
    assert any("format may have changed" in message for _, message in results)


def test_missing_exit_records_warn_but_open_sessions_do_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):
        write(tmp_path, f"s{i}", good(f"task {i}")[:3])  # no cost-state: looks killed
    assert "warn" in levels(doctor._transcript_checks(set()))
    assert "warn" not in levels(doctor._transcript_checks({f"s{i}" for i in range(6)}))


def test_replies_without_token_counts_warn(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):
        write(tmp_path, f"s{i}", good(f"task {i}", usage=False))
    results = doctor._transcript_checks(set())
    assert any(level == "warn" and "token" in message for level, message in results)


HELP = """Usage: claude [options] [command] [prompt]

Options:
  -r, --resume [value]            Resume a conversation by session ID
  --fork-session                  When resuming, create a new session ID
  -c, --continue                  Continue the most recent conversation
  --bg, --background              Start the session in the background
  -p, --print                     Print response and exit; works with --settings and --bg
  --model <model>                 Model for the current session
  --effort <level>                Effort level for the current session
  --session-id <uuid>             Use a specific session ID for the conversation
  --tools <tools...>              Specify the list of available tools
  --no-session-persistence        Disable session persistence
  --autocompact <auto|tokens>     Auto-compact window size (auto, or 100k–1M tokens)
  --strict-mcp-config             Only use MCP servers from --mcp-config
  -n, --name <name>               Set a display name for this session
  --append-system-prompt <prompt> Append a system prompt
  --settings <file-or-json>       Path to a settings JSON file or a JSON string
  --allowedTools, --allowed-tools <tools...>  Tools to allow

Commands:
  agents [options]                Manage background agents
  attach <id|name>                Open a background session in this terminal
  stop|kill <id>                  Stop a background session
  rm <id>                         Delete a background session
"""
AGENTS_HELP = """Usage: claude agents [options]

Options:
  --all                           With --json: also include completed background sessions
  --json                          Print active sessions
"""


def test_a_flag_or_command_gone_from_claude_is_named_with_what_it_is_for(monkeypatch):
    helps = {"": HELP, "agents": AGENTS_HELP}
    monkeypatch.setattr(doctor, "_help", lambda claude, command: helps[command])
    assert doctor.missing_from_cli("claude") == []
    # Still named in -p's words, but no longer a flag of its own: gone.
    helps[""] = HELP.replace("  --bg, --background              Start the session in the background\n", "")
    helps[""] = helps[""].replace("  stop|kill <id>                  Stop a background session\n", "")
    helps["agents"] = AGENTS_HELP.replace("  --json                          Print active sessions\n", "")
    assert doctor.missing_from_cli("claude") == [
        "claude --bg (background sessions)", "claude stop (background sessions)",
        "claude agents --json (which sessions are open)"]


def test_a_help_that_cannot_be_read_says_nothing(monkeypatch):
    monkeypatch.setattr(doctor, "_help", lambda claude, command: None)
    assert doctor.missing_from_cli("claude") is None


def test_a_newer_claude_code_than_last_checked_gets_a_word():
    major, minor, patch = (int(part) for part in doctor.TESTED_WITH.split("."))
    assert doctor.version_check(f"{major}.{minor}.{patch + 40} (Claude Code)")[0] == "ok"
    assert doctor.version_check(f"{major}.{minor + 1}.0 (Claude Code)")[0] == "warn"
    assert doctor.version_check(f"{major + 1}.0.0 (Claude Code)")[0] == "warn"
    assert doctor.version_check("claude")[0] == "ok"  # no version to read: just the path


def test_shutdown_records_are_checked_on_every_platform(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)  # no claude, no terminal
    monkeypatch.setattr(doctor, "recent_shutdowns", list)
    results = doctor.checks()
    told = [message for level, message in results if level == "warn" and "No shutdown records in" in message]
    assert told and doctor.SHUTDOWN_RECORDS.get(sys.platform, doctor.SHUTDOWN_RECORDS["linux"]) in told[0]
    assert any("WezTerm" in message for _, message in results)  # every terminal it can open tabs in


def test_run_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "checks", lambda: [("ok", "fine"), ("fail", "broken")])
    assert doctor.run() == 1
    assert "✗ broken" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "checks", lambda: [("ok", "fine")])
    assert doctor.run() == 0
    assert sessions  # imported for the conftest stub


class Tty:
    def isatty(self):
        return True


def test_doctor_says_how_bit_is_drawn(monkeypatch):
    from tabmancer import sixel
    from tabmancer.config import write_json

    assert "TABMANCER_SIXEL=0" in doctor.bit_check()[1]  # as the tests run
    monkeypatch.delenv("TABMANCER_SIXEL")
    assert "not run in a terminal" in doctor.bit_check()[1]
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(sixel, "probe", lambda: None)
    assert "does not say it draws Sixel" in doctor.bit_check()[1]
    monkeypatch.setattr(sixel, "probe", lambda: sixel.Terminal((9, 19), sync=True))
    assert doctor.bit_check() == ("ok", "Bit is drawn in real pixels (Sixel, 9x19 pixel cells, synchronized output).")
    write_json("config.json", {"mascot": "blocks"})
    assert "as chosen with b" in doctor.bit_check()[1]
    write_json("config.json", {"mascot": False})  # hidden, from before b chose among three
    assert "away" in doctor.bit_check()[1]
