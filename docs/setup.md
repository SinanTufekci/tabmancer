# Setup and commands

[← tabmancer](../README.md)

## Install

Requires Python 3.10+ and the Claude Code CLI (`claude`) on your PATH. Tabs open in
Windows Terminal, tmux, WezTerm, kitty, iTerm2, Terminal.app, GNOME Terminal or Konsole.
In any other terminal, tabmancer prints the commands to run.

```sh
uv tool install tabmancer    # or: pipx install tabmancer
tabmancer shortcut           # Windows: adds a "tabmancer" shortcut to the desktop
```

Or run `tabmancer` from any terminal. For the latest from GitHub:
`uv tool install git+https://github.com/SinanTufekci/tabmancer`.

## Other commands

```sh
tabmancer list           # print recent sessions; * marks the ones open at the last shutdown
tabmancer list --json    # the same as JSON
tabmancer stats          # tokens, working hours, tools and limits (--json, --csv)
tabmancer report         # a report of the week, written by Claude (--save)
tabmancer --days 30      # look further back (default 14)
tabmancer --all          # include claude -p, desktop app and IDE sessions
tabmancer doctor         # check that everything tabmancer relies on still works
tabmancer --demo         # the dashboard over made-up sessions: nothing of yours is touched
```

Run `tabmancer doctor` after a Claude Code update if something looks off. It checks the
CLI (its version against the one tabmancer was last checked with, and that every flag and
command tabmancer runs it with is still there), how open sessions are detected, the
terminal, the shutdown records, and whether recent transcripts still read as expected.

## Start at login

```sh
tabmancer startup on       # also: off, status
```

At login a windowless check runs. If sessions were left open at the last shutdown, the
picker opens with them ticked, in a terminal window of its own; if none were, nothing
appears. On Windows this is a shortcut in your Startup folder, on macOS a LaunchAgent
(`~/Library/LaunchAgents/com.tabmancer.startup.plist`) whose picker opens in Terminal, and on
Linux an autostart entry (`~/.config/autostart/tabmancer.desktop`) whose picker opens in the
first terminal found (x-terminal-emulator, GNOME Terminal, Konsole, kitty, WezTerm, ...).

## Desktop notifications

While the dashboard is open, a session that stops working and waits for you (or stops at
a usage limit) is also told by a desktop notification, unless you are looking at the
dashboard. Several at once come as one notification.

On Windows a Windows toast shows it (it says it comes from Windows PowerShell, whose app id
it borrows so that nothing has to be installed). Whether you are looking is asked of
Windows itself: the window in front is Windows Terminal with the dashboard's tab selected.
macOS uses `osascript` and Linux `notify-send`, when the terminal reports that the dashboard
is not focused.

```sh
tabmancer notifications          # on or off
tabmancer notifications off      # and on again with: on
tabmancer notifications test     # show one now
```

## Free disk space

```sh
tabmancer cleanup
```

Lists every session on disk, biggest first. A session's size counts its transcript, its
folder of subagent transcripts, and its file snapshots for rewind. Tick sessions with
`Space`, or press `o` to tick everything older than a week. `Enter` moves them to the
Recycle Bin (Trash on macOS and Linux), so a mistake can be undone. Sessions that are
open right now cannot be ticked.

Claude Code already deletes sessions older than `cleanupPeriodDays` (30 by default) on
its own. This is for the big ones in between.

## Codex and Antigravity

tabmancer is built around Claude Code, and lists only its sessions until you say
otherwise. If you also use OpenAI's Codex CLI or Google's Antigravity CLI (`agy`), press
`,` and turn them on, or run:

```sh
tabmancer clis                    # which are on, and where each one is installed
tabmancer clis enable codex agy   # or: disable
```

Their sessions then join the list, the project sidebar and the counters, with the CLI's
name in the Process column. They open in tabs like Claude Code's, with that CLI's own
commands: `codex resume <id>` or `agy --conversation <id>`, and a fresh `codex` or `agy`
for **new**. Each CLI keeps its sessions in its own format, so what tabmancer can show
differs:

| | Claude Code | Codex | Antigravity |
|---|---|---|---|
| How it ended, last prompt and reply | ✓ | ✓ | title only |
| Resume, auto-continue, new | ✓ | ✓ | ✓ |
| Fork | ✓ | ✓ | |
| Open right now, background sessions | ✓ | | |
| Tokens, time at work, tools in the stats | ✓ | ✓ | |
| AI summary | ✓ | ✓ | |

With Codex on, its plan limits show beside Claude's: in the top bar, in the Plan limits
tab and its forecast. Codex has no command to ask, so they come from what its latest turn
wrote into its session files.

Codex sessions started by `codex exec` are left out like `claude -p` ones; `--all`
brings them in. Antigravity keeps its conversations in a binary format, so only their
index (title, folder, time) can be read. Neither of the two says when a session is open,
so tabmancer cannot stop you from opening one twice.

## Bit

A little robot of our own walks along the top edge of the preview, below the list: a
head with a beacon on top and a dark visor, a body with a light on its chest, two arms
and two legs. It takes up four rows and has no box of its own: around it is the list's
background.

Bit is pixel art. In Windows Terminal each cell holds 2x4 pixels (Unicode block
octants), so Bit is 22x15 pixels in a space eleven cells wide (the outer cells are for
its arms, raised). It is drawn to the cell grid, so no cell has to show more than the two
colours a terminal cell can, in any pose, and it moves up or down only a whole cell at a
time: a hop lifts it one cell. Other
terminals get half blocks, which every font has. Set `TABMANCER_MASCOT_BLOCKS` to
`octants` or `half` to choose. Frames that have not changed
are not redrawn.

What Bit does follows what the sessions do:

- While sessions work, it works too, eyes down and hands quick, and hurries about; its
  chest light pulses quickly.
- While a session needs you (a question to answer), it stays put and calls you: it waves
  with its eyes wide every few seconds, and its beacon blinks red, for as long as it
  lasts.
- When nothing happens, it wanders slowly, sits, and in the end dozes off with drifting
  Z's and its eyes closed.

It walks with its feet lifting in turn and its arms swinging, looks around, waves, hops
and blinks. Its eyes follow the mouse pointer. It talks in a bubble beside it:

- It greets you, saying how many sessions were open at the last shutdown.
- It cheers with confetti when a session finishes ("web-app is done!").
- It throws red sparks when one needs you.
- It warns when a usage limit passes 85 %.
- Click it for a tip about the keys; click it again and again and its head spins.

Press `b` to hide Bit, or to bring it back.
