import json
from datetime import datetime, timezone

from tabmancer import doctor, sessions


def write(home, name, records):
    project = home / "projects" / "-work-demo"
    project.mkdir(parents=True, exist_ok=True)
    (project / f"{name}.jsonl").write_text(
        "\n".join(json.dumps(r, separators=(",", ":")) for r in records) + "\n", encoding="utf-8")


def good(prompt, usage=True):
    reply = {"type": "assistant", "timestamp": datetime.now(timezone.utc).isoformat(),
             "message": {"id": f"msg_{prompt}", "content": [{"type": "text", "text": "ok"}],
                         "stop_reason": "end_turn"}}
    if usage:
        reply["message"]["usage"] = {"input_tokens": 3, "output_tokens": 40}
    return [{"type": "user", "cwd": "/work/demo", "entrypoint": "cli", "message": {"content": prompt}},
            reply, {"type": "ai-title", "aiTitle": prompt}, {"type": "cost-state"}]


def levels(results):
    return [level for level, _ in results]


def test_healthy_transcripts_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):
        write(tmp_path, f"s{i}", good(f"task {i}"))
    results = doctor._transcript_checks(set())
    assert "fail" not in levels(results) and "warn" not in levels(results)


def test_unreadable_endings_point_at_a_format_change(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):  # a prompt, then only record types tabmancer does not know
        write(tmp_path, f"s{i}", [good(f"task {i}")[0], {"type": "brand-new-record"}])
    results = doctor._transcript_checks(set())
    assert "fail" in levels(results)
    assert any("format may have changed" in message for _, message in results)


def test_missing_exit_records_warn_but_open_sessions_do_not_count(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):
        write(tmp_path, f"s{i}", good(f"task {i}")[:3])  # no cost-state: looks killed
    assert "warn" in levels(doctor._transcript_checks(set()))
    assert "warn" not in levels(doctor._transcript_checks({f"s{i}" for i in range(6)}))


def test_replies_without_token_counts_warn(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for i in range(6):
        write(tmp_path, f"s{i}", good(f"task {i}", usage=False))
    results = doctor._transcript_checks(set())
    assert any(level == "warn" and "token" in message for level, message in results)


def test_run_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "checks", lambda: [("ok", "fine"), ("fail", "broken")])
    assert doctor.run() == 1
    assert "✗ broken" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "checks", lambda: [("ok", "fine")])
    assert doctor.run() == 0
    assert sessions  # imported for the conftest stub
