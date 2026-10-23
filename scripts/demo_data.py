"""The made-up sessions the README's pictures show: build_home() writes them into a folder that
stands in for ~/.claude, build_team() adds a team at work. scripts/window_shots.py and
scripts/demo_window.py use them; your real sessions are never read, so nothing private can end up
in docs/.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

NOW = time.time()
SHUTDOWN = datetime.now() - timedelta(hours=9)
ROOT = "C:\\dev" if sys.platform == "win32" else "/home/dev/code"


def clock(moment):
    """'4pm': the hour a usage limit lifts, as Claude Code words it."""
    hour = datetime.fromtimestamp(moment).hour
    return f"{hour % 12 or 12}{'pm' if hour >= 12 else 'am'}"


def user(text):
    return {"type": "user", "entrypoint": "cli", "message": {"content": text}}


def reply(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}],
                                             "stop_reason": "end_turn"}}


def edit(path):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "Edit", "input": {"file_path": path}}],
        "stop_reason": "tool_use"}}


def tool_result():
    return user([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}])


def cost(added, removed):
    return {"type": "cost-state", "totalLinesAdded": added, "totalLinesRemoved": removed}


MODELS = {"nightly": "claude-haiku-5-5", "ml": "claude-sonnet-5-5"}  # the rest: Opus


TOOLS = ["Bash"] * 6 + ["Read"] * 4 + ["Edit"] * 3 + ["Grep"] * 2 + ["Write", "WebSearch", "Glob"]
FILES = ["src/app.py", "src/routes.ts", "tests/test_app.py", "README.md", "src/config.py", "pyproject.toml"]


def turns(sid: str, project: str, ended: float) -> list[dict]:
    """Made-up replies with usage counts and tool calls, over the few days before the session ended."""
    rng = random.Random(sid)
    out = []
    for back in range(rng.randint(1, 4)):
        start = ended - back * 86400 - rng.randint(0, 3 * 3600)
        context = rng.randint(20_000, 40_000)
        for turn in range(rng.randint(10, 40)):
            context += rng.randint(1_000, 6_000)
            stamp = datetime.fromtimestamp(start - (40 - turn) * 90, tz=timezone.utc)
            content = []
            if rng.random() < 0.7:
                tool = rng.choice(TOOLS + (["mcp__kicad__run_drc"] * 6 if project == "pcb-router" else []))
                content = [{"type": "tool_use", "id": "t", "name": tool,
                            "input": {"file_path": f"ROOT/{project}/{rng.choice(FILES)}"}}]
            out.append({"type": "assistant", "timestamp": stamp.isoformat().replace("+00:00", "Z"),
                        "message": {"id": f"msg_{sid}_{back}_{turn}", "model": MODELS.get(sid, "claude-opus-5-5"),
                                    "content": content, "usage": {
                                        "input_tokens": 2, "cache_creation_input_tokens": rng.randint(500, 6_000),
                                        "cache_read_input_tokens": context,
                                        "output_tokens": rng.randint(150, 3_000)}}})
    return out


# (session id, project, title, records after the title, ended at, branch)
SESSIONS = [
    ("cli-tool", "cli-tool", "Add --json output to the list command",
     [user("add a --json flag to `list`"), reply("Added it, with tests.")], NOW - 60, "json-output"),
    ("api-server", "api-server", "Rate limiting for the public API",
     [user("add per-key rate limiting"), reply("Done: a token bucket per API key, 60 requests/minute.")],
     NOW - 240, "main"),
    ("nightly", "nightly-reports", "Summarise yesterday's error logs",
     [user("summarise the error logs every night"), reply("Scheduled; first run tonight.")], NOW - 900, "main"),
    ("pcb-router", "pcb-router", "Route the power plane on the 4-layer board",
     [user("route the 3V3 plane and stitch the ground vias"), edit("ROOT/pcb-router/board.kicad_pcb"),
      tool_result(), edit("ROOT/pcb-router/rules.dru"), tool_result(), cost(412, 38)],
     SHUTDOWN.timestamp() - 30, "power-plane"),
    ("web-app", "web-app", "Fix the login redirect loop",
     [user("users bounce between /login and /home"),
      edit("ROOT/web-app/src/auth/session.ts"), tool_result(),
      reply("Fixed: the session cookie was set on the wrong path. Added a regression test."), cost(57, 12)],
     SHUTDOWN.timestamp() - 12, "fix-login-loop"),
    ("data", "data-pipeline", "Speed up the nightly CSV import",
     [user("the import takes 40 minutes"), reply("Batched the inserts: 6 minutes now."),
      user("can we also skip unchanged rows?"), cost(140, 61)], NOW - 30 * 3600, "main"),
    ("docs", "docs-site", "Rewrite the getting-started guide",
     [user("rewrite the getting started page"), edit("ROOT/docs-site/start.md"),
      user([{"type": "text", "text": "[Request interrupted by user for tool use]"}]), cost(80, 95)],
     NOW - 2 * 86400, "main"),
    ("ml", "ml-experiments", "Compare three tokenizer settings",
     [user("compare the three tokenizer configs on the eval set"),
      {"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
       "message": {"content": [{"type": "text", "text": f"You've hit your session limit · resets {clock(NOW + 2 * 3600)}"}],
                   "stop_reason": "stop_sequence"}}, cost(30, 4)],
     NOW - 2 * 3600, "tokenizers"),
    ("mobile", "mobile-app", "Ask for notification permission at the right moment",
     [user("when should we ask for push permission?"), reply("After the first saved item. Implemented."),
      cost(96, 20)], NOW - 5 * 86400, "main"),
]
LIVE = [
    {"sessionId": "cli-tool", "kind": "interactive", "status": "busy"},
    {"sessionId": "api-server", "kind": "interactive", "status": "idle"},
    {"sessionId": "nightly", "kind": "background", "state": "blocked"},
]
DEMO_REPORT = """# Work report · the last 14 days

Two weeks of finishing touches: the public API got its rate limits, the login loop is
gone, and the 4-layer board is routed apart from its design rules.

## pcb-router
- Routed the 3V3 plane on layer 3 and stitched the ground vias around the regulator.
- Open: the design rules were being updated when the PC shut down; DRC has not run yet.

## api-server
- Per-key rate limiting: a token bucket per API key, 60 requests a minute.

## web-app
- Fixed the login redirect loop: the session cookie was set on the wrong path. Added a regression test.

## data-pipeline
- The nightly CSV import went from 40 to 6 minutes by batching the inserts.
- Open: skipping rows that did not change."""
SUMMARY = ("Routed the 3V3 plane on layer 3 and added ground stitching around the regulator. "
           "Claude was updating the design rules when the PC shut down; DRC has not run yet.")


def build_home(home: Path) -> None:
    for sid, project, title, records, ended, branch in SESSIONS:
        cwd = f"{ROOT}{os.sep}{project}"
        folder = home / "projects" / cwd.replace(":", "-").replace(os.sep, "-")
        folder.mkdir(parents=True, exist_ok=True)
        lines = []
        # The made-up turns sit before the session's last records, which decide how it ended.
        for record in [records[0], *turns(sid, project, ended), {"type": "ai-title", "aiTitle": title},
                       *records[1:]]:
            record = json.loads(json.dumps(record).replace("ROOT", ROOT.replace("\\", "\\\\")))
            if record["type"] in ("user", "assistant"):
                record.update(cwd=cwd, gitBranch=branch)
                record.setdefault("timestamp", datetime.fromtimestamp(ended - 3600, tz=timezone.utc)
                                  .isoformat().replace("+00:00", "Z"))
            lines.append(json.dumps(record, separators=(",", ":")))
        path = folder / f"{sid}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(path, (ended, ended))


def made_up_limits() -> None:
    """Four days of plan-limit readings, as the dashboard would have taken them: the week
    fills during working hours, the five-hour session limit rises and resets."""
    from tabmancer import usage

    now = datetime.now().replace(second=0, microsecond=0)
    week_resets = now + timedelta(days=2, hours=5)
    week = 18.0
    for step in range(4 * 48):
        moment = now - timedelta(days=4) + timedelta(minutes=30 * step)
        if not 8 <= moment.hour < 20:
            continue  # the dashboard was closed at night
        week += 0.75
        session = (moment.hour * 60 + moment.minute) % 300 * 100 // 300
        usage.record([usage.Limit("session", session, moment + timedelta(minutes=300 - session * 3), ""),
                      usage.Limit("week", min(99, round(week)), week_resets, "")], now=moment)


TEAM = [  # (member, focus, live state, context tokens)
    ("lead", "plans the work, hands it out and checks it", "busy", 182_000),
    ("api", "Stripe endpoints and webhooks", "busy", 341_000),
    ("ui", "the checkout screens", "idle", 96_000),
    ("tests", "end-to-end tests of the flow", "blocked", 702_000),
]
TALK = [  # (minutes ago, sender, receiver, summary)
    (14, "lead", "api", "Add POST /checkout and the Stripe webhook; tell me the payload shape"),
    (13, "lead", "ui", "Build the checkout form; take the payload shape from api"),
    (13, "lead", "tests", "Write the e2e test for a successful payment"),
    (9, "api", "ui", "Payload: {cart_id, email, return_url}; errors come back as 422 with field names"),
    (6, "ui", "lead", "Form done in src/checkout/Form.tsx, wired to POST /checkout"),
    (3, "tests", "lead", "The webhook test needs a Stripe test key: can the user add one?"),
]


def build_team(home: Path):
    """A made-up team at work, "shop": each member's transcript with the messages between them,
    saved as tabmancer's team. Returns the team and its members' live states."""
    from tabmancer import teams

    folder = f"{ROOT}{os.sep}shop"  # only named: nothing is read from it
    team = teams.new_team("shop", folder, "Add Stripe checkout with an end-to-end test",
                          [teams.Member(m, f) for m, f, _, _ in TEAM[1:]])
    teams.save({"shop": team})
    project = home / "projects" / "shop"
    project.mkdir(parents=True, exist_ok=True)
    live = []
    for name, _, state, used in TEAM:
        sid = f"shop-{name}-id"
        records = [{"type": "user", "cwd": folder, "entrypoint": "cli", "timestamp": _ago(20),
                    "message": {"content": "Add Stripe checkout" if name == "lead" else f"work on {name}"}},
                   {"type": "custom-title", "customTitle": f"shop-{name}"},
                   {"type": "assistant", "cwd": folder, "timestamp": _ago(1), "message": {
                       "model": "claude-opus-5-5", "content": [{"type": "text", "text": "On it."}],
                       "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1000,
                                 "cache_read_input_tokens": used - 1002, "output_tokens": 300}}}]
        for minutes, sender, receiver, summary in TALK:
            if sender == name:
                records.append({"type": "assistant", "cwd": folder, "timestamp": _ago(minutes), "message": {
                    "content": [{"type": "tool_use", "id": "t", "name": "SendMessage", "input": {
                        "to": f"shop-{receiver}", "message": summary, "summary": summary}}]}})
            if receiver == name:
                records.append({"type": "user", "isMeta": True, "timestamp": _ago(minutes - 0.5),
                                "origin": {"kind": "peer", "name": f"shop-{sender}", "body": summary},
                                "message": {"content": summary}})
        (project / f"{sid}.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records),
                                              encoding="utf-8")
        live.append({"sessionId": sid, "kind": "interactive", "status": state})
    return team, live


def _ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
