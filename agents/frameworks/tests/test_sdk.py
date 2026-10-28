"""Tests for the Agent SDK desk. Offline: no CLI subprocess, no proxy, no network, no API key.

The desk runs through the SDK's real `query()` with a fake transport (fake_cli.FakeCLI) in place of the bundled
CLI, so the SDK's own control protocol carries the hooks and the in-process MCP calls. The tools are the real
lumen-read and lumen-ops MCP servers (in memory) opened through desk_core's open_desk_tools, so a write is a real
row in a temporary labels file and the harness guard decides it. The guard's scripted person prints and appends
to the harness's trace.jsonl in the working directory, so every test runs in tmp_path.
"""
import json
import sys
from pathlib import Path

import anyio
import pytest
from claude_agent_sdk import (AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage,
                              query)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "agents" / "frameworks"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sdk_desk as sd  # noqa: E402  (first: it puts agents/desk/python on sys.path)
import desk_core as core  # noqa: E402
import supervisor as sv  # noqa: E402
from desk_core import open_desk_tools  # noqa: E402
from fake_cli import FakeCLI, use  # noqa: E402

T = {t["id"]: t for t in core.TICKETS}
READ = ["classify_complaint", "get_ticket", "search_tickets"]
DENIED = "The user denied this action."     # the harness guard's words (desk_core.run_tool)
LABEL = "mcp__lumen-ops__label_ticket"
GET = "mcp__lumen-read__get_ticket"


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def judge(*failed):
    return json.dumps({r: {"pass": r not in failed, "reason": "fails" if r in failed else "ok"}
                       for r in sv.RUBRIC_IDS}, sort_keys=True)


def agent(worker, task="Do your part."):
    return use("Agent", subagent_type=worker, description=f"{worker} step", prompt=task)


class Fake:
    """A client for the desks: the SDK's query() over a FakeCLI, keeping the options and the transport."""

    def __init__(self, script, **kw):
        self.cli = FakeCLI(script, **kw)
        self.options = None

    def __call__(self, prompt, options):
        self.options = options
        return query(prompt=prompt, options=options, transport=self.cli)


def run_single(script, tmp_path, **kw):
    fake = Fake(script)
    labels = tmp_path / "labels.jsonl"
    run = sd.sdk_single(T[kw.pop("tid", "t001")], client=fake, labels_path=labels, **kw)
    return run, fake, labels


def run_supervisor(script, tmp_path, tid="t001", **kw):
    fake = Fake(script)
    labels = tmp_path / "labels.jsonl"
    run = sd.sdk_supervisor(T[tid], client=fake, labels_path=labels, **kw)
    return run, fake, labels


def passing_review(tid="t001", category="billing"):
    """triage, drafter and a passing reviewer, as main + worker scripts."""
    return {"main": [agent("triage"), agent("drafter"), agent("reviewer")],
            "triage": [json.dumps({"category": category, "priority": "high"})],
            "drafter": [f"Draft for {tid}."], "reviewer": [judge()]}


# ── options: hermetic ───────────────────────────────────────────────────────
ENV = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8790", "ANTHROPIC_API_KEY": "sk-ant-dummy",
       "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4000",
       "CLAUDE_CODE_MAX_RETRIES": "0", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
       "CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS": "1", "CLAUDE_CODE_ENTRYPOINT": "sdk-py"}


def check_env(env):
    """R44: ours exactly, HOME = the config dir, and everything else (inherited or risky) set empty."""
    env = dict(env)
    config_dir = env.pop("CLAUDE_CONFIG_DIR")
    assert env.pop("HOME") == config_dir
    assert {k: env[k] for k in ENV} == ENV
    assert all(v == "" for k, v in env.items() if k not in ENV), {k: v for k, v in env.items() if k not in ENV and v}
    assert set(sd.NEUTRALISE) <= set(env)
    return config_dir


def test_single_options_are_hermetic(tmp_path):
    run, fake, _ = run_single({"main": ["Hello."]}, tmp_path)
    o = fake.options
    assert o.tools == [] and o.setting_sources == [] and o.strict_mcp_config is True
    assert o.model == "claude-haiku-5-5" and o.permission_mode == "dontAsk" and o.max_turns == 8
    config_dir = check_env(o.env)
    assert o.settings == '{"includeGitInstructions": false}' and o.forward_subagent_text is True
    # this run's own empty config and working directories (no git repository), gone afterwards
    assert Path(config_dir).parent == Path(o.cwd).parent and not Path(o.cwd).parent.exists()
    assert o.allowed_tools == ["mcp__lumen-ops__label_ticket", "mcp__lumen-read__classify_complaint",
                               "mcp__lumen-read__get_ticket", "mcp__lumen-read__search_tickets"]
    assert set(o.mcp_servers) == {"lumen-read", "lumen-ops"}
    assert all(c["type"] == "sdk" for c in o.mcp_servers.values())     # in-process: the gated path
    assert o.agents is None
    assert run["reply"] == "Hello." and run["stop"] == "end_turn"


def test_supervisor_options_add_only_the_agent_tool(tmp_path):
    """tools=[] would drop the Agent tool too (verified against the CLI), so the supervisor has exactly Agent."""
    _, fake, _ = run_supervisor({"main": ["Nothing to do."]}, tmp_path)
    o = fake.options
    assert o.tools == ["Agent"] and o.setting_sources == [] and o.permission_mode == "dontAsk"
    check_env(o.env)
    assert o.model == "claude-haiku-5-5" and o.setting_sources == [] and o.strict_mcp_config
    assert o.settings == '{"includeGitInstructions": false}' and o.forward_subagent_text is True
    assert o.allowed_tools == ["Agent", "mcp__lumen-ops__label_ticket", "mcp__lumen-read__classify_complaint",
                               "mcp__lumen-read__get_ticket", "mcp__lumen-read__search_tickets"]
    assert set(o.agents) == {"triage", "research", "drafter", "reviewer"}
    assert o.agents["research"].tools == ["mcp__lumen-read__classify_complaint", "mcp__lumen-read__get_ticket",
                                          "mcp__lumen-read__search_tickets"]
    assert o.agents["research"].maxTurns == 3
    for name in ("triage", "drafter", "reviewer"):
        assert o.agents[name].tools == [] and o.agents[name].maxTurns == 1
    assert all(a.model == "claude-haiku-5-5" and a.background is False for a in o.agents.values())


def test_research_server_exposes_only_read_tools(tmp_path):
    from mcp.client import Client

    async def names(config):
        async with Client(config["instance"]) as c:
            return sorted(t.name for t in (await c.list_tools()).tools)

    with open_desk_tools(("read", "ops"), guard=True, labels_path=tmp_path / "l.jsonl") as (schema, run_tool, _):
        servers = sd.desk_servers(schema, run_tool)
        assert anyio.run(names, servers["lumen-read"]) == READ
        assert anyio.run(names, servers["lumen-ops"]) == ["label_ticket"]
    assert all(t.startswith("mcp__lumen-read__") for t in sd.worker_agents()["research"].tools)


def test_mcp_handler_goes_through_run_tool(tmp_path):
    """The handler is run_tool: an untrusted read through it sets the session's flag (the guard's input)."""
    seen = []
    with open_desk_tools(("read", "ops"), guard=True, labels_path=tmp_path / "l.jsonl") as (schema, run_tool, session):
        def spy(name, args):
            seen.append(name)
            return run_tool(name, args)
        servers = sd.desk_servers(schema, spy)
        from mcp.client import Client

        async def call():
            async with Client(servers["lumen-read"]["instance"]) as c:
                return await c.call_tool("get_ticket", {"id": "t001"})
        result = anyio.run(call)
        assert seen == ["get_ticket"] and not result.is_error and session.read_untrusted.is_set()


# ── the guard hook ──────────────────────────────────────────────────────────
def _pre(hooks, name, args, **who):
    data = {"tool_name": name, "tool_input": args, "tool_use_id": "toolu_x", **who}
    return anyio.run(hooks.pre, data, "toolu_x", {})


def test_guard_hook_denies_a_write_after_an_untrusted_read(tmp_path):
    with open_desk_tools(("read", "ops"), guard=True, labels_path=tmp_path / "l.jsonl") as (_, run_tool, session):
        hooks = sd.DeskHooks("single", sd._new_run(), session, "decline", T["t001"])
        assert _pre(hooks, LABEL, {"id": "t001", "label": "billing"}) == {}     # nothing untrusted read yet
        run_tool("get_ticket", {"id": "t001"})                                  # ticket text enters the context
        out = _pre(hooks, LABEL, {"id": "t001", "label": "billing"})
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert out["hookSpecificOutput"]["permissionDecisionReason"] == DENIED
        accept = sd.DeskHooks("single", sd._new_run(), session, "accept", T["t001"])
        assert _pre(accept, LABEL, {"id": "t001", "label": "billing"}) == {}
        assert _pre(hooks, GET, {"id": "t001"}) == {}                           # a read is never asked


def test_single_first_turn_label_is_denied_r24(tmp_path):
    """R24: the ticket text is in the prompt, so even a first-turn write (no read yet) needs a person's yes."""
    run, fake, labels = run_single({"main": [use(LABEL, id="t001", label="billing"), "Done."]}, tmp_path,
                                   approve="decline", elicit="accept")
    assert rows(labels) == []
    assert run["tool_calls"] == [{"agent": "single", "tool": "label_ticket", "args": {"id": "t001", "label": "billing"},
                                  "is_error": True}]
    assert run["reply"] == "Done." and run["errors"] == []


def test_single_label_written_with_both_yeses(tmp_path):
    run, _, labels = run_single({"main": [use(GET, id="t001"), use(LABEL, id="t001", label="billing"), "Done."]},
                                tmp_path, approve="accept", elicit="accept")
    assert [r["label"] for r in rows(labels)] == ["billing"]
    assert [(t["tool"], t["is_error"]) for t in run["tool_calls"]] == [("get_ticket", False), ("label_ticket", False)]
    assert [r["name"] for r in run["trace"].to_list() if r["kind"] == "tool"] == ["get_ticket", "label_ticket"]


def test_single_unknown_tool_goes_to_errors(tmp_path):
    """R35/R37: a name outside the desk tools is no tool call; it goes to errors with the CLI's text."""
    run, _, _ = run_single({"main": [use("Bash", command="ls"), "Done."]}, tmp_path)
    assert run["tool_calls"] == []
    assert run["errors"] == [{"agent": "single", "tool": "Bash", "args": {"command": "ls"},
                              "result": "PreToolUse:Bash hook error: Unknown tool: Bash"}]


def test_single_turn_cap_r29(tmp_path):
    run, _, _ = run_single({"main": [use(GET, id="t001")] * 8}, tmp_path)
    assert run["stop"] == "max_turns" and run["reply"] == sv.STOPPED
    assert [r for r in run["trace"].to_list() if r["kind"] == "stop"] == [
        {"agent": "single", "kind": "stop", "name": "max_turns", "tokens_in": None, "tokens_out": None, "ms": None}]


def test_replay_miss_raises_proxy_error(tmp_path):
    fake = Fake({"main": []}, result={"subtype": "success", "is_error": True, "api_error_status": 404,
                                      "result": "There's an issue with the selected model"})
    with pytest.raises(core.ProxyError, match="replay miss"):
        sd.sdk_single(T["t001"], client=fake, labels_path=tmp_path / "l.jsonl")


# ── the supervisor ──────────────────────────────────────────────────────────
def test_supervisor_full_path(tmp_path):
    script = {"main": [agent("triage"), agent("research", "Look up the ticket."), agent("drafter"), agent("reviewer"),
                       use(LABEL, id="t001", label="billing"), "Labelled t001."],
              "triage": ['{"category": "billing", "priority": "high"}'],
              "research": [use(GET, id="t001"), "Found t001."],
              "drafter": ["Draft for t001."], "reviewer": [judge()]}
    run, fake, labels = run_supervisor(script, tmp_path, approve="accept", elicit="accept")
    assert [r["label"] for r in rows(labels)] == ["billing"]
    assert [h["worker"] for h in run["handoffs"]] == ["triage", "research", "drafter", "reviewer"]
    assert run["state"] == {"ticket_id": "t001", "category": "billing", "findings": "Found t001.",
                            "draft": "Draft for t001.",
                            "review": {"passed": True, "verdict": {r: True for r in sv.RUBRIC_IDS}}}
    assert run["reply"] == "Draft for t001." and run["stop"] == "end_turn" and run["errors"] == []
    assert run["tool_calls"] == [
        {"agent": "research", "tool": "get_ticket", "args": {"id": "t001"}, "is_error": False},
        {"agent": "supervisor", "tool": "label_ticket", "args": {"id": "t001", "label": "billing"}, "is_error": False}]
    # every Agent call ran in the foreground with the hand-built task message, not the supervisor's text
    assert all(i["run_in_background"] is False for i in fake.cli.agent_inputs)
    prompts = dict(fake.cli.prompts)
    assert prompts["research"].startswith('<ticket id="t001">') and "Your task: Look up the ticket." in prompts["research"]
    assert "<category>billing</category>" in prompts["research"]
    assert "<reply>\nDraft for t001.\n</reply>" in prompts["reviewer"]
    trace = run["trace"].to_list()
    assert {r["kind"] for r in trace} == {"model", "tool", "handoff"}
    assert [r["name"] for r in trace if r["kind"] == "handoff"] == [
        "triage: category", "research: findings", "drafter: draft", "reviewer: review"]
    assert [c["agent"] for c in run["calls"]][:2] == ["supervisor", "triage"]
    assert run["cost_usd"] == 0.0042


def test_label_refused_before_the_review_passes(tmp_path):
    run, _, labels = run_supervisor({"main": [use(LABEL, id="t001", label="billing"), "Stopped."]}, tmp_path,
                                    approve="accept", elicit="accept")
    assert rows(labels) == [] and run["tool_calls"][0]["is_error"] is True


def test_label_beside_the_reviewers_handoff_is_refused(tmp_path):
    """The hand-built "offered in the request that asked for it": the review passed during this response."""
    script = passing_review()
    script["main"] = [agent("triage"), agent("drafter"), [agent("reviewer"), use(LABEL, id="t001", label="billing")],
                      use(LABEL, id="t001", label="billing"), "Done."]
    run, _, labels = run_supervisor(script, tmp_path, approve="accept", elicit="accept")
    assert [t["is_error"] for t in run["tool_calls"]] == [True, False]
    assert len(rows(labels)) == 1


def test_label_bound_to_state_r27(tmp_path):
    script = passing_review("t061", "how_to")
    script["main"] += [use(LABEL, id="t001", label="outage"), use(LABEL, id="t061", label="outage"), "Stopped."]
    run, _, labels = run_supervisor(script, tmp_path, tid="t061", approve="accept", elicit="accept")
    assert rows(labels) == [] and [t["is_error"] for t in run["tool_calls"]] == [True, True]
    assert run["reply"] == "Draft for t061."


def test_new_category_clears_the_review_r30(tmp_path):
    script = passing_review()
    script["main"] += [agent("triage"), use(LABEL, id="t001", label="account"), "Stopped."]
    script["triage"].append('{"category": "account", "priority": "high"}')
    run, _, labels = run_supervisor(script, tmp_path, approve="accept", elicit="accept")
    assert run["handoffs"][-1]["changed"] == {"category": "account", "review": None}
    assert rows(labels) == [] and run["tool_calls"][-1]["is_error"] is True


def test_supervisor_r24_label_needs_a_person(tmp_path):
    script = passing_review()
    script["main"] += [use(LABEL, id="t001", label="billing"), "Done."]
    run, _, labels = run_supervisor(script, tmp_path, approve="decline", elicit="accept")
    assert rows(labels) == [] and run["tool_calls"] == [
        {"agent": "supervisor", "tool": "label_ticket", "args": {"id": "t001", "label": "billing"}, "is_error": True}]


def test_research_cannot_label_and_supervisor_cannot_read(tmp_path):
    script = {"main": [agent("research"), use(GET, id="t001"), "Stopped."],
              "research": [use(LABEL, id="t001", label="outage"), "Nothing."]}
    run, _, labels = run_supervisor(script, tmp_path, tid="t061", approve="accept", elicit="accept")
    assert rows(labels) == []
    assert [(t["agent"], t["tool"], t["is_error"]) for t in run["tool_calls"]] == [
        ("research", "label_ticket", True), ("supervisor", "get_ticket", True)]


def test_research_cannot_label_even_after_a_passed_review(tmp_path):
    script = passing_review()
    script["main"] += [agent("research"), "Stopped."]
    script["research"] = [use(LABEL, id="t001", label="billing"), "Nothing."]
    run, _, labels = run_supervisor(script, tmp_path, approve="accept", elicit="accept")
    assert rows(labels) == [] and run["tool_calls"] == [
        {"agent": "research", "tool": "label_ticket", "args": {"id": "t001", "label": "billing"}, "is_error": True}]


def test_unknown_worker_goes_to_errors_r37(tmp_path):
    """R26: the loop goes on, the state is unchanged. R37: no handoff, an errors entry."""
    run, _, _ = run_supervisor({"main": [agent("billing-bot"), agent("fork"), "Gave up."]}, tmp_path)
    assert run["handoffs"] == [] and run["tool_calls"] == []
    assert [e["args"]["subagent_type"] for e in run["errors"]] == ["billing-bot", "fork"]
    assert run["errors"][0]["tool"] == "Agent" and run["errors"][0]["agent"] == "supervisor"
    assert "Unknown worker: billing-bot" in run["errors"][0]["result"]
    assert run["state"]["category"] is None and run["reply"] == "Gave up."
    assert [r for r in run["trace"].to_list() if r["kind"] == "handoff"] == []


def test_failed_handoffs_are_logged_and_change_nothing(tmp_path):
    script = {"main": [agent("reviewer"), agent("triage"), "Stopped."],
              "triage": ['{"category": "weather", "priority": "high"}']}
    run, fake, _ = run_supervisor(script, tmp_path)
    assert [(h["worker"], h["is_error"], h["changed"]) for h in run["handoffs"]] == [
        ("reviewer", True, {}), ("triage", True, {})]
    assert run["state"]["category"] is None
    assert any("triage handoff failed" in c for c in fake.cli.contexts)
    assert [r["name"] for r in run["trace"].to_list() if r["kind"] == "handoff"] == ["reviewer: error", "triage: error"]


def test_supervisor_turn_cap_r29(tmp_path):
    run, _, _ = run_supervisor({"main": [agent("billing-bot")] * 8}, tmp_path)
    assert run["stop"] == "max_turns" and run["reply"] == sv.STOPPED
    assert [r["name"] for r in run["trace"].to_list() if r["kind"] == "stop"] == ["max_turns"]


# ── the trace from a canned stream ──────────────────────────────────────────
def test_trace_from_a_canned_stream():
    """One response split into two messages (one record), a tool that ran, a subagent's response, a handoff
    record placed at the Agent result, the research cap, and the main cap. Every record has the six keys."""
    stream = [
        AssistantMessage(content=[TextBlock(text="Looking.")], model="claude-haiku-5-5", message_id="m1",
                         usage={"input_tokens": 50, "output_tokens": 1}),
        AssistantMessage(content=[ToolUseBlock(id="t1", name=GET, input={"id": "t001"})], model="claude-haiku-5-5",
                         message_id="m1", usage={"input_tokens": 50, "output_tokens": 1}),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=[{"type": "text", "text": "{}"}], is_error=None)]),
        AssistantMessage(content=[ToolUseBlock(id="a1", name="Agent", input={"subagent_type": "research", "prompt": "x"})],
                         model="claude-haiku-5-5", message_id="m2", usage={"input_tokens": 60}),
        *[AssistantMessage(content=[ToolUseBlock(id=f"r{i}", name=GET, input={"id": "t001"})], model="claude-haiku-5-5",
                           message_id=f"s{i}", parent_tool_use_id="a1", usage=None) for i in range(3)],
        UserMessage(content=[ToolResultBlock(tool_use_id="a1", content=[{"type": "text", "text": "Found."}])]),
        AssistantMessage(content=[ToolUseBlock(id="x1", name="Write", input={"path": "p"})], model="claude-haiku-5-5",
                         message_id="m3"),
        UserMessage(content=[ToolResultBlock(tool_use_id="x1", content="No such tool available: Write", is_error=True)]),
        ResultMessage(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=8,
                      session_id="s"),
    ]
    run = sd._new_run()
    handoff = {"agent": "supervisor", "kind": "handoff", "name": "research: findings", "tokens_in": None,
               "tokens_out": None, "ms": 12}
    sd.trace_from_stream(stream, run, main="supervisor", role="supervise", offered=["Agent", "label_ticket"],
                         durations={"t1": 7}, handoffs={"a1": handoff})
    trace = run["trace"].to_list()
    assert all(list(r) == ["agent", "kind", "name", "tokens_in", "tokens_out", "ms"] for r in trace)
    assert [(r["agent"], r["kind"], r["name"]) for r in trace] == [
        ("supervisor", "model", "claude-haiku-5-5"), ("supervisor", "tool", "get_ticket"),
        ("supervisor", "model", "claude-haiku-5-5"),
        ("research", "model", "claude-haiku-5-5"), ("research", "model", "claude-haiku-5-5"),
        ("research", "model", "claude-haiku-5-5"),
        ("supervisor", "handoff", "research: findings"), ("research", "stop", "max_worker_steps"),
        ("supervisor", "model", "claude-haiku-5-5"), ("supervisor", "stop", "max_turns")]
    assert trace[0]["tokens_in"] == 50 and trace[0]["tokens_out"] is None and trace[0]["ms"] is None
    assert trace[1]["ms"] == 7 and trace[3]["tokens_in"] is None
    assert run["calls"][0] == {"agent": "supervisor", "role": "supervise", "tools": ["Agent", "label_ticket"]}
    assert run["calls"][2] == {"agent": "research", "role": "research", "tools": READ}
    assert run["tool_calls"] == [{"agent": "supervisor", "tool": "get_ticket", "args": {"id": "t001"}, "is_error": False}]
    assert run["errors"] == [{"agent": "supervisor", "tool": "Write", "args": {"path": "p"},
                              "result": "No such tool available: Write"}]


def test_sdk_engine_registered():
    eng = core.ENGINES["sdk"]
    assert eng.single is sd.sdk_single and eng.supervisor is sd.sdk_supervisor


# ── fix round 1: R42 one handoff at a time, R43 hooks fail closed, R44 hermetic env ──
def test_child_env_neutralises_the_parents_risky_variables(tmp_path, monkeypatch):
    """R44: the SDK starts the CLI with {**os.environ (minus CLAUDECODE), CLAUDE_CODE_ENTRYPOINT, **options.env}.
    Seed the parent with what could bypass the proxy or leak, and check the merged child env."""
    risky = {"CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_AUTH_TOKEN": "leak",
             "ANTHROPIC_CUSTOM_HEADERS": "X-Leak: yes", "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
             "ALL_PROXY": "socks5://127.0.0.1:9", "ANTHROPIC_API_URL": "https://elsewhere.invalid",
             "CLAUDE_CODE_OAUTH_TOKEN": "leak", "ANTHROPIC_BASE_URL": "https://elsewhere.invalid",
             "ANTHROPIC_API_KEY": "sk-ant-real-looking", "SOME_TOOL_SETTING": "x", "HOME": "/home/someone"}
    for k, v in risky.items():
        monkeypatch.setenv(k, v)
    _, fake, _ = run_single({"main": ["Hi."]}, tmp_path)
    merged = {**{k: v for k, v in __import__("os").environ.items() if k != "CLAUDECODE"},
              "CLAUDE_CODE_ENTRYPOINT": "sdk-py", **fake.options.env}
    for k in risky:
        if k not in ENV and k != "HOME":
            assert merged[k] == "", k
    assert merged["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8790" and merged["ANTHROPIC_API_KEY"] == "sk-ant-dummy"
    assert merged["HOME"] == merged["CLAUDE_CONFIG_DIR"] and merged["PATH"] == __import__("os").environ["PATH"]


def test_parallel_handoffs_take_one_at_a_time_r42(tmp_path):
    """The CLI runs a response's Agent calls at once. Without R42 a slow reviewer passes draft 1 after a parallel
    drafter wrote draft 2, and draft 2 is labelled unreviewed. Now the second call is refused."""
    script = {"main": [agent("triage"), agent("drafter"), agent("reviewer"),
                       [agent("drafter", "Fix the tone."), agent("reviewer")],
                       use(LABEL, id="t001", label="billing"), "Done."],
              "triage": ['{"category": "billing", "priority": "high"}'],
              "drafter": ["DRAFT1 for t001.", "DRAFT2 for t001."],
              "reviewer": [judge("tone"), {"text": judge(), "delay": 0.3}]}
    fake = Fake(script)
    labels = tmp_path / "labels.jsonl"
    run = sd.sdk_supervisor(T["t001"], client=fake, labels_path=labels, approve="accept", elicit="accept")
    pair = {h["worker"]: h for h in run["handoffs"][3:]}    # the refusal is logged at once, before the drafter ends
    assert pair["drafter"]["is_error"] is False and pair["reviewer"]["is_error"] is True   # the second was refused
    assert pair["drafter"]["changed"] == {"draft": "DRAFT2 for t001.", "review": None} and run["state"]["review"] is None
    assert rows(labels) == [] and run["tool_calls"][-1]["is_error"] is True
    assert run["reply"] == "Done."


def test_a_failing_pre_hook_denies_r43(tmp_path, monkeypatch):
    async def boom(self, *a):
        raise RuntimeError("gate broke")
    monkeypatch.setattr(sd.DeskHooks, "_ops_gate", boom)
    script = passing_review()
    script["main"] += [use(LABEL, id="t001", label="billing"), "Done."]
    run, _, labels = run_supervisor(script, tmp_path, approve="accept", elicit="accept")
    assert rows(labels) == [] and run["tool_calls"][-1]["is_error"] is True


def test_a_failing_worker_check_is_a_failed_handoff(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise TypeError("unexpected")
    monkeypatch.setitem(sv.RUN_WORKER, "triage", broken)
    run, fake, _ = run_supervisor({"main": [agent("triage"), "Stopped."], "triage": ["{}"]}, tmp_path)
    assert run["handoffs"] == [{"worker": "triage", "task": "Do your part.", "is_error": True, "changed": {}}]
    assert run["errors"] == [] and any("TypeError" in c for c in fake.cli.contexts)


def test_a_response_without_a_message_id_cannot_be_placed_r43():
    log = sd.StreamLog("supervisor", "supervise", [])
    log.feed(AssistantMessage(content=[ToolUseBlock(id="l1", name=LABEL, input={})], model="m", message_id=None))
    assert anyio.run(log.response_of, "l1", 0.1) is None
