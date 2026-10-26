# How it works

[← tabmancer](../README.md)

## How it works

Claude Code stores each session as a JSONL transcript under
`~/.claude/projects/<project>/<session-id>.jsonl` (or under `CLAUDE_CONFIG_DIR`).
tabmancer reads the start and end of each recent transcript:

- The last conversation record tells how the session ended. For example, a tool call
  with no result means it was cut off mid-task.
- Claude Code writes a final bookkeeping record when it exits normally. If that record
  is missing, the process was killed.
- Sessions that are open right now come from `claude agents --json`. If the CLI is
  missing or too old, they come from Claude Code's live-session files in
  `~/.claude/sessions` instead.
- Shutdown times come from the System event log on Windows, from the journal (each
  earlier boot's last entry) or else wtmp on Linux, and from `last` on macOS. A session
  whose last write lands moments before a shutdown counts as open at that shutdown. Where
  none can be read, sessions that ended within the same minute or two count instead.

Parsed results are cached by file size and modification time, so the list opens
instantly. The first run reads each recent transcript once.

The transcript format is internal to Claude Code and can change between releases.
tabmancer reads it defensively: a field of a type it does not expect is left out and shows up
empty, a transcript it cannot read at all is left out of the list (the dashboard says how many,
`tabmancer doctor` says why), and an error in the background is told in a notification and
written to `errors.log` in tabmancer's own folder instead of closing the dashboard.
`tabmancer doctor` also notices when too many fields come up empty.

## Status

Early (0.1). It is built and used daily on Windows 11 with Windows Terminal, and the
tests run on Windows and Linux.

On macOS and Linux it has not been used day to day yet. What is known:

- The dashboard itself is plain Textual and runs anywhere Python does.
- In tmux on Debian (under WSL) it has been installed with pipx and run for real: `Enter`
  opened the ticked sessions in tmux windows, each with the right `claude --resume`, and a
  cut-off session carried on with an auto-continue prompt full of quotes, `$(...)` and `%`,
  which arrived whole. `tabmancer --demo` ran there too. Opening tabs in WezTerm, kitty,
  iTerm2, Terminal.app, GNOME Terminal and Konsole is covered by unit tests but has not been
  tried by hand.
- The shutdown times (the journal, wtmp) and focusing a session's pane in a real tmux server
  run for real in the Linux tests. Start at login writes its autostart entry in the tests
  too, but neither the entry nor the picker's terminal window has been tried at an actual
  desktop login, nor have the desktop notifications (`notify-send`).
- Nothing has been tried on a real Mac: the LaunchAgent, `last`'s output there, the
  AppleScript that finds a tab in iTerm2 or Terminal, and the notifications through
  `osascript` are written to their documentation and unit-tested with the commands stubbed.
- Closing a session's tab (for a handover of an open session) is Windows Terminal only.

Reports are welcome.

The pictures are of a real Windows Terminal window over made-up sessions:
`uv run --with pillow python scripts/window_shots.py <folder>` takes them and
`scripts/readme_media.py <folder>` turns them into the PNGs and `docs/demo.webp`;
`uv run --with pillow --with websockets python scripts/promo.py` draws the banner.
