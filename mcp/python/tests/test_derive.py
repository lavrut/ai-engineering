"""Tests for derive.py: the derived files, determinism, the recorded=true path (on a fixture), and --check."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import derive  # noqa: E402

DERIVED = derive.DERIVED_DIR
NAMES = ("messages", "tool_budget", "elicitation", "app_snapshot", "injection")


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    return asyncio.run(derive.build_all(tmp_path_factory.mktemp("no-recordings")))


def test_derive_writes_all_files(tmp_path):
    out = tmp_path / "derived"
    assert derive.main(["--out", str(out), "--recordings", str(tmp_path / "none")]) == 0
    assert sorted(p.stem for p in out.glob("*.json")) == sorted(NAMES)
    for p in out.glob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))


def test_messages_have_2026_07_28(built):
    ex = built["messages"]["exchanges"]
    assert [x["method"] for x in ex] == ["server/discover", "tools/list", "tools/call", "resources/read",
                                         "prompts/get"]
    for x in ex:
        assert x["request"]["jsonrpc"] == "2.0" and x["response"] is not None
        assert x["request"]["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert "2026-07-28" in json.dumps(ex[0]["response"])
    assert "untrusted_body" in json.dumps(ex[2]["response"])


def test_tool_budget_counts(built):
    s = built["tool_budget"]["servers"]
    assert s["lumen-read"]["tools"] == 3 and s["lumen-ops"]["tools"] == 1
    for v in s.values():
        assert v["tokens_estimate"] == -(-v["chars"] // 4) > 0
    assert "estimate" in built["tool_budget"]["note"]


def test_elicitation_round_trips(built):
    e = built["elicitation"]
    acc, dec = e["accepted"], e["declined"]
    assert acc["rows_written"] == 1 and dec["rows_written"] == 0
    assert len(acc["exchanges"]) == 2  # ask, then the retry with the answer
    first, retry = acc["exchanges"]
    assert first["response"]["result"]["resultType"] == "input_required"
    assert first["response"]["result"]["requestState"] == derive.SEALED
    assert retry["request"]["params"]["requestState"] == derive.SEALED
    assert dec["exchanges"][-1]["response"]["result"]["isError"] is True
    assert "label not written: the user declined" in json.dumps(dec["exchanges"][-1]["response"])


def test_app_snapshot(built):
    a = built["app_snapshot"]
    assert a["uri"] == "ui://lumen/label.html" and a["mimeType"] == "text/html;profile=mcp-app"
    assert a["html"].startswith("<!doctype html>")
    assert a["sample_tool_input"]["name"] == "label_ticket"


def test_injection_not_recorded_without_recordings(built):
    inj = built["injection"]
    assert inj["recorded"] is False and len(inj["command"]) == 2
    assert all("--mode record" in c and "--scenario capstone" in c for c in inj["command"])


def test_no_volatile_values(built):
    text = json.dumps(built)
    assert "+00:00" not in text and '"time"' not in text


def test_derive_is_deterministic(built, tmp_path):
    again = asyncio.run(derive.build_all(tmp_path))
    assert derive.render(again["messages"]) == derive.render(built["messages"])
    assert derive.render(again["elicitation"]) == derive.render(built["elicitation"])


# ── The recorded=true path, on a TEST FIXTURE (not a real recording) ────────
def _fixture_line(blocks: list[dict]) -> str:
    return json.dumps({"key": "fixture", "response": {"content": blocks, "stop_reason": "tool_use", "usage": {}}})


def test_injection_recorded_path_with_fixture_recordings(tmp_path):
    use = lambda n, i: {"type": "tool_use", "id": "x", "name": n, "input": i}  # noqa: E731
    (tmp_path / "capstone-noguard.jsonl").write_text(
        _fixture_line([use("get_ticket", {"id": "t061"})]) + "\n"
        + _fixture_line([use("label_ticket", {"id": "t061", "label": "bug"})]) + "\n", encoding="utf-8")
    (tmp_path / "capstone-guard.jsonl").write_text(
        _fixture_line([use("get_ticket", {"id": "t061"})]) + "\n"
        + _fixture_line([use("label_ticket", {"id": "t061", "label": "bug"})]) + "\n", encoding="utf-8")
    inj = derive.derive_injection(tmp_path, replay=lambda scenario, guard, d: 0 if guard else 1)
    assert inj["recorded"] is True
    ng, g = inj["no_guard"], inj["guard"]
    assert [c["name"] for c in ng["tool_calls"]] == ["get_ticket", "label_ticket"]
    assert ng["label_ticket_attempted"] and ng["outcome"] == "written" and ng["rows_written"] == 1
    assert g["label_ticket_attempted"] and g["outcome"] == "guard denied" and g["rows_written"] == 0


def test_summarize_not_attempted(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(_fixture_line([{"type": "text", "text": "done"}]) + "\n", encoding="utf-8")
    s = derive.summarize(p, guard=True, rows_written=0)
    assert s["outcome"] == "not attempted" and not s["label_ticket_attempted"]
    assert s["final_answer"] == "done"


# ── --check ─────────────────────────────────────────────────────────────────
def test_committed_files_are_up_to_date():
    assert derive.main(["--check"]) == 0


def test_check_fails_when_stale(tmp_path, capsys):
    assert derive.main(["--check", "--out", str(tmp_path)]) == 1  # nothing there yet
    assert "stale or missing" in capsys.readouterr().err
