# Changelog

## Unreleased

- **`E` unfolds every team, and folds them all again.** Each press goes the other way: the
  teams' members under their rows, or every team back to one row (any team still folded makes the
  next press unfold). From a member's row, folding takes the cursor to its team's row. `e` still
  does one team. Not in the live view, where no team shows.
- **The project sidebar lines up.** The list was 4 cells narrower than the sidebar (a dead strip
  on its right) and its lines started a cell to the left of the heading above them; now they fill
  the sidebar, the selection bar runs edge to edge, and the text starts where the heading's does.
  A rule sets All projects apart from the projects, and the legend of the marks follows the list
  instead of sinking to the bottom behind a dozen empty lines (a long list scrolls and leaves the
  legend and the token chart in sight).
- **A team spends less on waiting.** Measured on six teams' transcripts (31 sessions, some 20k
  replies): files read by more than one member cost 1.3% of what the teams used, and a message
  answered with nothing done about 1%, but sessions left for more than an hour cost 7.2% (Claude's
  cache keeps a conversation for an hour, then the next message writes all of it again, 220k on
  average) and waiting for a background command with `sleep` 4.1%. The roles now ask each
  session that waits with nothing to do to keep its cache with a background `sleep 3300` (three
  times in a row at most, a twentieth of writing it again), and never to wait for a command with
  `sleep` or a polling loop (Claude Code says when one finishes). Starting a closed team whose
  members' cache has gone, with 150k or more in them, now says first how much going on writes again
  and how much less a handover (`h` twice) would; `s` again starts it as it was.
- The dashboard's larger thread pool is set on every system (a test that called itself Linux on
  Windows ran short of threads on a 4-core CI runner).

## 0.1.7

- **No more freezes while you type.** Every few seconds the dashboard stood still for a quarter
  to half a second, in a dialog's boxes too (the first message in `n`). Two causes, both measured
  on a real day of 22 open sessions: Python's garbage collector walked the 460k objects the
  dashboard keeps (sessions, token counts, the search index) on every full collection, 150 to
  260 ms each, and the poll every 2 seconds read each session at work again from 2 MB back (a
  team member a lead started, from 8 MB, looking for a prompt of yours it never had). What is
  loaded at the start is now set aside from the collector (`gc.freeze`), and a session at work is
  read on from where the last read ended. Typing for 50 seconds in `n`: 0.07 s of stalls, from 5
  to 12 s.
- **The router waits for `Ctrl+R`.** It is no longer asked by itself once you stop typing, so
  nothing is sent while you are still writing; `Ctrl+R` asks it and puts its pick in. The dialog
  says that it needs the first message. `"router": false` and `TABMANCER_ROUTER` are gone with
  the automatic call.
- **`Ctrl+E` improves a prompt.** In `n` the first message, and in the new-team form the goal, is
  rewritten by Claude so a session can act on it: clearer, nothing made up, in your language, one
  line for each line in `n` (each line is a session). `Ctrl+Z` in the box puts yours back.
- **Stats: an overview, all time and one project.** A first tab sums the period up: tokens, time
  at work, sessions and projects, each against the period before it, as long; every day of it;
  the top projects, the models, where the tokens went and the records (busiest day, longest day,
  days in a row, longest session, busiest hour, top tool). The other tabs are framed in cards.
  `5` looks over all the time kept, `p` and `P` show one project. The tabs are counted in the
  background, so the screen opens at once.
- **Days kept past their transcripts.** Claude Code deletes a transcript some weeks after its
  last use, and its counts went with it. tabmancer now keeps each finished day as a summary
  (`history.json`, a few kilobytes a day) and takes the days before from Claude Code's own
  `stats-cache.json` (tokens and sessions only; the stats say where they stand in). `tabmancer
  stats --all-time`.
- **Wrapped: slides, a month, all time, and moving.** `W` shows slides now, `←` `→` between
  them, each in a sky of its own colour: who you were (Bit in a pose of the name's own), the
  tokens, the time, the sessions, tools and models, and the card that sums it up. `1` `2` `3`
  take the week, the month or all time. Every slide saves (`s`), copies (`c`) and shares (`p`) as
  shown, and `g` saves it as a GIF with its numbers counting up. `n` puts the top project's name on the tools slide; it is off by
  default. `tabmancer wrapped --period month|all --slide ... --gif --names`.
- **Claude Code up to date before your sessions open.** `tabmancer claude-updates on` runs
  `claude update` once a day before tabmancer opens a session: the dashboard as it opens, the
  sessions you ask for meanwhile waiting for it (Enter, `n`, a team, a prompt), and `tabmancer new`,
  `open` and `team` first. Claude Code's own update takes effect only the next time it starts, so
  the morning's sessions started on yesterday's version. A new version is checked at once for
  every flag tabmancer runs it with. Off by default; `tabmancer claude-updates now` runs it.
- **Fable 5.2's price, as the router and the model picker say it:** twice Opus's, the same for
  reading the conversation back (it said 2.5 times).
- **An advisor for a session or a team's lead.** `n` has an **Advisor** row (none, opus, fable),
  and the new-team form's Runs card one for the lead alone: Claude Code's `--advisor`, a stronger
  model the session asks at the turns that matter. Given as a flag it holds for that session only
  (`/advisor` would save it for every session), and tabmancer gives it again on resume. Only a model
  as strong or stronger can advise (a Fable session takes only Fable). `tabmancer new --advisor`.
- **`j` goes to a session that needs you first** (a question, a permission), round those while
  there are any, then round the ones waiting for you.
- **Fixed:** the stats could hang for good when a period or a project was picked while a tab was
  still being drawn; and counting the projects took a second and a half on a long period (it
  measured the time at work once for each project; now once for all).

## 0.1.6

- **The router.** `n` suggests the model, the effort and the context cap for the first message:
  once you stop typing, one quick call to Haiku reads it and says what will do the work at the
  least cost to your plan, and why. `Ctrl+R` takes the pick; nothing changes without it. Team
  line-ups (`Ctrl+R` in the new-team form) and a team's handover weigh models and efforts the
  same way.
- **A context cap.** The sessions tabmancer starts, and a team's members, start with Claude
  Code's `--autocompact 300k`: every reply reads the whole conversation again, and long ones are
  where most of a plan's limit goes. Pick another in the new **Context** row (in `n`, in the
  new-team form and in a team's handover), with `tabmancer new --autocompact`, or `context_cap` in
  `config.json`. Claude Code forgets the cap on
  resume, so tabmancer gives it again; a session from before caps gets it only while well under
  it. A capped session's context counts against its cap; one without is crowded past the cap new
  sessions get, and the preview says when a big one's cache has gone (an hour idle) and its next
  message writes all of it again.
- **A whole team handed over.** `h` twice in a team's view: its lead writes the handover and the
  line-up for the rest of the work, you see both and change the line-up, and `Ctrl+S` starts the
  team again as a new generation of itself: a fresh lead with the handover, fresh members. `H`
  hands the picked member over alone, as `h` did.
- **Model and effort in the list.** A **Model** column says each session's model and the effort
  it ran at (`opus·xhigh`), as Claude Code now writes it on every reply; on a narrow screen it
  leaves its room to the title.
- The demo's team shows as a team again (it was made after its members' sessions began, so
  they were not its members). `claude -p` calls (summaries, the router, suggestions) start no MCP
  servers: half of a short call's time went on them.

## 0.1.5

- **Each account signs in where it is signed in.** Claude Code's sign-in page took whichever
  claude.ai account the browser was signed in to, whatever account `A` asked for. Now `A` says
  where each account's page opens and `←`/`→` picks it: the browser profile signed in to that
  account (Chrome, Edge, Brave, Chromium; one click), a private window (you sign in afresh), or
  the default browser. An account opens in the profile that syncs with the same Google account
  until you pick another, and the pick is kept. tabmancer reads only the browsers' list of
  profiles, never their cookies or passwords; the sign-in stays Claude Code's.
- **`Ctrl+R` suggests the team's size, and why.** The suggestion says how many members the goal
  needs (2 to 8 besides a lead, up from 5, as few as can work at the same time) and why, in a note
  beside the line-up it writes. Where the lead decides, the size goes into *At most*, and the
  lead still picks the members (the suggestion used to replace that line-up with its own).
- **A goal weighed against the team at work.** With a team at work in the project, the new-team
  form says so, and `Ctrl+R` tells Claude of that team and decides first: a team of its own (its
  parts apart from that team's), members to add to that team (`Ctrl+S` adds them, its goal grows
  by yours and its lead is told who joins and why), or that the team has the goal already (a
  warning, and `Ctrl+S` sends the goal to its lead as a prompt you can change first). The lead's
  message is typed into its tab once it waits for you; `Ctrl+N` makes a team of its own after all.
- **The team's project, picked as in `n`.** While the new-team form's Project box has the
  cursor, the projects you have worked in are listed beside it, with their sessions and when
  you last worked in them: type to find one (those with the words in their name first), `↑`/`↓`
  and Enter (or a click) to pick it. A folder path written out still works. `n` finds them the
  same way now: the projects with the words in their name first, a folder typed out in full first
  of all.

## 0.1.4

- **`tabmancer team start`.** A team's closed members come back from the command line, as `s` brings
  them back in the dashboard: each as itself, where it left off, one that never started new, the
  lead last, in the background where the team runs its members there; name some, or none for every
  one that is not open. A lead may run it without asking and its role says so: `tabmancer team add`
  took only members new to the team, so a lead whose member had closed could not bring it back. A
  member open under its name (even one up and not yet written to), or opened by it a moment ago, is
  left be, so no two sessions answer to one name; where no tab could open, it says so and exits 1.
- **A new team is a fresh one.** A session counts as a team's member only if it began once the team
  was made: a team made again under an earlier one's name, in the same folder, took that team's old
  sessions for its own members, and `s` brought them back instead of starting afresh. The moment a
  team is made is kept with its time zone, so a change of zone or the clocks going back drop no
  member. A team with worktrees asks for another name where an earlier team of its name left its
  worktrees in the project: a fresh member would have started in the old one's, on its branch.
- **A member told only by its lead is in the list.** A member starts with no message of its own and
  waits for the lead's; one never typed into was taken for a session with nothing to resume and
  left out, so its team showed it as never started and `s` would have opened it a second time.
- **Fixed, from a sweep of the whole code for bugs:**
  - `s` twice on a team, while its first `claude --bg` calls were still running, started every
    member twice; a member opened a moment ago (from the dashboard or `team start`) is not opened
    again now.
  - A handover chosen while the session opened or closed behind the dialog went by the session as it
    was: an open one was written to by a second process and not closed.
  - An idle member waiting for its lead (resting) could never be handed over or continued.
  - Enter on a background session's row opened nothing ("open already"): it attaches now, as its
    preview says.
  - `tabmancer open` took a session running in the background for closed and resumed it a second
    time.
  - Clean-up asks again what is open before it moves anything: a session resumed while its screen
    stood open went to the Recycle Bin.
  - The stats screen crashed just after midnight with yesterday's report still in hand, and its days
    were named one off.
  - A 30- or 90-day report told only of the dashboard's last 14 days; a summary written before the
    latest work no longer stands in for the last reply.
  - A first message beginning with a dash (a list's line) was taken by claude for an option; one in
    quotes through npm's `claude.cmd` lost everything after an `&`.
  - A prompt beginning with markup (`<div …>`) was taken for Claude Code's own tag and its session
    left out; a transcript a backup held for a moment stayed out of the list until it changed.
  - A second team linked from `n` in a folder with a long name could be saved over the first.
  - A background session still starting was cleared away by `c` twice in `B`.
  - `tabmancer shortcut` and `startup` broke on a Desktop or Startup folder with non-ASCII letters
    (PowerShell's output read in the wrong code page).
  - Older model ids with a date (`claude-opus-4-20250514`) were read as 1M windows.
  - On Linux, a forked session's replies went to the wrong one of the two; copying the Wrapped card
    there froze the screen for 10 s; and in half-hour time zones the card's hours were 30 minutes out.
  - Hiding a project left its sessions ticked; the footer's "Open N" stood at the first count; the
    card's folder was not picked in Explorer where the path had a space.
  - A part of the dashboard drawn while it faded in could stay blended towards black for good (the
    preview's facts column): every part is drawn again once the fade is over.

## 0.1.3

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
- **Worktrees for a team's members.** `Ctrl+O` in the new-team form starts each member but the
  lead in a git worktree of its own (`claude --worktree <team>-<member>`, on the branch
  `worktree-<team>-<member>`), so two members' half-done changes never mix in one checkout; the
  lead merges (for peers, you). Members are still known as the team's in their worktrees, and
  are resumed there.
- **Several topics from `n`.** In the message box `Enter` adds a line and each line is a session of
  its own; with one folder, `Ctrl+L` opens the lines linked, as a team of peers. `Ctrl+S` starts.
  The tabs are titled by their lines. `tabmancer new` takes `--prompt` more than once, and
  `--linked`. The dialog keeps its message box, model, effort and keys on a 120x30 screen however
  many projects there are: the list of folders once pushed them out of sight.
- **A team with a long goal opens.** A member's role now goes to Claude Code in a file of its own
  (`--append-system-prompt-file`), and a first message names the goal on one line, cut short when
  it is long. With a plan of a few pages pasted in as the goal, each tab's command line carried it
  once or twice, six tabs came to 38,000 characters, past Windows' 32,767, and no tab opened. A
  goal keeps its lines now, so a pasted plan stays readable in the roles. Many tabs at once go to
  Windows Terminal in several command lines when one would be too long, and a terminal that will
  not start is said in the dashboard rather than ending it.
- **A `;` inside a member's part stays in it.** `art: drafts first; icons and the splash [assets/]`
  made a second member named after the words after the `;` (Claude's suggested line-ups write
  them too, and so does a lead adding members). A `;` starts another member only when a name
  follows it (`; ui: screens`, `; tests @haiku`). Names may be written in any letters.
- **A member that went into a folder of the project is still the team's.** A transcript keeps the
  folder its session was in last, so a member that had gone into `src/` showed as never started
  and `s` started a fresh one; it is found, and resumed in the project, now.
- **Wrapped under a night sky.** Both cards are drawn on a sky going from deep blue to purple, with
  stars where nothing is written and Bit glowing in the theme's colour; each number is a line of
  its own, with a sign, the number large and in a heavier stroke, and its name in a colour of its
  own. Labels are in small letters now (the pixel font has them, the tails of g, j, p, q and y
  under the line). The `W` window is the card's sky too, with the wide and the story card to pick
  at its top; the share menu matches it, and so does the card in words where no pictures are drawn.
- **`n` and the new-team form, made over.** Each is in framed cards, the one the cursor is in lit
  in the theme's colour, with one line of help for the box in focus instead of many grey ones, and
  its keys always on screen. `n` has two columns: the folders on the left (headings, ✓ for the ones
  ticked, where the highlighted one is under the list, your home as `~`, what the filter found
  lit), and on the right the first message, the model and effort, and every tab that will open.
  A `/` and a `\` are alike in the filter (`desktop/shop` finds `C:\...\Desktop\shop`) and in the
  team's project box, and every word typed must match. The new-team form has the team and its
  line-up on the left, and on the right the team it will open, drawn as the team view draws one
  (the lead on top, the members on their wires, each card with its model and effort), over where
  the members run. A goal written in the form keeps its lines.

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
