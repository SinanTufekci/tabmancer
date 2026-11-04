"""The session cache: unchanged transcripts are not parsed again."""

import json

from tabmancer import sessions as S
from tabmancer.config import config_dir


def write(home, name, prompt="hello", title="Title"):
    project = home / "projects" / "-work-demo"
    project.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "cwd": "/work/demo", "entrypoint": "cli", "message": {"content": prompt}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}],
                                          "stop_reason": "end_turn"}},
        {"type": "ai-title", "aiTitle": title},
        {"type": "cost-state"},
    ]
    path = project / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n",
                    encoding="utf-8")
    return path


def counting_reads(monkeypatch):
    calls = []
    real = S.read_session

    def counted(path, strict=False):
        calls.append(path.stem)
        return real(path, strict)

    monkeypatch.setattr(S, "read_session", counted)
    return calls


def test_unchanged_transcripts_come_from_the_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    write(tmp_path, "a")
    write(tmp_path, "b", title="Before")
    calls = counting_reads(monkeypatch)

    first = {s.session_id: s for s in S.find_sessions(quick=True)}
    assert sorted(calls) == ["a", "b"]

    calls.clear()
    second = {s.session_id: s for s in S.find_sessions(quick=True)}
    assert calls == []
    assert second == first  # the cached copy round-trips every field

    write(tmp_path, "b", prompt="changed prompt", title="After")
    calls.clear()
    third = {s.session_id: s for s in S.find_sessions(quick=True)}
    assert calls == ["b"]
    assert third["b"].title == "After"


def test_a_broken_or_old_cache_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    write(tmp_path, "a")
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / S.CACHE_FILE).write_text("{not json", encoding="utf-8")
    assert [s.session_id for s in S.find_sessions(quick=True)] == ["a"]

    (config_dir() / S.CACHE_FILE).write_text(json.dumps({"stamp": "0:old", "entries": {
        str(tmp_path / "projects" / "-work-demo" / "a.jsonl"): {"size": 1, "mtime": 1, "session": None}}}),
        encoding="utf-8")
    assert [s.session_id for s in S.find_sessions(quick=True)] == ["a"]


def test_running_state_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    write(tmp_path, "a")
    monkeypatch.setattr(S, "_agents_json", lambda: [{"sessionId": "a", "kind": "interactive"}])
    assert S.find_sessions()[0].status is S.Status.RUNNING
    monkeypatch.setattr(S, "_agents_json", list)
    assert S.find_sessions()[0].status is S.Status.DONE
