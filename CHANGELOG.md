# Changelog

## Unreleased

- **Model and effort where sessions start.** The `n` dialog has Model and Effort rows (left at
  your default, nothing is passed), `tabmancer new` takes `--model` and `--effort`, and a team's
  line takes an effort after its model: `api: endpoints @sonnet @medium`. Claude Code resumes a
  session at your default effort, so tabmancer gives the sessions it opens with an effort their
  id and passes that effort again when it resumes, forks or hands them over. The preview has a
  Model row.
- **Claude Code Wrapped, to post.** The card now says who the week made you (the Conductor,
  the Night Owl, the Early Bird, the Weekend Warrior, the Marathoner, the Comeback Kid, the
  Steady Hand, the Toolsmith, or the Builder), with the number behind it, and what Claude wrote
  in novels' worth. A story card (1080x1920, `f`) adds the files edited, the models, the longest
  session, the top tool and the hours of the day you worked. `p` shares it: X, Bluesky or Threads
  open a new post with the words in it and the card on the clipboard to paste; for Instagram the
  story card is saved and its folder opened. `tabmancer wrapped --story` and `--share`. On the
  wide card the top tool gave its place to what Claude wrote; it is on the story card.
- **Teams of peers.** A team need not have a lead: leave the lead line out of the members box
  (or pick the line-up *two topics*) and each member owns a topic of its own, starts on it in its
  own tab, and tells the others what touches their work. You talk to each; an idle peer waits for
  you. In the list and the team view peers stand side by side on one wire, with what each sent
  and was sent counted on it.
- **Help with the roles.** In the new-team form, `Ctrl+R` has Claude suggest a line-up for the
  goal (each member's part, its own files, a model and an effort, a lead only when one is needed),
  from the project's files and README, in one call on your plan; `Ctrl+G` shows what the member on
  the cursor's line is told, word for word. `[src/api]` after a member's part names its own files,
  and members named review, tests, docs or research get their kind's rules.
- **Several topics from `n`.** In the message box `Enter` adds a line and each line is a session of
  its own; with one folder, `Ctrl+L` opens the lines linked, as a team of peers. `Ctrl+S` starts.
  The tabs are titled by their lines. `tabmancer new` takes `--prompt` more than once, and
  `--linked`. The dialog keeps its message box, model, effort and keys on a 120x30 screen however
  many projects there are: the list of folders once pushed them out of sight.

## 0.1.2

- **Claude Code Wrapped.** Your last seven days as a card to share, in pixels like Bit: the
  tokens against the week before, the time at work, the sessions, how many worked side by side,
  the ones tabmancer brought back where they left off, the tool called most. On Fridays Bit
  says it is ready; `W` shows it, `s` saves it as a PNG, `c` copies it to paste. Counts only, no
  project names. `tabmancer wrapped` saves it from the command line; `tabmancer wrapped off`
  stops the Friday word.
- **Smoother.** Bit's new poses are drawn about two and a half times faster (front to back, and
  the pixels that cannot be inside a part are skipped), so the dashboard no longer stalls while
  it walks; a turning star draws its own row again, not the whole list; and with a dialog in
  front (the stats, the keys), the clock, the live dot, the stars and Bit behind it no longer
  have the whole screen sent to the terminal again ten times a second: behind a dialog they
  wait, and the clock still ticks.
- **The stats' charts in real pixels.** Where the terminal draws Bit in pixels, the bars by
  day, by project and by tool are drawn in them, of any length and with round ends, and the
  hours of the week are tiles with a gap around each. Scrolled partly out of sight, the part in
  sight is drawn; elsewhere they are the block characters `tabmancer stats` prints.
- **TOKENS TODAY in real pixels.** Where the terminal draws Bit in pixels, the card's chart has
  a place for each of the day's hours, the hours gone by on a floor and the hour now in white.

## 0.1.1

- **A newer tabmancer is told of.** Once a day the dashboard asks PyPI for the latest version
  and, when a newer one is out, shows it beside its name in the top bar and once in a note with
  the command that upgrades it the way you installed it. `tabmancer updates` says where you
  stand; `tabmancer updates off` stops the question.
- **Releases on GitHub.** Each version gets a release with its notes from this changelog: watch
  the repository's releases to hear of them.
- **The token chart in real pixels.** Where the terminal draws Bit in pixels, the sidebar's
  14-day chart is drawn in them too: a bar a day, all as wide, with a gap between them.
- **A tidier dashboard.** Key hints wrap between keys, never inside one, and a team's keys
  always show; the preview's status words hang under themselves, and how long a session ran is a
  line of its own; the footer's keys sit two cells apart where they fit; the column with an open
  session's context is headed Context; a team's row puts each member's sign before its name; the
  stats' bars stand apart.
- **Switch accounts from the dashboard.** `A` (or a click on the account in the top bar) lists the
  Claude accounts you use and opens Claude Code's own sign-in for the one you pick; once it
  lands, the sessions a usage limit stopped on the other account can go on at once.
- **What going on costs, said.** `c` lists the sessions stopped at a limit with how full each
  one's context is and what going on with the ticked ones costs on another account (each
  writes its whole context into the new account's cache), goes on with the ticked ones only,
  or hands them over to fresh sessions, the cheap way on.
- **Signed in again, on at once.** A session stopped at a usage limit in which you sign in with
  another account (`/login`) says so (`◐ signed in again`): `c` continues it now, and one waiting
  for its limit to lift goes on right away. The `/login` used to be taken for how it ended, and the
  limit was forgotten.
- **Fixed.** Clicking Bit again and again no longer lights up its strip (it was taken for text to
  select). Pictures in real pixels take the exact shade around them: Sixel colours were rounded
  down, a shade darker than the sidebar.

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
- **Bit in real pixels** where the terminal draws Sixel (Windows Terminal, foot, xterm and
  others): a finer robot with dozens of moves that follow what your sessions do, drawn to the
  terminal's cells and again when the font changes. Elsewhere, in block characters as before.
- **A demo** (`tabmancer --demo`): the dashboard over made-up sessions, to try every key before
  you have sessions of your own. Nothing of yours is read or changed.
- **Windows first**: Windows Terminal tabs, start at login, desktop notifications, a desktop
  shortcut. macOS and Linux: tmux, WezTerm, kitty, iTerm2, Terminal.app, GNOME Terminal and
  Konsole tabs, and their own ways to start at login.
