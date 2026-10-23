# Changelog

## 0.1.0

The first release.

- **The dashboard.** Every recent session from every project: how each closed one ended (done,
  stopped, cut off, unanswered, at a usage limit, connection lost) and what each open one is
  doing (working, waiting for you, needs you). Counters that filter the list (`1` `2` `3`), a
  project sidebar with a 14-day token chart, a preview that says what `Enter` will do, and a
  footer that follows the row you are on.
- **Call sessions back.** After a reboot or a crash, the sessions open at the shutdown are
  ticked; `Enter` opens each in its own tab and folder, and the ones cut off mid-reply carry on
  by themselves. Resume, auto-continue, fork or start fresh, per session (`m`).
- **Past a usage limit.** Sessions stopped at a limit say when it lifts; `c` continues them all
  then, or right away once you have signed in with another account. An open one gets the
  prompt in its own tab.
- **Teams.** Several sessions on one project with an orchestrator, talking through Claude Code's
  own messaging between sessions: one row in the list with each member's state, a team view with
  the conversation as a sequence diagram, members in tabs or in the background, a lead that picks
  its own members if you let it.
- **Crowded sessions.** How full every context is, and `h` to hand the work over to a fresh
  session with a full handover as its first message.
- **Write to a session** (`p`), in a tab or in the background; **background sessions** (`B`);
  **fresh sessions** in several projects at once (`n`); **workspaces**; **rename** (`r`);
  **hide and pin** (`x`, `f`); **search** everything said (`/`).
- **Stats** (`u`): tokens, when you work, tools and files, plan limits with a forecast, and a
  written report of a period.
- **Codex and Antigravity** sessions in the same list, if you turn them on (`,`).
- **Windows first**: Windows Terminal tabs, start at login, desktop notifications, a desktop
  shortcut. macOS and Linux: tmux, WezTerm, kitty, iTerm2, Terminal.app, GNOME Terminal and
  Konsole tabs, and their own ways to start at login.
