# Using tabmancer

[← tabmancer](../README.md)

## What the status means

| Status | Meaning |
|---|---|
| ● running | open in another terminal right now (cannot be ticked) |
| ◆ background | a background session (`claude --bg`); opening it attaches to it |
| ✓ done | Claude had finished its reply; the session was waiting for you |
| ‖ stopped | you pressed Esc while Claude was replying |
| ▲ cut off | closed while Claude was still working: a tool call or reply was in flight |
| ○ unanswered | your last message never got a reply |
| × error | the last reply was an API error |
| ◐ limit · 16:00 | stopped at a usage limit, which lifts at 16:00 (then: `limit lifted`) |
| ◐ signed in again | stopped at a usage limit, then signed in with another account in it: it can go on now |
| × connection lost | the connection to the API dropped, so the reply never came |

A session stopped at a usage limit says when the limit lifts, whether it is still open or
not, and does not count as waiting for you. `c` lists them, each with how full its context is,
and asks what to do with the ticked ones (all to begin with; `Tab` to the list, `Space` ticks):

- **Continue them now.** For when you have signed in with another account (`/login`), or the
  limit has lifted.
- **Hand them over.** Each starts afresh with a handover Claude writes from its transcript.
  On another account this is the cheap way on: a session that goes on writes its whole
  context into the new account's cache with its first reply (hundreds of thousands of tokens
  for a big one, a large part of a 5-hour limit); a handover is a few thousand.
- **Continue them when the limit lifts.** The dashboard has to be open at that time; the plan
  is kept if you restart it. `c` again can call it off.

### Another account

`A` (or a click on the account in the top bar) lists the Claude accounts you have used, with
their plans: Enter on one opens Claude Code's own sign-in in a new tab (`claude auth login
--email`). Its page signs in whichever claude.ai account the browser is signed in to (the
email is only filled in), so under each account `A` says where its page opens, and `←`/`→`
picks another place:

- **A browser profile** (Chrome, Edge, Brave, Chromium) that is signed in to that account:
  one click on Authorize finishes it. An account opens in the profile that syncs with the
  same Google account until you pick another.
- **A private window**, where you sign in afresh: always the account you meant, nothing to
  set up.
- **Your default browser**, as links open (and as before).

The pick is kept for each account (and for "Another account"). Claude Code opens its page
with the program in `BROWSER`, so the sign-in tab gets a small launcher of tabmancer's that
opens it there. tabmancer reads only your browsers' list of profiles, each one's name and
the Google account it syncs with, and never touches a sign-in itself: it keeps the emails it
has seen Claude Code signed in with and where each one's page opens. Once the switch lands,
every session goes on with the new account, and the sessions a usage limit stopped on the
other one say `◐ signed in again`: `c` continues them now, and those waiting for their limit
go on at once.

Sign in with another account in a session stopped at a limit (`/login` in it) and it says
`◐ signed in again`: `c` continues it now, and if it was waiting for its limit to lift it goes
on right away.

Either way a closed session opens in a new tab, and an open one gets the prompt in its own
tab, as if you had typed it there: on Windows it goes into that tab's console, and no window
comes to the front. Only the process Claude Code lists for that session gets it, and its tab
is never closed. Where the prompt cannot be typed in (not on Windows, say), Bit says which
sessions to continue by hand. When the top bar's last reading shows room on your plan,
continuing now is offered first. Auto-continue tells Claude what stopped the session (a
shutdown, a usage limit or a dropped connection), unless you set a prompt of your own.

The list keeps the open sessions under **OPEN NOW** and the rest under **EARLIER**. For an
open session the **Context** column shows how full its context is. For a closed one (under
EARLIER, where the column says `process`) it shows how its process ended: it is
empty when the Claude process closed normally (exit, a closed window or an OS shutdown;
the preview says `clean`) and says `killed` when it died without cleaning up (a crash,
power loss, killed from Task Manager). **Open as** is shown only when a session would not
simply be resumed.

Each colour means one thing everywhere: red is your turn (a session waiting for you, and
the NEED YOU card), amber is work to continue (cut off, unanswered, and the TO CONTINUE
card), blue is at work, green is open, grey is done. A state that wants you (needs you, cut
off, unanswered, a limit lifted) is a badge on its own colour; the rest are plain words. The
highlighted row takes the theme's colour. Under the projects, a legend says what their marks
mean. Beside each card's number is what it counts: the open sessions by what they are doing,
the projects waiting for you or with work to continue, and today's tokens hour by hour. In a
window under 40 rows each card is two lines instead, its number and then what it counts, so
the list keeps its room. The top bar shows what the list is narrowed to and how many sessions
are ticked as pills that a narrow window never cuts off.

## Keys

The footer shows most keys, on two rows when one is too narrow for them (three in a very
narrow window), so none is cut off. It follows the highlighted row: on a team's row `s` reads
"Start rest" and `x` "Close team", with sessions ticked `Enter` reads "Open 2", and a key that
does nothing on the row is dimmed there (never hidden). The keys keep to the groups `?` shows
them in. `?` lists every key with what it does, and runs the one you pick with `Enter`. Under
the highlighted session the preview says what `Enter` will do with it, as it stands.

| Key | Action |
|---|---|
| `?` | every key, explained; `Enter` runs the one picked |
| `Space`, click `[ ]` | tick / untick a session; on a closed team's row, all its members |
| `Enter` | open the ticked sessions (or the highlighted one if none are ticked); on a team's row, its members, then the team view; on a closed team's row, start the team |
| `j` | go to the next open session that needs you (a question, a permission), again for the one after; when none does, the ones waiting for you |
| `m` | cycle how it opens: resume → auto-continue → fork → new |
| `a` | tick / untick everything visible; on a team's row, add members |
| `/` | find by project, title, or anything said in a session (`Esc` clears) |
| `1` `2` `3`, click a card | only what OPEN NOW, NEED YOU or TO CONTINUE counts; again (or `Esc`) shows all. A click on TOKENS TODAY opens the stats |
| `w` | save the ticked sessions' folders as a workspace |
| `o` | open a workspace (`d` deletes one) |
| `s` | AI summary of the highlighted session; on a team's row, start every member not open |
| `l` | switch to the live view and back |
| `e`, click | open the row to see how the session ended (again to close); on a team's row, its members under it (again to fold) |
| `E` | every team's members under its row, or every team back to one row; each press goes the other way |
| `p` | write a prompt to the highlighted session |
| `n` | start fresh sessions in several projects at once |
| `T` | teams: several sessions on one project with an orchestrator, talking to each other; the list of them, and a new one (a team's own view: `Enter` on its row) |
| `h` | hand a session over to a fresh one; an open one is closed |
| `c` | the sessions stopped at a usage limit: continue them now (signed in with another account) or when the limit lifts (in the footer only while one is) |
| `r` | rename the highlighted session (see below) |
| `x` | hide the ticked sessions, or the highlighted one (in the sidebar: the whole project); nothing is deleted. On an open team's row, twice: close its members' tabs |
| `X` | show the hidden ones too (`x` on one brings it back) |
| `f` | pin the ticked sessions, or the highlighted one, above the rest; again unpins |
| `u` | stats: an overview, tokens, when you work, tools and files, plan limits, a written report; `5` all time, `p` one project |
| `W` | Claude Code Wrapped: your week, month (`2`) or all time (`3`) as slides to share, `←` `→` between them ([stats](stats.md#claude-code-wrapped)) |
| `B` | background sessions: attach, stop, remove, clean up |
| `,` | which CLIs to list: Claude Code, Codex, Antigravity |
| `t` | next colour theme (remembered) |
| `b` | Bit in real pixels, calm, in block characters, or away (remembered) |
| `Tab` | move between the project sidebar and the session list |
| `q`, `Esc` | quit (`Esc` first clears the filter, then a card's) |

**resume** runs `claude --resume <id>`. **auto-continue** resumes and immediately tells
Claude to carry on with the interrupted work, so you do not have to type "continue" in
every tab. It is the default for sessions that were cut off, left unanswered or ended
on an error. **fork** runs `claude --resume <id> --fork-session`, a new session that
starts from a copy of the history. **new** runs a fresh `claude` in that session's folder.
A session that is open right now, in another tab or in the background, is never resumed a
second time: just before it opens anything, tabmancer asks again which sessions are open,
since the list can be a moment behind. And if one it opened has not come up after a while
(Claude Code would not resume it, its folder gone say), the dashboard names it; its tab says why.
To move a session's work to a fresh one, press `h` (see
[Crowded sessions and handing over](handover.md#crowded-sessions-and-handing-over)).

To change what auto-continue says: `tabmancer continue-prompt "Pick up where you left off"`.

`r` renames a Claude Code session the way `/rename` does inside it, so the new name shows
in Claude Code's own `/resume` list too. A closed session runs `/rename` once without a tab
(`claude -p --resume`), which asks no model. An open one has to do it itself, or its next
turn would write the old name back: `/rename` goes to your clipboard and its tab comes to
the front, to paste. Team members keep their names, since the team finds them by it.

## Write to a session

Press `p` on a session and type what Claude should do next.

- **Enter** opens the session in a new tab with your prompt already sent
  (`claude --resume <id> "<prompt>"`).
- **Ctrl+B** runs it in the background instead (`claude --bg --resume <id> "<prompt>"`). The
  dashboard shows it working and tells you when it needs you. `Enter` on it attaches.
  `B` stops or removes it when you are done (see [Background sessions](#background-sessions)).

The dashboard stays open either way, so you can write to several sessions in a row.

A session that is already open in another tab cannot be written to from outside, so for
those `p` (or `Enter`) takes you there instead. It selects that session's tab and brings
its window to the front, ready for you to type: in Windows Terminal, in tmux (the pane
whose title is the session's), and on macOS in iTerm2 and Terminal. The tab is found by
its title, which Claude Code sets to the session's name.

Click a session, or press `e`, to open its row and see what it came to: Claude's answer
for a finished session, the question left hanging for a cut-off one, or the AI summary
once one exists. Click it again to close it. Clicking its `[ ]` box ticks it instead.
Opened rows stay open while the list updates.

## Live view

![Live view: open sessions sorted by what needs you first](live.png)

`tabmancer live`, or `l` in the picker, lists only the sessions that are open right now.
It refreshes every few seconds and sorts them by what needs you first:

- **needs you**: a background session reports that it is blocked
- **waiting for you**: Claude finished and the session is idle
- **working**: Claude is busy. If it has written nothing for five minutes, the row adds
  "quiet", which usually means a long command or an unanswered permission prompt.

The dashboard stays live in both views. Every two seconds it reads Claude Code's
live-session files, which costs next to nothing. Every 16 seconds it also asks
`claude agents --json`, which covers background sessions and notices sessions that
have closed. When a session goes from working to waiting, a note pops up. In the live
view the terminal bell rings as well.

## Background sessions

A background session (`claude --bg`) keeps its process running after Claude has
finished, waiting for a next message, and Claude Code keeps its entry after the process
ends. Both pile up. Press `B` to see every background session Claude Code knows:

- **Enter** attaches to it in a new tab (`claude attach <id>`).
- **x** stops it (`claude stop <id>`). Its conversation is kept, and attaching starts it
  again. A session that is working or waiting for an answer asks you to press `x` twice.
- **d** removes it (`claude rm <id>`), after a second press. That deletes the entry and
  its worktree. The transcript stays, so the session is still in the list and can be
  resumed. If the worktree has unpushed commits, Claude Code does not discard them; it reports them instead.
- **c** cleans up: it removes every background session that is idle or has stopped,
  after a second press. Sessions that are working or waiting for you are left alone.

Bit mentions it when background sessions sit idle.

## Fresh sessions

A long conversation carries its whole history into every turn, so starting clean is
often the better move. Press `n`:

- The projects you worked in lately are listed, newest first, with the folders of your
  workspaces. Type to filter them, or type any folder path to add it.
- `Space` ticks a project. Write a first message if you want one: every new session
  gets it, so they all start working right away.
- **Improve it (`Ctrl+E`).** Claude (Sonnet at a low effort, no tools) rewrites the first
  message so a session can act on it at once: what is to be done, where, what must hold and when
  it is done, as far as your words say so, in your language and with nothing made up. One line
  stays one line, since each line is a session. The rewrite goes into the box as one edit:
  `Ctrl+Z` there puts yours back.
- **Several topics at once.** In the message box `Enter` adds a line, and each line is a session
  of its own, in every ticked folder: two related topics in one project are two lines. The tab of
  each is titled by its line.
- **Linked.** With one folder ticked and two lines or more, `Ctrl+L` opens them as a
  [team of peers](teams.md#peers-no-lead): no lead, each line a member's part, and they know of
  each other and message each other about what touches the other's. A line written
  `api: the endpoints` names its member api; the others are first, second and so on.
- `Ctrl+S` (or `Enter` outside the message box) opens a fresh session in each ticked folder,
  each in its own tab.
- With more than one CLI on, `Ctrl+O` picks which one to start.
- **Model** and **Effort** pick Claude Code's `--model` (opus, sonnet, haiku, fable) and
  `--effort` (low to max) for these sessions: `Tab` to the row, `←`/`→` to change it, and a line
  under them says what the pick is good for. Left at *your default*, nothing is passed and your
  own Claude Code settings decide. **Context** is the context cap they start with, 300k unless you
  pick another (see [the context cap](handover.md#the-context-cap)).
- **Advisor** gives these sessions Claude Code's advisor (`--advisor opus` or `fable`): a stronger
  model each one asks at the turns that matter, before it settles on an approach, when an error
  keeps coming back, before it says it is done. Given as a flag, it holds for these sessions only
  (`/advisor` in a session saves it for all of them), and tabmancer gives it again when they are
  resumed. Only a model as strong as the session's or stronger can advise it (a Fable session takes
  only Fable): the row says so when the pick cannot. Each time it is asked, the advisor reads the
  whole conversation again, uncached, so it costs most on a long session.
- **The router** picks the three for the first message, so it needs one: write what the session
  is to do (three words or more), then press `Ctrl+R`. One quick call to Haiku (at a low effort,
  with no tools) reads the message and the folders' names, and its pick goes into the rows with
  why: `✓ the router's pick — well-defined tests`. It is never asked by itself, so nothing is sent
  while you are still writing; the pick stays yours to change after. It weighs the cost to your
  plan: the cheapest model and effort that will still do the work well, and a wider cap only where
  the work needs much of a codebase in view at once.

Claude Code resumes a session on the model it ran on, but at your default effort. So a session
opened here with an effort gets its id from tabmancer (`--session-id`), and whenever tabmancer
resumes, forks or hands it over, it passes the same effort again. The list's **Model** column
and the preview's **Model** row show the model a session last answered with and the effort it ran
at, as Claude Code writes it on each reply (since 2.1.319), else as tabmancer started it.

These start clean. To carry a session's work on in a fresh one, hand it over with `h`
instead (see [Crowded sessions and handing over](handover.md#crowded-sessions-and-handing-over)).

From a shell or a script:

```sh
tabmancer new web-app api-server --prompt "Run the tests and fix what fails"
tabmancer new C:\dev\new-idea --cli codex
tabmancer new docs-site --model sonnet --effort medium
tabmancer new docs-site --autocompact 400k
tabmancer new shop --prompt "api: the refund endpoint" --prompt "web: the refund page" --linked
```

A name is matched against the projects you worked in lately; a path works anywhere.

## Workspaces

A workspace remembers folders, not sessions. Session ids go stale as soon as you
`/clear` or start a new session. Opening a workspace therefore continues the most
recent session in each folder, the way `claude --continue` would. A folder saved
twice continues its two most recent sessions. Sessions that are already open count,
so nothing opens twice. Rows set to **new** in the picker are saved as "start a fresh
session here".

```sh
tabmancer open "PCB work"                    # open it without the picker
tabmancer workspaces                         # list them (--delete NAME to remove one)
tabmancer shortcut --workspace "PCB work"    # Windows: a desktop shortcut for it
```

## Hide and pin

Old work and test sessions need not crowd the list, and nothing has to be deleted for that.
`x` hides the ticked sessions, or the highlighted one; with a project picked in the sidebar,
it hides the whole project. Hidden ones drop out of the list, the counters and the sidebar,
and are not ticked for reopening; their transcripts stay where they are. The status line
says how many are hidden, `X` lists them too (marked `hidden`), and `x` on one brings it
back. An open session always shows. `f` pins sessions: they stand under **★ PINNED**,
above the rest. Both are kept in `marks.json` in tabmancer's own folder.
