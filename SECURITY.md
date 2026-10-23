# Security

Please report a security problem privately, through GitHub's
[private vulnerability reporting](https://github.com/SinanTufekci/tabmancer/security/advisories/new),
not in a public issue. You will get an answer within a week.

What is worth knowing when you look at tabmancer:

- It reads your agents' transcripts and settings under `~/.claude`, `~/.codex` and `~/.gemini`,
  and keeps its own files in `%APPDATA%\tabmancer` (or `~/.config/tabmancer`).
- It starts processes: the `claude`, `codex` and `agy` CLIs (in new terminal tabs, or in the
  background for summaries, handovers and team members), `git status`, and on Windows
  PowerShell for notifications and for finding a session's tab.
- The text of a prompt you write in the dashboard travels on a command line into a new tab;
  `launch.py` quotes it for each shell, and the tests hold every character to arriving intact.
- A team member may run one command without asking you, `tabmancer team add`, and only when
  you let its lead pick the members.
- It has no server and opens no network port.
