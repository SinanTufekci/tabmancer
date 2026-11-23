"""The accounts the coding CLIs are signed in with, read from their own files, never over the network:
shown in the dashboard's top bar, beside the plan limits they are about.

Claude Code keeps the account in .claude.json, in CLAUDE_CONFIG_DIR when that is set, in your home
folder otherwise: oauthAccount, with its email, its account and organisation ids, and the
organisation's name and plan. /login writes it again, so a switch to another account shows at the next
look. One email can belong to more than one organisation (a personal one and a Team, say): an account
is told apart by its account id and organisation id together (Account.key), the email is what is shown. Codex keeps its sign-in in auth.json under
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
    # Claude Code's only, from oauthAccount (all "" when not known, as for Codex):
    account_id: str = ""  # accountUuid
    org_id: str = ""      # organizationUuid
    org_name: str = ""    # organizationName
    org_type: str = ""    # organizationType: "claude_max", "claude_team"...

    @property
    def key(self) -> str:
        """What tells this account apart: its account and organisation ids, the email when either
        is not known (an account remembered before they were, or Codex's)."""
        return f"{self.account_id}:{self.org_id}" if self.account_id and self.org_id else self.email

    @property
    def personal(self) -> bool:
        """Signed in with a plan of your own (Pro, Max), not as a seat in an organisation's."""
        return bool(self.org_type) and self.org_type not in ORGANISATIONS

    def org_words(self) -> str:
        """The organisation as shown: 'personal' for your own plan's, else its name."""
        return "personal" if self.personal else self.org_name


PLANS = {"claude_max": "Max", "claude_pro": "Pro", "claude_team": "Team", "claude_enterprise": "Enterprise"}
ORGANISATIONS = {"claude_team", "claude_enterprise"}  # seats in an organisation's plan, not your own


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
    return Account("claude", str(account["emailAddress"]), plan,
                   *(str(account.get(name) or "") for name in
                     ("accountUuid", "organizationUuid", "organizationName", "organizationType")))


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
# tabmancer's own file keeps the accounts it has seen Claude Code signed in with, their email, plan,
# account and organisation ids and organisation's name and type, and nothing more (a sign-in itself
# stays Claude Code's: A runs `claude auth login --email`; the file stays on this computer), where each
# one's sign-in page opens (see browsers; by email, as the page is the same for every organisation),
# and on which of them each session stopped at a usage limit: signed in with another since, it can go
# on. A file written before the ids were kept holds emails only: an email's record takes its ids the
# first time that account is seen signed in, and a limit's email reads as the one account it can be.
KNOWN = "accounts.json"
SAVED = ("account_id", "org_id", "org_name", "org_type")  # an account's record: these, when known


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


def _record(account: Account, when: str) -> dict:
    """An account as accounts.json keeps it: the ids only when known."""
    return {"email": account.email, "plan": account.plan,
            **{name: getattr(account, name) for name in SAVED if getattr(account, name)}, "seen": when}


def _account(entry: dict) -> Account:
    return Account("claude", str(entry["email"]), str(entry.get("plan") or ""),
                   *(str(entry.get(name) or "") for name in SAVED))


def remember(account: Account | None, when: str) -> None:
    """Claude Code's account as it is now, first in the list of those seen (when: an ISO time). Its
    email's record from before the ids were kept is this account's: this one takes its place."""
    if account is None or account.cli != "claude" or "@" not in account.email:
        return
    saved = _known_file()
    seen = [e for e in saved.get("claude", []) if isinstance(e, dict) and e.get("email")]
    if seen and _account(seen[0]) == account:
        return  # as it was: no write every few seconds
    saved["claude"] = [_record(account, when), *(
        e for e in seen if _account(e).key != account.key
        and not (e["email"] == account.email and not _account(e).account_id))]
    _save_known(saved)


def known() -> list[tuple[Account, str]]:
    """The Claude accounts seen signed in, the latest first, each with when it was last seen (ISO)."""
    return [(_account(e), str(e.get("seen") or ""))
            for e in _known_file().get("claude", []) if isinstance(e, dict) and e.get("email")]


def shared_emails() -> set[str]:
    """The emails seen signed in to more than one organisation: shown with the organisation's name."""
    emails = [account.email for account, _ in known()]
    return {email for email in emails if emails.count(email) > 1}


def same(stopped_on: str, account: Account | None) -> bool:
    """Whether a limit's account (a key, or an email kept before the ids were) is this one."""
    return account is not None and stopped_on in (account.key, account.email)


def limited_on() -> dict[str, str]:
    """Session id -> the account (its key) it was signed in with when it stopped at a usage limit.
    An email kept before the ids were reads as the key of the one account seen with that email, and
    stays an email while there is not exactly one."""
    found = _known_file().get("limited", {})
    if not isinstance(found, dict):
        return {}
    keys: dict[str, list[str]] = {}
    for account, _ in known():
        if account.account_id and account.org_id:
            keys.setdefault(account.email, []).append(account.key)
    return {str(k): (keys[str(v)][0] if len(keys.get(str(v), ())) == 1 else str(v)) for k, v in found.items()}


def keep_limited(limited: dict[str, str]) -> None:
    saved = _known_file()
    if saved.get("limited") != limited:
        saved["limited"] = limited
        _save_known(saved)


ANY_OTHER = "*"  # opens_in's key for an account not listed yet


def opens_in() -> dict[str, str]:
    """Email (or ANY_OTHER) -> where its sign-in page opens (a browsers.Place id), as picked in A."""
    found = _known_file().get("opens_in", {})
    return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}


def keep_opens_in(email: str, place: str) -> None:
    saved = _known_file()
    picked = opens_in()
    if picked.get(email) != place:
        saved["opens_in"] = {**picked, email: place}
        _save_known(saved)


def words(account: Account, room: int, org: bool = False) -> str:
    """The account in at most room cells: the email with its plan; the email whole, without the
    plan (the email tells accounts apart); the email cut in the middle; "" when not even that fits.
    With org (an email signed in to more than one organisation), its organisation after the email,
    which goes after the plan does."""
    plan = f" · {account.plan}" if account.plan else ""
    email = account.email
    where = f" · {account.org_words()}" if org and account.org_words() else ""
    for shown in (email + where + plan, email + where):
        if len(shown) <= room:
            return shown
    if len(email) <= room:
        return email
    name, at, host = email.partition("@")
    keep = room - len(at + host) - 1  # what is left of the name, and its "…"
    if at and keep >= 3:
        return f"{name[:keep]}…{at}{host}"
    return email[:room - 1] + "…" if room >= 8 else ""
