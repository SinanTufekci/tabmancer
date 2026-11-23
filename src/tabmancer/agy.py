"""Antigravity CLI (agy) sessions, from its conversation index and JSONL transcripts.

agy keeps an index of its conversations in
~/.gemini/antigravity-cli/conversation_summaries.db (SQLite): title, folder, times and
run state. Newer agy builds also write a compact JSONL transcript at
~/.gemini/antigravity-cli/brain/<conversation-id>/.system_generated/logs/transcript.jsonl
with prompts, model names, token counts and tool calls. Both are opened read-only and
never written.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from .config import read_json, write_json
from .sessions import Exit, Session, Status, _shorten

INDEX = "conversation_summaries.db"
USER_REQUEST = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)
TEAM_TAG = re.compile(r"\[tabmancer:([a-z0-9][a-z0-9-]*)\]", re.IGNORECASE)
EDIT_TOOLS = {"edit_file", "write_file", "multi_replace_file_content", "replace_file_content", "write_to_file"}

_transcript_cache: dict[str, tuple[tuple[int, int], dict]] = {}  # path -> ((mtime_ns, size), parsed)


def home() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli"


def transcript_path(session_id: str) -> Path:
    return home() / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"


def _when(value) -> datetime | None:
    """'2026-10-16 11:57:27.4323262+00:00' -> local time; Go writes 7 digits of fraction."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    zone = ""
    for sign in ("+", "-"):
        cut = text.rfind(sign)
        if cut > 10:
            text, zone = text[:cut], text[cut:]
            break
    if "." in text:
        whole, fraction = text.split(".", 1)
        text = f"{whole}.{fraction[:6]}"
    try:
        moment = datetime.fromisoformat(text + zone)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if moment.year < 1980:
        return None  # Go's zero time: never set
    try:
        return moment.astimezone().replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _folder(uris) -> str:
    try:
        first = (json.loads(uris) if isinstance(uris, str) and uris else [None])[0]
    except (ValueError, IndexError, TypeError):
        return ""
    if not isinstance(first, str) or not first.startswith("file:"):
        return ""
    path = unquote(urlparse(first).path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":  # /C:/Users -> C:/Users
        path = path[1:]
    return str(Path(path))


def _clean_user_text(raw: str) -> str:
    """Extract the user's own message from agy's <USER_REQUEST>...</USER_REQUEST> wrapper."""
    found = USER_REQUEST.search(raw)
    return found.group(1).strip() if found else raw.strip()


def team_tag(text: str) -> str:
    """Session name embedded in an agy team member's first prompt ('[tabmancer:shop-ui]'), or ''."""
    found = TEAM_TAG.search(text or "")
    return found.group(1).lower() if found else ""


def _parse_arg_string(value: object) -> str:
    """agy logs tool_call args as JSON-encoded strings (e.g. '\"C:/foo/bar.py\"')."""
    if not isinstance(value, str):
        return ""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            decoded = json.loads(value)
            if isinstance(decoded, str):
                return decoded
        except ValueError:
            pass
    return value


def _read_transcript(path: Path) -> dict:
    """Read key metadata from an agy transcript.jsonl, cached by (mtime_ns, size)."""
    try:
        st = path.stat()
    except OSError:
        return {}
    sig = (st.st_mtime_ns, st.st_size)
    key = str(path)
    hit = _transcript_cache.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]

    info: dict = {
        "started": None,
        "model": "",
        "context": 0,
        "first_prompt": "",
        "last_prompt": "",
        "last_reply": "",
        "files": [],
        "tag": "",
        "busy": False,
        "size": st.st_size,
    }
    files: list[str] = []
    last_type = ""
    last_status = ""
    last_had_tools = False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                when = _when(record.get("created_at"))
                if info["started"] is None and when is not None:
                    info["started"] = when
                rtype = str(record.get("type") or "")
                rstatus = str(record.get("status") or "")
                last_type, last_status = rtype, rstatus
                if rtype == "USER_INPUT" and record.get("source") == "USER_EXPLICIT":
                    text = _clean_user_text(str(record.get("content") or ""))
                    if text:
                        if not info["first_prompt"]:
                            info["first_prompt"] = text
                        if not info["tag"]:
                            info["tag"] = team_tag(text)
                        info["last_prompt"] = text
                elif rtype == "PLANNER_RESPONSE":
                    model = str(record.get("model") or "")
                    if model:
                        info["model"] = model
                    inp = int(record.get("input_tokens") or 0)
                    cached = int(record.get("cache_read_tokens") or 0)
                    if inp + cached > 0:
                        info["context"] = inp + cached
                    content = str(record.get("content") or "").strip()
                    if content:
                        info["last_reply"] = content
                    calls = record.get("tool_calls") if isinstance(record.get("tool_calls"), list) else []
                    last_had_tools = bool(calls)
                    for call in calls:
                        if not isinstance(call, dict):
                            continue
                        if str(call.get("name") or "") in EDIT_TOOLS:
                            args = call.get("args") if isinstance(call.get("args"), dict) else {}
                            target = _parse_arg_string(args.get("Path") or args.get("TargetFile") or args.get("path"))
                            if target and target not in files:
                                files.append(target)
    except OSError:
        return {}
    info["files"] = files[-12:]
    info["busy"] = last_status not in ("", "DONE") or (last_type == "PLANNER_RESPONSE" and last_had_tools) or last_type == "USER_INPUT"
    _transcript_cache[key] = (sig, info)
    return info


# A conversation's steps (conversations/<id>.db, table steps) carry agy's CORTEX_STEP_STATUS_* as a
# number, in the enum's order as agy's binary lists it: 0 unspecified, 1 pending, 2 running, 3 done,
# ... 7 error, 8 generating, 9 waiting, 10 queued. The mapping is read from that order, not from a
# descriptor; the data fits it (a denied permission is 7, a done step 3), and the one step ever seen at
# 9 was an MCP tool call held for an OK in the member's tab that nobody gave (2026-11-20).
STEP_WAITING = 9
STEP_NOT_REACHED = (0, 1, 10)  # unspecified, pending, queued: behind the one held, not yet run
_CONVERSATION_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{7,63}")


STEP_AT_WORK = (2, 8)  # running (a tool, a command), generating (the model writing)


def _newest_step(session_id: str) -> int | None:
    """The status of the agy conversation's newest step that has been reached, from its own db, read
    only; None when there is none or it cannot be read."""
    if not _CONVERSATION_ID.fullmatch(session_id or ""):
        return None  # "agy:<member>" before its conversation is known, or anything odd
    path = home() / "conversations" / f"{session_id}.db"
    if not path.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return None
    try:
        marks = ",".join("?" * len(STEP_NOT_REACHED))
        row = con.execute(f"SELECT status FROM steps WHERE status NOT IN ({marks}) ORDER BY idx DESC LIMIT 1",
                          STEP_NOT_REACHED).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return row[0] if row is not None and isinstance(row[0], int) else None


def waiting_for_you(session_id: str) -> bool:
    """Whether the agy conversation stopped at a step held for your OK (a permission prompt in its
    tab: an MCP tool, a command): its newest step that has been reached is WAITING. Only read, from
    its own db; False when there is none or it cannot be read."""
    return _newest_step(session_id) == STEP_WAITING


def step_state(session_id: str) -> str:
    """What an agy conversation is doing, by its newest reached step: "permission" (held for your
    OK), "busy" (running a tool or the model writing), "" (done, stopped, or not known). One small
    read-only query: the caller keeps it to recently active conversations, as a step left running
    by a killed agy stays "busy" in its db."""
    status = _newest_step(session_id)
    return "permission" if status == STEP_WAITING else "busy" if status in STEP_AT_WORK else ""


def user_events(path: Path) -> list[dict]:
    """User prompts from an agy transcript.jsonl, for team conversation logs."""
    if path.suffix != ".jsonl" or not path.is_file():
        return []
    out: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"USER_INPUT"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "USER_INPUT":
                    continue
                if record.get("source") != "USER_EXPLICIT":
                    continue
                text = _clean_user_text(str(record.get("content") or ""))
                if text:
                    out.append({"kind": "you", "at": _when(record.get("created_at")) or datetime.now(), "text": text})
    except OSError:
        return out
    return out


def digest(session: Session, max_prompts: int = 40, keep_replies: int = 6) -> str:
    """User prompts, last replies and edited files from an agy transcript.jsonl."""
    path = session.path if session.path and session.path.suffix == ".jsonl" else transcript_path(session.session_id)
    if not path.is_file():
        raise FileNotFoundError(f"No readable Antigravity transcript at {path}")
    prompts: list[str] = []
    replies: list[str] = []
    files: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            rtype = str(record.get("type") or "")
            if rtype == "USER_INPUT" and record.get("source") == "USER_EXPLICIT":
                text = _clean_user_text(str(record.get("content") or ""))
                if text:
                    prompts.append(_shorten(text, 300))
            elif rtype == "PLANNER_RESPONSE":
                content = str(record.get("content") or "").strip()
                if content:
                    replies = [*replies[-(keep_replies - 1):], _shorten(content, 600)] if keep_replies > 1 else [
                        _shorten(content, 600)]
                calls = record.get("tool_calls") if isinstance(record.get("tool_calls"), list) else []
                for call in calls:
                    if isinstance(call, dict) and str(call.get("name") or "") in EDIT_TOOLS:
                        args = call.get("args") if isinstance(call.get("args"), dict) else {}
                        target = _parse_arg_string(args.get("Path") or args.get("TargetFile") or args.get("path"))
                        if target and target not in files:
                            files.append(target)
    if len(prompts) > max_prompts:
        prompts = [*prompts[:5], "(…)", *prompts[-(max_prompts - 5):]]
    parts = [f"Title: {session.title}", f"Folder: {session.cwd}",
             f"How it ended: {session.status.value}", "", "User messages, oldest first:"]
    parts += [f"- {p}" for p in prompts]
    if replies:
        parts += ["", "Antigravity's last replies:", *[f"- {r}" for r in replies]]
    if files:
        parts += ["", "Files edited:", ", ".join(files[-20:])]
    return "\n".join(parts)


def _rows() -> list[dict]:
    path = home() / INDEX
    if not path.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM conversation_summaries").fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        con.close()


BACKGROUND_DIR = "tabmancer-bg"


def background_dir() -> Path:
    """The folder tabmancer's own background agy calls run in (summarize.ask): a folder of their own,
    so they are told from your conversations by it and never listed."""
    return Path(tempfile.gettempdir()) / BACKGROUND_DIR


def _is_background(folder: str) -> bool:
    if not folder:
        return False
    try:
        return os.path.normcase(os.path.normpath(folder)) == os.path.normcase(os.path.normpath(background_dir()))
    except (TypeError, ValueError):
        return False


def find(days: float = 14) -> list[Session]:
    since = datetime.now() - timedelta(days=days)
    sessions = []
    for row in _rows():
        sid = str(row.get("conversation_id") or "")
        last = _when(row.get("last_modified_time"))
        if not sid or last is None or last < since or row.get("nesting_depth"):
            continue  # nested ones are subagents of another conversation
        folder = _folder(row.get("workspace_uris"))
        if _is_background(folder):
            continue  # tabmancer's own call (a summary, the router), not a conversation of yours
        title = str(row.get("title") or row.get("preview") or "").strip()
        preview = str(row.get("preview") or "").strip()
        status = str(row.get("status") or "")
        tpath = transcript_path(sid)
        meta = _read_transcript(tpath) if tpath.is_file() else {}
        tag = meta.get("tag") or team_tag(preview) or team_tag(title)
        first_prompt = meta.get("first_prompt") or preview or title
        session = Session(
            session_id=sid,
            path=tpath if tpath.is_file() else home() / "conversations" / f"{sid}.db",
            cli="agy",
            cwd=folder,
            title=tag or _shorten(title or "(untitled)", 80),
            first_prompt=_shorten(first_prompt, 300),
            last_prompt=_shorten(meta.get("last_prompt") or "", 300),
            last_reply=_shorten(meta.get("last_reply") or "", 400),
            entrypoint="cli",
            started=meta.get("started"),
            last_active=last,
            exit=Exit.UNKNOWN,
            size=int(meta.get("size") or row.get("step_count") or 0),
            model=str(meta.get("model") or ""),
            context=int(meta.get("context") or 0),
            files=list(meta.get("files") or ()),
        )
        if row.get("killed") or row.get("not_fully_idle"):
            session.status = Status.CUT_OFF  # stopped while it was still working
        elif status.endswith("IDLE"):
            session.status = Status.DONE
        sessions.append(session)
    return sessions


# --- which model an agy session runs on ----------------------------------------------------------
# Never a Claude model through agy, nor a GPT one (agy bills both to its "Claude and GPT models"
# quota): the chain's models in order, each while its quota lasts (Neon, then Hydrogen, unless
# config.json "agy_models" says otherwise), and none when all are out: the user then picks or waits.
# Any other model asked for by name (gemini-4-argon-high, say) is kept as it is. Claude Code's own
# picks (picks.py) are not touched by any of this.
#
# agy says what is left with `agy -p /usage` (1.1.11 and later): no agent turn, no quota spent,
# some 3 s. One tab-separated row per limit, e.g.
#   Gemini 4 Argon<TAB>Gemini 4 Argon Weekly Limit<TAB>37%<TAB>2026-11-25T07:02:59Z
# refresh_quota() reads it (blocking: call it off the UI thread) and keeps it in QUOTA_FILE;
# pick_model() only reads that file, so it is cheap. A run that fails on quota can be noted with
# note_out_of_quota(), which holds until the reset it names.

# The models agy's sessions get, in order, each while its quota lasts: config.json "agy_models" (a
# list of agy model ids), else these. With all of them out of quota there is no model: the one who
# starts the session picks one or waits; nothing else is put in their place.
DEFAULT_CHAIN = ("gemini-4-neon-high", "gemini-4-hydrogen-high")
CHAIN_SETTING = "agy_models"
# The /usage family a model draws on: one named after it ("Gemini 4 Argon" for Argon), else, for a
# Gemini model, "Gemini Models" (Hydrogen: a live 429 on it came with that row at 0%; Neon names no
# row of its own as of agy 1.5.15, 2026-11-23). That family has a five-hour and a weekly window:
# either at 0% is out.
SHARED_FAMILY = "Gemini Models"
MODEL_NAME = re.compile(r"^gemini-[\d.]+-([a-z]+)-(?:minimal|low|medium|high|xhigh)$")
QUOTA_FILE = "agy_quota.json"
STALE = timedelta(hours=2)  # a /usage read older than this says nothing about now
NOTED_FOR = timedelta(hours=5)  # a quota failure noted with no reset time holds this long
USAGE_SINCE = (1, 1, 11)  # older agy hands "/usage" to the model as a prompt, which spends quota
CLAUDE_WORDS = re.compile(r"claude|\b(opus|sonnet|haiku|fable)\b", re.IGNORECASE)
GPT_WORDS = re.compile(r"gpt", re.IGNORECASE)  # gpt-oss-120b, chatgpt-4o; o3/o4 are left alone
REREAD_AFTER = 30.0  # seconds: a quota read this recent serves the next caller too
UNREADABLE = "couldn't read agy's quota"

_version: tuple[int, ...] | None = None  # agy's version once read; a failed read is tried again
_reading = threading.Lock()  # one `agy -p /usage` at a time; the others wait for its answer
_last_read: tuple[float, bool] | None = None  # (time.monotonic() of the last read, whether it worked)


def is_claude(model: str) -> bool:
    """A Claude model by id, alias or display name: claude-opus-5-5-high, sonnet, 'Claude Opus 5.5 (High)'."""
    return bool(CLAUDE_WORDS.search(model or ""))


def barred(model: str) -> bool:
    """A model never run through agy: Claude's, or GPT's (gpt-oss-120b-medium, chatgpt-4o), by any name."""
    return is_claude(model) or bool(GPT_WORDS.search(model or ""))


def installed() -> bool:
    return shutil.which("agy") is not None


def chain() -> tuple[str, ...]:
    """The models agy's sessions get, in order: config.json "agy_models", else DEFAULT_CHAIN. A
    Claude or GPT model in that list is left out (never through agy); a list with none left, or
    no list, gives the default."""
    saved = read_json("config.json")
    listed = saved.get(CHAIN_SETTING) if isinstance(saved, dict) else None
    if isinstance(listed, list):
        models = tuple(dict.fromkeys(model_id(m) for m in listed if isinstance(m, str) and m.strip()))
        models = tuple(m for m in models if m and not barred(m))
        if models:
            return models
    return DEFAULT_CHAIN


def model_id(model: str) -> str:
    """An agy model id from its id or its display name: 'Gemini 4 Neon (High)' -> 'gemini-4-neon-high'."""
    return re.sub(r"[^a-z0-9.]+", "-", (model or "").strip().lower()).strip("-")


def chain_id(model: str) -> str:
    """The chain's id for a model written as its id or its display name ('Gemini 4 Neon (High)'); '' if none."""
    plain = model_id(model)
    return plain if plain in chain() else ""


def name(model: str) -> str:
    """A model's short name: 'gemini-4-neon-high' -> 'Neon', 'gemini-3.8-flash-high' -> 'Flash'; any
    other model as written."""
    found = MODEL_NAME.match(model_id(model))
    return found[1].capitalize() if found else (model or "").strip()


def label(model: str) -> str:
    """An agy model as a list names it: 'gemini-4-neon-high' or 'Gemini 4 Neon (High)' -> 'Neon (High)';
    '' for a model that is not agy's (a Claude one, say)."""
    plain = model_id(model)
    if not MODEL_NAME.match(plain):
        return ""
    return f"{name(plain)} ({plain.rsplit('-', 1)[1].capitalize()})"


def chain_words(model: str, models: tuple[str, ...] | None = None) -> str:
    """Where a model stands in the chain, in a few words: said where the model is picked."""
    models = models if models is not None else chain()
    if model not in models:
        return "picked by you, outside the chain"
    at = models.index(model)
    if at == 0:
        return "the first choice, while its quota lasts"
    return f"when {' and '.join(name(m) for m in models[:at])} {'is' if at == 1 else 'are'} out of quota"


def chain_line(models: tuple[str, ...] | None = None) -> str:
    """'Neon while its quota lasts, then Hydrogen; both out: you pick one or wait'."""
    models = models if models is not None else chain()
    names = [name(m) for m in models]
    first = f"{names[0]} while its quota lasts" + "".join(f", then {n}" for n in names[1:])
    return f"{first}; {'both' if len(names) == 2 else 'all'} out: you pick one or wait"


class NoModel(OSError):
    """No agy model to start with: every model of the chain is out of quota. An OSError so that
    each place that opens tabs says it, as it says a terminal that would not start."""


def _utc(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        found = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


PERCENT = re.compile(r"^\s*(<)?\s*(\d+(?:[.,]\d+)?)\s*%?\s*$")


def _percent(value) -> float | None:
    """'37%' -> 37.0, '37,5%' -> 37.5, '<1%' -> 0.5 (some left, under one); None when it is no share."""
    found = PERCENT.match(str(value))
    if not found:
        return None
    number = float(found[2].replace(",", "."))
    return number / 2 if found[1] else number


def _fields(line: str) -> list[str]:
    """A /usage line's fields: split at tabs, or else at runs of two spaces or more."""
    parts = line.split("\t") if "\t" in line else re.split(r"\s{2,}", line.strip())
    return [part.strip() for part in parts]


def parse_usage(stdout: str) -> list[list[str]]:
    """agy's /usage table as [family, limit, remaining, resets] rows; lines that are not one are left out."""
    rows = []
    for line in (stdout or "").splitlines():
        fields = _fields(line)
        if len(fields) >= 3 and fields[0] and _percent(fields[2]) is not None:
            rows.append([fields[0], fields[1], fields[2], fields[3] if len(fields) > 3 else ""])
    return rows


def _families(model: str, rows: list) -> set[str]:
    """The /usage families a model draws on, as the rows name them: a family named after the model
    ('Gemini 4 Argon'), else the shared one for a Gemini model; none for another model."""
    plain = model_id(model)
    short = name(plain).lower()
    named = {str(row[0]).strip() for row in rows if row and MODEL_NAME.match(plain)
             and re.search(rf"\b{re.escape(short)}\b", str(row[0]).lower())}
    if named:
        return named
    return {SHARED_FAMILY} if plain.startswith("gemini") else set()


def load_quota() -> dict:
    """What is known of agy's quota, as saved in agy_quota.json; every key is always there:

    - 'read_at': str, UTC ISO time of the last /usage read that gave a table; '' when none. A read
      older than STALE (2 h) says nothing about now.
    - 'rows': list of [family, limit, remaining, resets, ...] as /usage printed them, all str:
      ["Gemini 4 Argon", "Gemini 4 Argon Weekly Limit", "37%", "2026-11-25T07:02:59Z"]. remaining
      reads with _percent() ("37,5%", "<1%"); _families() says which family a model draws on.
    - 'out': {model id: UTC ISO time it is back}: models noted out of quota (note_out_of_quota).
    - 'unreadable': str, UTC ISO time agy last answered something that was no table; '' otherwise.

    For a front end, standing() and quota_view() turn this into percent left and local times."""
    saved = read_json(QUOTA_FILE)
    saved = saved if isinstance(saved, dict) else {}
    rows, out = saved.get("rows"), saved.get("out")
    rows = rows if isinstance(rows, list) else []
    out = out if isinstance(out, dict) else {}
    return {"read_at": str(saved.get("read_at") or ""),
            "rows": [r for r in rows if isinstance(r, list) and len(r) >= 3],
            "out": {str(k): str(v or "") for k, v in out.items()},
            "unreadable": str(saved.get("unreadable") or "")}


def _save_quota(data: dict) -> None:
    try:
        write_json(QUOTA_FILE, data, indent=1)
    except OSError:
        pass  # a note that is not kept costs one failed run at most


def note_out_of_quota(model: str, resets: datetime | None = None, now: datetime | None = None) -> None:
    """Remember that a model failed on quota, until resets (or NOTED_FOR from now), or until a fresh
    /usage read shows its quota has some left."""
    model_id = chain_id(model) or (model or "").strip().lower()
    if not model_id:
        return
    now = now or datetime.now(timezone.utc)
    data = load_quota()
    data["out"][model_id] = (resets or now + NOTED_FOR).astimezone(timezone.utc).isoformat(timespec="seconds")
    _save_quota(data)


def _captured(argv: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int, str] | None:
    """(exit code, stdout) of a short agy command, or None when it failed to start or ran out of
    time. The time is a hard one: subprocess.run's kills agy alone, and a child of agy still holding
    the pipe kept the read waiting after it (Windows); here the whole tree goes, and the read after
    that waits 2 s at most."""
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False,
                           timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.kill()
        try:
            proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return None
    except (OSError, subprocess.SubprocessError):
        return None


def _agy_version() -> tuple[int, ...]:
    """agy's version, (1, 5, 13); () when it could not be read, which is tried again next time."""
    global _version
    if _version is None:
        exe = shutil.which("agy")
        done = _captured([exe, "--version"], 10) if exe else None
        found = re.search(r"(\d+)\.(\d+)\.(\d+)", done[1]) if done else None
        if found is None:
            return ()
        _version = tuple(int(n) for n in found.groups())
    return _version


def _run_usage(timeout: float) -> str | None:
    """stdout of `agy -p /usage`, or None. Only on an agy that answers it itself (no quota spent)."""
    exe = shutil.which("agy")
    version = _agy_version()
    if not exe or not version or version < USAGE_SINCE:
        return None
    # No shell, and MSYS_NO_PATHCONV: started from Git Bash, "/usage" would become a path and go
    # to the model as a prompt.
    done = _captured([exe, "--print-timeout", "20s", "-p", "/usage"], timeout,
                     env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return done[1] if done and done[0] == 0 else None


def refresh_quota(timeout: float = 25, now: datetime | None = None) -> bool:
    """Read agy's quota now and keep it for pick_model(). Blocks for a few seconds: never on the UI
    thread. False when it could not be read (no agy, too old, or a table it did not recognise).
    Callers at the same moment (the n dialog and the router, say) share one read: the others wait
    for it, no longer than their own timeout, and a read under REREAD_AFTER seconds old is not made
    again."""
    global _last_read
    if not _reading.acquire(timeout=max(timeout, 0)):
        return bool(_last_read and _last_read[1])
    try:
        if _last_read is not None and time.monotonic() - _last_read[0] < REREAD_AFTER:
            return _last_read[1]
        done = _refresh(timeout, now)
        _last_read = (time.monotonic(), done)
        return done
    finally:
        _reading.release()


def _refresh(timeout: float, now: datetime | None) -> bool:
    said = _run_usage(timeout) or ""
    rows = parse_usage(said)
    now = now or datetime.now(timezone.utc)
    data = load_quota()
    if not rows:
        if said.strip():  # agy answered, but not with a table this can read: said where the pick is
            data["unreadable"] = now.isoformat(timespec="seconds")
            _save_quota(data)
        return False
    data["read_at"] = now.isoformat(timespec="seconds")
    data["rows"] = rows
    data["unreadable"] = ""

    def still_out(model: str, until: str) -> bool:
        """A note holds until its time, unless a family named after the model shows some left: the
        shared family having some says nothing of one model's own quota ("Individual quota reached")."""
        if (_utc(until) or now) <= now:
            return False
        own = _families(model, rows) - {SHARED_FAMILY}
        shares = [_percent(remaining) for family, _, remaining, *_ in rows if family.strip() in own]
        return not shares or min(s or 0 for s in shares) <= 0

    data["out"] = {model: until for model, until in data["out"].items() if still_out(model, until)}
    _save_quota(data)
    return True


def _fresh(quota: dict, now: datetime) -> datetime | None:
    """When the saved /usage table was read, if that still says something about now."""
    read_at = _utc(quota["read_at"])
    return read_at if read_at is not None and now - read_at <= STALE else None


def _out_until(model: str, quota: dict, now: datetime) -> datetime | None:
    """When a model is back if it is out of quota now; None when it is not known to be out. Out by
    a note on it (only on it: one model's 429 says nothing of another's quota, so the next in the
    chain is tried), or by any of its family's rows at 0% (a five-hour window and a weekly one each
    count: then every model of that family is out); back once the last of those lifts."""
    backs = []
    for noted, until in quota["out"].items():
        if noted == model:
            back = _utc(until) or now + NOTED_FOR
            if back > now:
                backs.append(back)
    read_at = _fresh(quota, now)
    if read_at is not None:
        families = _families(model, quota["rows"])
        for family, _, remaining, *rest in quota["rows"]:
            if family.strip() in families and _percent(remaining) == 0:
                back = _utc(rest[0] if rest else "") or read_at + STALE
                if back > now:
                    backs.append(back)
    return max(backs) if backs else None


def _left(model: str, quota: dict, now: datetime) -> str:
    """'37% of its week left' from a fresh read, or '' when it is not known."""
    if _fresh(quota, now) is None:
        return ""
    families = _families(model, quota["rows"])
    found = [(limit, remaining) for family, limit, remaining, *_ in quota["rows"] if family.strip() in families]
    if not found:
        return ""
    limit, remaining = min(found, key=lambda row: _percent(row[1]) or 0)
    span = "5 hours" if "five" in limit.lower() else "week" if "week" in limit.lower() else "limit"
    return f"{remaining} of its {span} left"


def _when_back(moment: datetime) -> str:
    return moment.astimezone().strftime("%b %d %H:%M")


def pick_model(usage: dict | None = None, now: datetime | None = None, start: str = "") -> tuple[str, str]:
    """The model an agy session should run on, and why: the first of the chain (chain()) not known
    to be out of quota, from `start` on when given. ('', why) when every one is out ("Neon and
    Hydrogen out until Nov 23 12:16: pick one or wait"): nothing else is put in their place, the
    one who starts the session decides. ('', why) too when there is no agy. Never a Claude or GPT
    model. Reads only the saved quota (refresh_quota() reads agy's), so it is cheap; never raises."""
    if not installed():
        return "", "agy is not installed"
    return _pick(usage, now, start)


def _quota(usage: dict | None) -> dict:
    quota = usage if usage is not None else load_quota()
    return {"read_at": str(quota.get("read_at") or ""), "rows": list(quota.get("rows") or []),
            "out": dict(quota.get("out") or {}), "unreadable": str(quota.get("unreadable") or "")}


def _pick(usage: dict | None, now: datetime | None, start: str) -> tuple[str, str]:
    models = chain()
    try:
        quota = _quota(usage)
        now = now or datetime.now(timezone.utc)
        skipped: list[tuple[str, datetime]] = []
        for model in models[models.index(start) if start in models else 0:]:
            back = _out_until(model, quota, now)
            if back is not None:
                skipped.append((model, back))
                continue
            if skipped:
                gone = "; ".join(f"{name(m)} out of quota until {_when_back(b)}" for m, b in skipped)
                return model, f"{gone}: {name(model)} next"
            left = _left(model, quota, now)
            if left:
                return model, f"{name(model)}: {left}"
            if _fresh(quota, now) is not None:
                return model, f"{name(model)} first"
            unreadable = _utc(quota["unreadable"])
            known = unreadable is not None and now - unreadable <= STALE
            return model, f"{name(model)} first ({UNREADABLE if known else 'quota not read yet'})"
        return "", out_words(skipped)
    except Exception:  # noqa: BLE001 - a guess beats a crash: the chain's first
        return models[0], f"{name(models[0])} first ({UNREADABLE})"


def out_words(skipped: list[tuple[str, datetime]]) -> str:
    """'Neon and Hydrogen out until Nov 23 12:16: pick one or wait', short: a dialog's line holds
    some 80 characters. The time is when the first of them is back."""
    names = [name(m) for m, _ in skipped]
    back = min(b for _, b in skipped)
    who = " and ".join(names) if len(names) <= 2 else "all agy models"
    return f"{who} out until {_when_back(back)}: pick one or wait"


def resolve(model: str = "", usage: dict | None = None, now: datetime | None = None) -> tuple[str, str]:
    """The model an agy session is started with, for what was asked: the one guard on the way to
    `agy --model`. Empty or "auto" gives the chain's model by quota; a chain model is kept (the
    chain goes on from it if that one is out); a Claude or GPT model gives the chain's, with a note
    saying so; any other model is kept as asked (a model typed is the user's choice, Argon too).
    Never "auto". ('', why) when the chain is needed and all of it is out of quota: the caller
    asks, or refuses where it can be seen; it never picks another model."""
    word = (model or "").strip()
    if word and word.lower() != "auto" and not barred(word) and not chain_id(word):
        return word, ""
    asked = chain_id(word)
    picked, why = _pick(usage, now, asked)
    if asked:
        return picked, ("" if picked == asked else why)
    if barred(word):
        # With the chain out, only why: the short line a dialog holds (the model asked for is not run anyway).
        return picked, f"{word} never runs through agy: {name(picked)} instead ({why})" if picked else why
    return picked, why


def no_model(why: str) -> NoModel:
    return NoModel(f"no agy model now: {why}" if why else "no agy model now")


def model_flags(model: str, options: tuple[str, ...] | list[str] = (), session_id: str = "",
                usage: dict | None = None, now: datetime | None = None) -> tuple[list[str], list[str], str]:
    """For an agy command line: (--model flags to add, the options with any --model in them made
    safe, a note when a model was replaced). A --model X or --model=X in the options goes through
    resolve() too. With no model of its own, a new session (no session_id) gets the chain's; a
    resumed one keeps the model its transcript says it ran on while that is one of the chain, else
    (Argon from an older chain, a Claude or GPT one, not known) it gets the chain's. "auto" never
    reaches agy. Raises NoModel when the chain is needed and all of it is out of quota: the tab
    is not opened, and why is said, rather than another model run in its place."""
    opts, notes, found = list(options), [], False
    for index, word in enumerate(opts):
        value = opts[index + 1] if word == "--model" and index + 1 < len(opts) else (
            word.split("=", 1)[1] if word.startswith("--model=") else None)
        if value is None:
            continue
        found = True
        safe, note = resolve(value, usage, now)
        if not safe:
            raise no_model(note)
        notes += [note] if note and (barred(value) or chain_id(value)) else []
        if word == "--model":
            opts[index + 1] = safe
        else:
            opts[index] = f"--model={safe}"
    if found:
        return [], opts, "; ".join(notes)
    word = (model or "").strip()
    if not word and session_id:
        ran_on = str(_read_transcript(transcript_path(session_id)).get("model") or "")
        if ran_on and chain_id(ran_on):
            return [], opts, ""  # it goes on with the model it ran on
        safe, why = resolve("", usage, now)
        if not safe:
            raise no_model(why)
        if not ran_on:
            return ["--model", safe], opts, ""
        said = "never runs through agy" if barred(ran_on) else "is not in the chain"
        return ["--model", safe], opts, f"it ran on {ran_on}, which {said}: {why}"
    safe, note = resolve(word, usage, now)
    if not safe:
        raise no_model(note)
    return ["--model", safe], opts, note if barred(word) or chain_id(word) else ""


# --- what the dashboard shows of agy's quota -------------------------------------------------------
# Read from the saved quota only (load_quota): showing it never runs agy.

def _local(moment: datetime | None) -> datetime | None:
    """A moment as local time without a zone, as the dashboard's other times are."""
    return moment.astimezone().replace(tzinfo=None) if moment is not None else None


def standing(model: str, usage: dict | None = None, now: datetime | None = None) -> dict:
    """How a model stands: {'model', 'name', 'left': percent of its tightest limit left (None when
    not known), 'span': 'week' | '5 hours' | 'limit' | '', 'back': when it is back if it is out of
    quota now, else None}. Only what a fresh read (or a noted failure) says; never runs agy."""
    plain = chain_id(model) or (model or "").strip()
    out = {"model": plain, "name": name(plain), "left": None, "span": "", "back": None}
    try:
        quota = _quota(usage)
        now = now or datetime.now(timezone.utc)
        out["back"] = _local(_out_until(plain, quota, now))
        if _fresh(quota, now) is not None:
            families = _families(plain, quota["rows"])
            found = [(limit, _percent(remaining)) for family, limit, remaining, *_ in quota["rows"]
                     if family.strip() in families]
            if found:
                limit, left = min(found, key=lambda row: row[1] if row[1] is not None else 101)
                out["left"] = left
                out["span"] = "5 hours" if "five" in limit.lower() else "week" if "week" in limit.lower() else "limit"
        if out["back"] is not None:
            out["left"] = 0.0
    except Exception:  # noqa: BLE001 - nothing known beats a crash in a view
        return {**out, "left": None, "span": "", "back": None}
    return out


def quota_view(usage: dict | None = None, now: datetime | None = None) -> dict:
    """agy's quota for the dashboard: {'read_at': local time of the /usage read or None, 'unreadable':
    bool, 'chain': [standing() of each model in chain()], 'pick': the model a new session gets ('' when
    all are out), 'why'};
    {} when agy is not installed."""
    if not installed():
        return {}
    quota = usage if usage is not None else load_quota()
    now = now or datetime.now(timezone.utc)
    pick, why = _pick(quota, now, "")
    read_at = _utc(str(quota.get("read_at") or ""))
    unreadable = _utc(str(quota.get("unreadable") or ""))
    return {"read_at": _local(read_at), "unreadable": unreadable is not None and now - unreadable <= STALE,
            "chain": [standing(model, quota, now) for model in chain()], "pick": pick, "why": why}


def all_out(usage: dict | None = None, now: datetime | None = None) -> datetime | None:
    """When the first model of the chain is back, if every one is out of quota now; else None.
    Reads only the saved quota."""
    try:
        quota = _quota(usage)
        now = now or datetime.now(timezone.utc)
        backs = [_out_until(model, quota, now) for model in chain()]
    except Exception:  # noqa: BLE001 - not known to be out
        return None
    return None if any(back is None for back in backs) else min(b for b in backs if b is not None)


# --- a run that failed on quota ----------------------------------------------------------------
# agy's error for it: "RESOURCE_EXHAUSTED (429): Individual quota reached ... Resets in 4h7m". A 429
# that says "overloaded" (capacity), or a rate limit that names no quota, passes in minutes: not noted.

QUOTA_ERROR = re.compile(r"resource[_ ]exhausted|quota (?:reached|exceeded|exhausted)|individual quota|"
                         r"out of quota", re.IGNORECASE)
RESETS_IN = re.compile(r"resets?\s+in\s+~?\s*((?:\d+(?:[.,]\d+)?\s*(?:d|h|m|s)\w*\s*)+)", re.IGNORECASE)
SPAN = re.compile(r"(\d+(?:[.,]\d+)?)\s*(d|h|m|s)", re.IGNORECASE)
SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def resets_in(text: str) -> timedelta | None:
    """'Resets in 4h7m' -> 4 h 7 min; '1d2h', '~75h', '35m', '45s' too; None when it says no time."""
    found = RESETS_IN.search(text or "")
    if found is None:
        return None
    seconds = sum(float(n.replace(",", ".")) * SECONDS[unit.lower()] for n, unit in SPAN.findall(found[1]))
    return timedelta(seconds=seconds) if seconds > 0 else None


def _quota_model(model: str) -> str:
    """The chain model whose quota a model draws on: its own id for a chain model, by its id or
    display name; the chain's model of the same name at another effort ('Gemini 4 Hydrogen (Low)');
    else its id ('Gemini 3.8 Flash (High)' -> 'gemini-3.8-flash-high')."""
    found = chain_id(model)
    if found:
        return found
    plain = model_id(model)
    for chain_model in chain():
        if name(chain_model).lower() in plain.split("-"):
            return chain_model
    return plain


def note_quota_error(text: str, model: str, now: datetime | None = None) -> datetime | None:
    """For a failed agy run's error text: when it says the model is out of quota, note it (its whole
    family) until the time it gives ("Resets in 4h7m"), or NOTED_FOR when it gives none, and return
    that time (UTC). None, and nothing noted, for any other error (a 429 'overloaded', say). model:
    the one the run used, its id or display name."""
    if not text or not QUOTA_ERROR.search(text) or not (model or "").strip():
        return None
    now = now or datetime.now(timezone.utc)
    back = now + (resets_in(text) or NOTED_FOR)
    note_out_of_quota(_quota_model(model), back, now)
    return back
