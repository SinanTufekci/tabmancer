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
helmet with a dark visor, two eyes and a beacon on a springy antenna, a body with a light
on its chest, two arms and two legs. It takes up four rows and has no box of its own:
around it is the list's background.

Where the terminal draws Sixel images (Windows Terminal from 1.22, foot, WezTerm, iTerm2,
Konsole and others that say so), Bit is drawn in real pixels: 110x78 in Windows Terminal,
in the same eleven cells by four rows, to the size of the cells elsewhere. It moves a pixel
at a time, squashes before a hop and stretches in the air, its eyes change with what it does
(happy, wide, hearts, stars, dizzy rings...), and it handles things: a laptop, a mug, a
clipboard, a box, a wrench. Its pictures are sent right after the dashboard's own updates, so
it never covers a dialog. Made bigger or smaller with the font, it is drawn again to the new
cells. `tabmancer` asks the terminal when it starts; `TABMANCER_SIXEL=0` keeps Bit in block
characters, `=1` asks for pixels where the terminal does not say it draws them. xterm draws
them when started as a VT340: `xterm -ti vt340`.

Elsewhere Bit is pixel art in block characters. In Windows Terminal each cell holds 2x4
pixels (Unicode block octants), so Bit is 22x15 pixels in a space eleven cells wide (the
outer cells are for its arms, raised). It is drawn to the cell grid, so no cell has to show
more than the two colours a terminal cell can, in any pose, and it moves up or down only a
whole cell at a time: a hop lifts it one cell. Other terminals get half blocks, which every
font has. Set `TABMANCER_MASCOT_BLOCKS` to `octants` or `half` to choose. Both drawings
follow the same life; in blocks, a move shows as the nearest pose these have. Frames that
have not changed are not redrawn.

What Bit does follows what the sessions do:

- While sessions work, it works too: it types on its laptop, thinks it over (a gear turns
  above its head, and sometimes a light bulb comes on), reads, fixes things, carries a box
  across or hurries about; its chest light pulses quickly.
- While a session needs you (a question to answer), it stays put and calls you, a
  different way each time: waving, pointing up at the list, or jumping with both arms up,
  with its eyes wide and its beacon blinking red, for as long as it lasts.
- When nothing happens, it wanders, skips, looks around, taps its foot, stretches, drinks
  a coffee, whistles or sits swinging its feet; in the end it yawns, nods off and sleeps
  with drifting Z's. News, a click or the pointer startles it awake.

It talks in a bubble beside it:

- It greets you, saying how many sessions were open at the last shutdown.
- It cheers with confetti when a session finishes ("web-app is done!"), or dances, or spins.
- It throws red sparks when one needs you.
- It worries, with a drop of sweat, when a usage limit passes 85 % or a context fills up.
- It hurries off when the sessions it called back are at work again, hops twice when a
  usage limit lifts, carries a box across when a session is handed over, and taps its foot
  while background sessions sit idle.
- Click it for a tip about the keys; click it again and again and it gets dizzy.

Press `b` to change how Bit is drawn: in real pixels (where the terminal draws them), calm
(the same, standing about more, with no hops, spins or confetti), in block characters, or not
at all, then back; it is remembered. `tabmancer doctor` says how it is drawn
in the terminal it runs in, and why.
