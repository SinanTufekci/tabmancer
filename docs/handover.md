# Crowded sessions and handing over

[← tabmancer](../README.md)

A session whose context fills up gets slow, costly and forgetful. tabmancer shows how full
each one is: a bar and `73%` beside every open session, and the size against the model's window
(200K, or 1M for the models that have it) in the preview. It is worked out the way Claude
Code's own statusline does it, from what the last reply was sent with.

Past 60% (set `TABMANCER_CONTEXT_WARN` to change it) the dashboard says so, once per
session: "web-app has been going for 6h 12m and its context is 73% full. /compact in its tab
goes on with the same work; h starts a new phase in a fresh session, and shows what is handed
over." The preview says the same in a line under the context, `/compact: go on · h: new
phase`, for as long as the session stays that full. It never does anything about it by itself.

When you decide to, press `h`. A fresh session takes over in the same folder, with a full
handover as its first message: the goal, the decisions and why, what is done, what is open
or broken, the gotchas and the next steps. Nothing is written to a file. The handover
comes from one of two places:

- **The session itself** (`Enter`), which knows everything, so this is the most complete.
  It is resumed once in the background, without a tab (`claude -p --resume`), writes the
  handover and exits. Nothing to paste. It reads its whole context back to do so, which
  takes a minute or two for a crowded one.
- **The transcript** (`↓ Enter`), cheaper: Claude (`claude -p --model sonnet`) writes it
  from your messages, Claude's last replies, the files edited and the commits made. It
  cannot know what was never written down, so prefer the first.

An open session is closed on the way: tabmancer closes its Windows Terminal tab, and
Claude Code exits as it does on `/exit`, so you can still resume it later. Closing a tab
works in Windows Terminal only for now; elsewhere, close the session yourself (`/exit`)
and press `h` on it then. Only a session that waits for you is closed. `h` on one that is working queues the handover:
**Open as** says `▶ hand over`, it happens as soon as the session finishes, and `h` again
cancels it. One whose tab cannot be told apart from another of the same title is left
alone: close it yourself (`/exit`), then press `h` again.

To go on with the same work instead, `/compact` in the session's own tab shrinks its
context in place: it fits carrying on, `h` fits a new phase, a closed session, or wanting to
read what is passed on.

A team member handed over (`h` in the team view as well) comes back with the same name and
role, so the rest of the team reaches the new session.
