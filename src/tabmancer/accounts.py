"""The accounts the coding CLIs are signed in with, read from their own files, never over the network:
shown in the dashboard's top bar, beside the plan limits they are about.

Claude Code keeps the account in .claude.json, in CLAUDE_CONFIG_DIR when that is set, in your home
folder otherwise: oauthAccount, with its email and the organisation's plan. /login writes it again,
so a switch to another account shows at the next look. Codex keeps its sign-in in auth.json under
CODEX_HOME (~/.codex): with ChatGPT, an id token whose middle part says the email and the plan (read
for showing only: nothing is checked or sent); otherwise an API key, which has no account to show.
Antigravity keeps its sign-in in the system's credential store, which tabmancer does not read.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import codex


@dataclass(frozen=True)
class Account:
    cli: str      # "claude" or "codex"
    email: str    # what it is signed in as; for an API key, "API key"
    plan: str = ""  # "Max 5x", "Pro", "free"...


PLANS = {"claude_max": "Max", "claude_pro": "Pro", "claude_team": "Team", "claude_enterprise": "Enterprise"}


def claude_file() -> Path:
    """Where Claude Code keeps its settings and account: in CLAUDE_CONFIG_DIR when set (a second
    account's folder, say), and then only there; in the home folder otherwise."""
    folder = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(folder) / ".claude.json" if folder else Path.home() / ".claude.json"


_read_before: dict[Path, tuple[tuple[int, int], dict]] = {}


def _read(path: Path) -> dict:
    """A JSON file as a dict ({} when it is not one), read again only when it changed: .claude.json
    holds a lot besides the account, and the dashboard looks every few seconds."""
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if path in _read_before and _read_before[path][0] == stamp:
            return _read_before[path][1]
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = found if isinstance(found, dict) else {}
    _read_before[path] = (stamp, found)
    return found


def claude() -> Account | None:
    account = _read(claude_file()).get("oauthAccount")
    if not isinstance(account, dict) or not account.get("emailAddress"):
        return None
    plan = PLANS.get(str(account.get("organizationType") or ""), "")
    tier = str(account.get("organizationRateLimitTier") or "")
    for size in ("20x", "5x"):  # Max comes in two sizes, said only by the rate limit tier
        if plan == "Max" and tier.endswith(size):
            plan = f"Max {size}"
    return Account("claude", str(account["emailAddress"]), plan)


def _claims(token: str) -> dict:
    """The middle part of a JSON web token, as it says it: for showing, nothing is verified."""
    try:
        middle = token.split(".")[1]
        found = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    except (IndexError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def codex_account() -> Account | None:
    auth = _read(codex.home() / "auth.json")
    if not auth:
        return None
    tokens = auth.get("tokens") if isinstance(auth.get("tokens"), dict) else {}
    claims = _claims(str(tokens.get("id_token") or ""))
    if claims.get("email"):
        openai = claims.get("https://api.openai.com/auth")
        plan = str((openai if isinstance(openai, dict) else {}).get("chatgpt_plan_type") or "")
        return Account("codex", str(claims["email"]), plan)
    if auth.get("OPENAI_API_KEY"):
        return Account("codex", "API key")
    return None


def signed_in() -> list[Account]:
    """Claude Code's account, then Codex's when its sessions are listed (turned on with ,)."""
    from . import clis

    found = [claude()]
    if "codex" in clis.enabled():
        found.append(codex_account())
    return [account for account in found if account is not None]


# --- the Claude accounts you use (A switches between them) -----------------------------------------
# tabmancer's own file keeps the accounts it has seen Claude Code signed in with, their email and plan
# and nothing more (a sign-in itself stays Claude Code's: A runs `claude auth login --email`), and on
# which of them each session stopped at a usage limit: signed in with another since, it can go on.
KNOWN = "accounts.json"


def _known_file() -> dict:
    from .config import read_json

    saved = read_json(KNOWN)
    return saved if isinstance(saved, dict) else {}


def _save_known(saved: dict) -> None:
    from .config import write_json

    try:
        write_json(KNOWN, saved, indent=2)
    except OSError:
        pass  # a read-only config folder: the list is not kept, nothing else is lost


def remember(account: Account | None, when: str) -> None:
    """Claude Code's account as it is now, first in the list of those seen (when: an ISO time)."""
    if account is None or account.cli != "claude" or "@" not in account.email:
        return
    saved = _known_file()
    seen = [e for e in saved.get("claude", []) if isinstance(e, dict) and e.get("email")]
    if seen and seen[0].get("email") == account.email and seen[0].get("plan") == account.plan:
        return  # as it was: no write every few seconds
    saved["claude"] = [{"email": account.email, "plan": account.plan, "seen": when},
                       *(e for e in seen if e.get("email") != account.email)]
    _save_known(saved)


def known() -> list[tuple[Account, str]]:
    """The Claude accounts seen signed in, the latest first, each with when it was last seen (ISO)."""
    return [(Account("claude", str(e["email"]), str(e.get("plan") or "")), str(e.get("seen") or ""))
            for e in _known_file().get("claude", []) if isinstance(e, dict) and e.get("email")]


def limited_on() -> dict[str, str]:
    """Session id -> the account it was signed in with when it stopped at a usage limit."""
    found = _known_file().get("limited", {})
    return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}


def keep_limited(limited: dict[str, str]) -> None:
    saved = _known_file()
    if saved.get("limited") != limited:
        saved["limited"] = limited
        _save_known(saved)


def words(account: Account, room: int) -> str:
    """The account in at most room cells: the email with its plan; the email whole, without the
    plan (the email tells accounts apart); the email cut in the middle; "" when not even that fits."""
    plan = f" · {account.plan}" if account.plan else ""
    email = account.email
    if len(email) + len(plan) <= room:
        return email + plan
    if len(email) <= room:
        return email
    name, at, host = email.partition("@")
    keep = room - len(at + host) - 1  # what is left of the name, and its "…"
    if at and keep >= 3:
        return f"{name[:keep]}…{at}{host}"
    return email[:room - 1] + "…" if room >= 8 else ""
