# Teams

[← tabmancer](../README.md)

![A team at work: the orchestrator, three members and a message on its way](team.png)

A team is several sessions on one project, each with a focus of its own (the API, the
screens, the tests), and one of them the orchestrator that splits the goal into tasks and
hands them out; or, with no orchestrator, [peers](#peers-no-lead) on related topics side by side. Press `T` and give the team a name, a project folder (it completes the
folders you have worked in), a goal and its members, one a line as `name: what they work
on`. The line-ups ready to use (feature team, bug hunt, write and review) are shown by
name, and `Ctrl+T` goes from one to the next. The lead's line is there too: leave it, or
change what it does. `@sonnet` (or any model Claude Code takes) at the end of a line runs
that member on that model, and `@low` to `@max` sets its effort: `lead @opus @xhigh`,
`api: endpoints @opus @high`, `tests: checks it @sonnet @medium`, say. A team spends your
limit once per member, so the routine areas are where a lighter model or effort pays. Under the form,
what will open is spelled out as you type (the tabs and their names), or what is still
missing. `Enter` and `Tab` go from box to box, and `Ctrl+S` starts the team. `Ctrl+L`
keeps the members in the box as a line-up of your own, under a name; it is then among the
line-ups `Ctrl+T` goes through, and `Ctrl+L` twice on it forgets it.

### Worktrees of their own

Members working at the same time in one checkout edit the same working tree: one's half-done
change breaks another's build, and a commit can take in someone else's files. `Ctrl+O` in the
form gives each member (every one but the lead) a git worktree of its own: it starts with
`claude --worktree <team>-<member>`, which makes `<project>/.claude/worktrees/<team>-<member>`
on the branch `worktree-<team>-<member>`, and works and commits there. Its role says so, and
says who brings the branches together: the lead, which stays in the project and merges a
member's branch when its part is done, or for peers you. The project has to be a git
repository's top folder. The members are still the team's in their worktrees: in the list, in
the team view, and resumed there with their name and role. Claude Code locks a worktree for its
session; once a branch is merged, `git worktree unlock` and `git worktree remove` it (tried with
Claude Code 2.1.317 in `-p` runs: the lock stayed after the session ended).

### Help with the roles

- **Their own files.** `[...]` at the end of a member's part names the folders or files that are
  its own: `api: the endpoints [src/api, docs/api.md] @sonnet`. Its role says they are its to
  edit and the others' are theirs, and every other member sees who owns what. Two sessions
  editing the same file at the same time is what a team most often trips on.
- **The usual kinds.** A member whose name starts with `review`, `test`, `doc` or `research` gets
  that kind's rules besides its area: a reviewer changes no code and sends what it finds to
  whoever made the change; tests tell a member about a bug in its part rather than fix it there;
  docs ask rather than guess; research changes no files and reports with its sources.
- **Suggest (`Ctrl+R`).** With the goal and the folder written, Claude reads the goal, the
  project's files (from git, folder by folder; else its top level) and the start of its README,
  and writes a line-up into the box: each member with its part, its own files, a model and an
  effort, and a lead line only when the parts depend on each other so much that someone must
  hand out the work (else they are peers). One `claude -p` call on your plan, with your default
  model at a medium effort, and no tools; the box is yours to change after.
- **What a member is told (`Ctrl+G`).** On a member's line, shows its role word for word as it
  will start with it, and its first message.

`Ctrl+B` in the same form runs the members in the background instead (`claude --bg`): no tab
each, only the lead's, where you talk to the team. The members start first, the lead's tab
opens once they are up, and `B` lists them. `x` twice on the team stops them (`claude stop`;
their sessions stay), and `s` starts them in the background again.

Or let the lead decide: with the line-up **lead decides**, only the lead starts. It reads
the goal, looks at the project and picks the members the work needs, up to the limit you
set (4 unless you change it), fewer when the work is small: their names, their areas and,
when it matters, their models and efforts. It adds them with one command,
`tabmancer team add <team> "name: area @model @effort; name: area"`, the only command it may run
without asking you (`--allowedTools=...,Bash(tabmancer team add:*)`). Each member opens in
its own tab (or, with `Ctrl+B`, in the background) and introduces itself to the lead, and the
team view shows them join.

A member that has closed comes back with `tabmancer team start <team> [member ...]`, as `s` brings
it back in the dashboard: as itself (its name, its role), where it left off; one that never started
opens new; the lead last; in the background where the team runs its members there. With no member
named, every one that is not open starts; one that is open, in a tab or in the background, is left
be. Every lead may run it without asking (`--allowedTools=...,Bash(tabmancer team start:*)`) and is
told of it in its role, so it can bring back a member it needs; `tabmancer team add` is for members
new to the team.

A new team is always a fresh one: a session is a team's member only if it began once the team was
made, so sessions left by an earlier team of the same name, in the same folder, stay sessions of
their own and are never brought back as the new team's members.

### Peers: no lead

Or no lead at all: **peers**. Take the lead line out of the members box (or pick the line-up
**two topics**) and the team has nobody in charge: each member owns a topic of its own, say
the API reference and the site's pages, two related pieces of work going on side by side.
Each opens in its own tab and starts on its part, you talk to each of them there, and they
tell each other with `SendMessage` what touches the other's work (a shared file, an
interface, the build), agreeing before they change it. Peers always run in tabs, never in the
background. In the list their tree has no lead line, each member's wire counts what the
others sent it and what it sent them, and an idle peer waits for you (an idle member of a
team with a lead waits for the lead). In the team view the cards stand side by side on one
wire. A note about a change made from the dashboard (`g`, `d`) is for every open peer: it
goes on the clipboard, to paste into each tab. `Ctrl+L` keeps a line-up of peers as one.

![A team heading OPEN NOW as one row: each member a name with a sign of what it is doing, and its preview with each member's wire and the messages along it](team-rows.png)

While any of its members is open, the team heads OPEN NOW in the dashboard's own list, as
one row: the team's name, then each member's name after a sign of what it is doing (red `●`
when it waits for you, `!` when it needs you, Claude's turning star while it works, a grey
`●` when it is idle, waiting for its lead, amber `◐` when it stopped at a usage limit, `▲`
when it was cut off), what the open ones are doing in all (`● 1  ✶ 2`, with the same signs),
and the tokens it has used. When the
names do not fit, the quietest are counted (`+2`, red if one of them waits for you too), so
whoever waits for you stays named. `j` goes to the next member waiting for you and shows its
team's members. With the team's row highlighted, the preview shows who talks to whom: each
member on its wire from the lead, with the count of messages sent along it both ways and the
latest one beside it, and a message flying along the wires as it is sent. From 150 columns
the newest messages are listed beside it.

`Enter` on the team's row opens the team view. `e` unfolds the team: its members hang under
its row as a tree, the lead first, each with the latest message to or from it. A
member's row is an ordinary session row (`Enter`, `h`, `p` and the rest work as on any
other), and its preview says how many messages it got and sent. On the team's row, `s`
starts every member that is not open, `a` adds members (each opens and introduces itself to
the lead) and `x` twice closes the members' tabs.

A team whose members are all closed, after a restart say, stays a team: its row sits among
the closed sessions where it last worked, with a box to tick the whole team. `s` on it
starts the team: each member resumed as itself, the lead last, so the others are up when it
starts handing out work. A member opened any other way (ticked and opened, `p`, `c`, a
workspace) also comes back with its name, its role and the setting that lets the team's
messages in; a plain `claude --resume` would leave all three out. Members added to a closed
team wait to start with it.

Every member is an ordinary Claude Code session in its own tab, so it can be closed,
resumed and handed over like any other. Each starts with:

- a name (`--name shop-api`), which is how the others address it;
- its model and effort (`--model sonnet --effort medium`), if the line-up gave it them;
- its role, added to the system prompt (`--append-system-prompt`): its focus, the other
  members and who the orchestrator is;
- a setting that lets messages from the other sessions in (`--settings`, a small file of
  tabmancer's own: your settings are not touched).

They talk with Claude Code's own messaging between sessions: a member calls `SendMessage`
with another member's name, and the message arrives there as a new turn, even if that
session was idle. tabmancer never carries a message; it reads them back from the
transcripts to draw the team view:

- the orchestrator's card on top and the members' cards below it on a tree of wires, each
  card with what the member is doing (working, waiting, needs you, needs your OK, idle), how
  many messages it has not read, how full its context is (a small meter), its model and the
  tokens it has used. The cards grow with the window, a row of them under each wire, and the
  picked member's focus is written under them;
- on the wire to each member, how many messages went down it and back up (`3▼ 2▲`);
- every new message flying along the wires from sender to receiver;
- the conversation as a sequence diagram: a lane for each member (yours on the left), and
  for each message an arrow from the sender's lane to the receiver's, with what it said
  beside it. A message still on its way is a dashed arrow, and a new day is named. The
  prompts tabmancer starts members with are drawn as tabmancer's, quieter than yours.

`Enter` on a team's row opens that team's view. `T` lists the teams anywhere, to open one
or start a new one (on a team's row or a member, with that team picked). `←` `→` pick
a member: its card, its lane and its arrows stay lit and the rest go quiet; past the last
member everyone is lit again. `Enter` goes to the member's tab (or opens it if it is
closed), `s` starts every member that is not open, and `a` adds members, who introduce
themselves to the orchestrator. `d` twice takes the picked member off the team (never the
lead); its sessions stay, as ordinary ones. `g` changes the team's goal; members started
from then on get it in their role. An open lead started with the old line-up or goal, so
either way a note for it goes to the clipboard and you to its tab, to paste it. `x` twice
closes the whole team: every open member's tab, Claude exiting as on `/exit`, so `s` starts
them again later. Under the goal, the tokens the team has used, and each member's.
