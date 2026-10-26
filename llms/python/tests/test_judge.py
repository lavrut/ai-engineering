"""Tests for sections 6 and 7: the LLM judge and judge agreement. Expected numbers are in
data/fixtures/expected_scores.json, worked out by hand from the fixture files."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import triage_lab as lab

FIXTURES = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected_scores.json").read_text(encoding="utf-8"))
TICKETS = {t["id"]: t for t in lab.load_tickets(FIXTURES / "tickets.jsonl")}
LABELS = lab.load_jsonl(FIXTURES / "reply_labels.jsonl")
CRITERIA = lab.RUBRIC_IDS


def judged(order):
    """Judge every fixture reply in one order (replay over the saved fixture lines)."""
    return {r["id"]: lab.judge(TICKETS[r["ticket_id"]]["text"], r["reply"], order=order, mode="replay", root=FIXTURES)
            for r in LABELS}


def column(per_reply, criterion):
    return [lab.verdicts(per_reply[r["id"]])[criterion] for r in LABELS]


def test_cohen_kappa_known_values():
    t, f = True, False
    assert lab.cohen_kappa([t, t, f, f], [t, t, f, f]) == 1.0
    assert lab.cohen_kappa([t, t, f, f], [t, f, t, f]) == 0.0
    # po = 3/4; judge says true 3/4, human 1/2, so pe = 3/4*1/2 + 1/4*1/2 = 1/2; kappa = (3/4 - 1/2) / (1 - 1/2)
    assert lab.cohen_kappa([t, t, t, f], [t, t, f, f]) == pytest.approx(0.5)


def test_kappa_degenerate_all_same():
    assert lab.cohen_kappa([True] * 4, [True] * 4) == 1.0    # pe == 1 and po == 1
    assert lab.cohen_kappa([True] * 4, [False] * 4) == 0.0   # pe == 0 here; po == 0


def test_verdicts_null_output():
    assert lab.verdicts({"output": None}) == {c: None for c in CRITERIA}
    out = {c: {"pass": i % 2 == 0, "reason": "x"} for i, c in enumerate(CRITERIA)}
    assert lab.verdicts({"output": out}) == {"addresses_issue": True, "tone": False, "no_false_promises": True}


def test_agreement_and_flips_match_expected():
    before, after = judged("before"), judged("after")
    human = {r["id"]: r["labels"] for r in LABELS}
    for c in CRITERIA:
        judge_v = column(after, c)
        human_v = [human[r["id"]][c] for r in LABELS]
        assert lab.agreement(judge_v, human_v, c) == pytest.approx(EXPECTED["agreement"][c])
        assert lab.cohen_kappa(judge_v, human_v) == pytest.approx(EXPECTED["kappa"][c])
        assert lab.position_flips(column(before, c), judge_v) == EXPECTED["position_flips"][c]


class Capture:
    """A fake client that records the params it was called with."""
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **params):
        self.calls.append(params)
        out = {c: {"pass": True, "reason": "ok"} for c in CRITERIA}
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                               content=[SimpleNamespace(type="text", text=json.dumps(out))])


def test_judge_order_changes_message_and_key():
    client = Capture()
    lines = {o: lab.judge("TICKET TEXT", "REPLY TEXT", order=o, mode="live", client=client, root=FIXTURES)
             for o in ("before", "after")}
    before, after = (c["messages"][0]["content"] for c in client.calls)
    assert before.index("addresses_issue") < before.index("REPLY TEXT")
    assert after.index("addresses_issue") > after.index("REPLY TEXT")
    assert before != after
    assert lines["before"]["key"] != lines["after"]["key"]
    params = client.calls[0]
    assert params["model"] == lab.JUDGE_MODEL and "effort" not in params["output_config"]
    assert "{rubric}" not in params["system"] and "addresses_issue" in params["system"]
    assert not {"temperature", "top_p", "top_k", "tool_choice"} & params.keys()
    with pytest.raises(ValueError):
        lab.judge("t", "r", order="sideways", mode="live", client=client, root=FIXTURES)


def test_judge_schema_has_every_rubric_id():
    rubric_ids = [r["id"] for r in lab.load_prompts(FIXTURES / "prompts.json")["rubric"]]
    assert set(lab.JUDGE_SCHEMA["properties"]) == set(rubric_ids) == set(lab.JUDGE_SCHEMA["required"])
