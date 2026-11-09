# Crowded sessions and handing over

[← tabmancer](../README.md)

A session whose context fills up gets slow, costly and forgetful. tabmancer shows how full
each one is: a bar and `73%` beside every open session, and the size against the model's window
(200K, or 1M for the models that have it) in the preview. It is worked out the way Claude
Code's own statusline does it, from what the last reply was sent with.

## The context cap

Every reply reads the whole conversation again, from Claude's cache. In a long session that is
where most of a plan's limit goes: over two weeks of one user's many parallel sessions, two thirds
of the usage was the conversation read back, at an average of 474k tokens a reply, against a
seventh for what the model wrote. So the sessions tabmancer starts (`n`, `tabmancer new`, a
handover, a team's members) start with a **context cap**: Claude Code's `--autocompact 300k`,
which keeps the session's window at 300k, compacting the conversation into a summary some 33k
before that. A capped session's bar and preview count against its cap (`240k of a 300k cap`).

The cap narrows what a session keeps word for word: past it, earlier detail is in a summary.
Its files, its `CLAUDE.md` and your memory files are all still there to read again. Pick
another one where it is needed: the **Context** row in `n`, in the new-team form's **Runs** card
and in a team's handover (200k, 300k, 400k, 600k, or Claude Code's own window), `tabmancer new --autocompact 400k`, or `"context_cap": "400k"` (or `"auto"`
for none) in tabmancer's `config.json`; `TABMANCER_CONTEXT_CAP` overrides that.

Claude Code forgets the cap on resume (back to the whole window), so tabmancer gives it again
whenever it resumes or forks the session. A session from before caps, or opened elsewhere, is
given the cap on resume only while its context is well under it (50k at least), so that nothing
is compacted the moment it opens; one past it goes on as it was, and the dashboard suggests a
handover instead.

## Crowded sessions

A session without a cap is crowded past 60% of its window (set `TABMANCER_CONTEXT_WARN` to change
it), or past the cap new sessions get, whichever comes first; a capped one at three quarters of its
cap, before Claude Code compacts it. The dashboard says so, once per session: "web-app has been
going for 6h 12m and its context is 640k, and every reply reads all of it again. /compact in its
tab goes on with the same work; h starts a new phase in a fresh session, and shows what is handed
over." The preview says the same in a line under the context, `/compact: go on · h: new phase`, for
as long as the session stays that full. It never does anything about it by itself.

Claude's cache lasts an hour. A session left longer than that writes all of its context afresh
with the next message, at more than a read costs (97% of one user's replies after an hour's gap
did). The preview says so for a big one: `cache gone after an hour: the next message writes all
640k of it again`, the moment to weigh a handover against going on.

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

A team member handed over (`H` in the team view, or `h` on its row) comes back with the same
name and role, so the rest of the team reaches the new session.

## A whole team

`h` twice in a team's view hands the whole team over. Its open members close (each must be
waiting for you: one at work keeps the team as it is), and its lead is resumed once without a tab
to write two things: the handover for a new lead (the goal, the decisions and why, what each member
did and where its part stands, what is open, the gotchas, the next steps), and the line-up the rest
of the work needs, each member with its part, its files, a model and an effort, as `Ctrl+R` writes
one. You see both: the handover to read, the line-up to change as you like, and the **Context** cap
the new generation starts under (the team's own, `←`/`→` for another). `Ctrl+S` starts the team
again as a new generation of itself, under the same name: its members fresh, then a fresh lead with
the handover as its first message, every one under that cap. The old sessions
stay, resumable, as sessions of their own. `Esc` leaves the team closed; `h` in its view shows the
same handover again without writing it again, and `s` starts the team as it was.
A team of peers has no lead to write it: hand its members over one by one (`H`).
