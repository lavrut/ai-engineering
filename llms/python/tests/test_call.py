"""Tests for sections 3-4: the task schema, cache keys and saved-response calls. No API calls."""
import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

import triage_lab as lab

FIXTURES = Path(__file__).resolve().parent.parent.parent / "data" / "fixtures"
ARGS = dict(kind="triage", model="m", effort=None, prompt_version="v1", prompt_text="p", input="hello", rep=0)
GOOD = {"category": "bug", "priority": "high", "reply": "Sorry about that."}


def text(s):
    return SimpleNamespace(type="text", text=s)


def response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=blocks, stop_reason=stop_reason,
                           usage=SimpleNamespace(input_tokens=12, output_tokens=7))


class FakeClient:
    """Stands in for anthropic.Anthropic: returns a canned response or raises."""
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result, self.error = result, error
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return self.result


def ok_client():
    return FakeClient(response([text(json.dumps(GOOD))]))


def call(client, mode="record", root=None, **kw):
    cfg = {k: ARGS[k] for k in ("kind", "model", "effort", "prompt_version")} | {"rep": 0}
    return lab.call_model({"model": "m"}, key=lab.cache_key(**ARGS), config=cfg, case_id="t1",
                          mode=mode, client=client, root=root, **kw)


def test_key_is_stable_and_order_independent():
    k = lab.cache_key(**ARGS)
    assert len(k) == 64 and int(k, 16) >= 0
    assert k == lab.cache_key(**dict(reversed(list(ARGS.items()))))
    vec = json.loads((FIXTURES / "expected_scores.json").read_text(encoding="utf-8"))["key_vector"]
    args = {n: vec[n] for n in ARGS}
    assert lab.cache_key(**args) == vec["key"]


def test_key_changes_with_prompt_text_and_rep():
    base = lab.cache_key(**ARGS)
    assert lab.cache_key(**{**ARGS, "prompt_text": "other"}) != base
    assert lab.cache_key(**{**ARGS, "rep": 1}) != base


def test_response_path_names_the_file(tmp_path):
    p = lab.response_path("triage", "m", "v1", None, root=tmp_path)
    assert p == tmp_path / "triage__m__v1__default.jsonl"
    assert lab.response_path("judge", "m", "before", "low", root=tmp_path).name == "judge__m__before__low.jsonl"


def test_replay_hit_returns_saved_line(tmp_path):
    call(ok_client(), root=tmp_path)
    c = FakeClient()
    line = call(c, mode="replay", root=tmp_path)
    assert line["output"] == GOOD and c.calls == []


def test_replay_miss_raises_with_record_hint(tmp_path):
    with pytest.raises(lab.ReplayMiss) as e:
        call(FakeClient(), mode="replay", root=tmp_path)
    assert "record" in str(e.value) and "t1" in str(e.value) and "triage" in str(e.value)


def test_record_appends_and_replay_then_hits(tmp_path):
    c = ok_client()
    line = call(c, root=tmp_path)
    path = lab.response_path("triage", "m", "v1", None, root=tmp_path)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert (line["input_tokens"], line["output_tokens"], line["error"]) == (12, 7, None)
    assert call(c, mode="replay", root=tmp_path) == line and len(c.calls) == 1


def test_live_does_not_save(tmp_path):
    assert call(ok_client(), mode="live", root=tmp_path)["output"] == GOOD
    assert not list(tmp_path.glob("*"))


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_refusal_and_max_tokens_have_null_output(tmp_path, stop):
    line = call(FakeClient(response([text('{"category":')], stop)), root=tmp_path)
    assert line["output"] is None and line["stop_reason"] == stop


def test_api_error_is_recorded(tmp_path):
    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    line = call(FakeClient(error=err), root=tmp_path)
    assert line["output"] is None and line["error"]


def test_triage_params_have_no_sampling_and_omit_null_effort(tmp_path):
    prompts = lab.load_prompts(FIXTURES / "prompts.json")
    ticket = lab.load_tickets(FIXTURES / "tickets.jsonl")[0]
    c = ok_client()
    lab.triage(ticket, "v1_basic", mode="record", client=c, root=tmp_path, prompts=prompts)
    p = c.calls[0]
    assert "temperature" not in p and "tool_choice" not in p
    assert p["system"] == prompts["triage"]["v1_basic"]
    assert p["messages"] == [{"role": "user", "content": ticket["text"]}]
    assert p["output_config"]["format"]["type"] == "json_schema" and "effort" not in p["output_config"]
    lab.triage(ticket, "v1_basic", effort="low", mode="record", client=c, root=tmp_path, prompts=prompts)
    assert c.calls[1]["output_config"]["effort"] == "low"


def test_fixture_triage_replay_hits_for_every_ticket():
    prompts = lab.load_prompts(FIXTURES / "prompts.json")
    for t in lab.load_tickets(FIXTURES / "tickets.jsonl"):
        for variant, reps in (("v1_basic", [0]), ("v2_rules", [0, 1])):
            for rep in reps:
                line = lab.triage(t, variant, rep=rep, mode="replay", root=FIXTURES / "responses", prompts=prompts)
                assert line["case_id"] == t["id"]
