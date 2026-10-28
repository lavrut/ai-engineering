#!/usr/bin/env python3
"""
triage_inspect — the triage eval from triage_lab, re-run through Inspect AI.

Inspect is a framework for running evals: it owns the dataset, the solver, the
scorers, epochs and the log. The lab owns the prompts, the saved responses and
the graders. This file connects them.

Replay vs live. In replay (the default) the model is `replay/<model>`: a custom
Inspect model API that answers from the lab's saved responses in llms/data/responses/,
so the run is free and identical every time. It never falls back to a paid call;
a request with no saved answer raises an error naming the lab's record command.
In live mode you pass `--model anthropic/<model>` instead and Inspect calls the
API itself (paid, gated by LUMEN_LIVE_OK below). Live mode is documented in the lesson, not run here.

Run (from the repo root, in the venv):
    .venv-inspect/bin/pip install -r llms/inspect/requirements.txt
    .venv-inspect/bin/pip install -e llms/inspect     # registers the replay provider (the CLI needs it)

    # the triage eval, free, from saved responses (add -T split=dev or -T split=test to limit it)
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v1_basic --log-dir llms/inspect/logs
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v2_rules --epochs 3 --log-dir llms/inspect/logs
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v3_examples --log-dir llms/inspect/logs

    # the judged run: the Opus grader replays too (rep-0 replies on the test split)
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage_judged --model replay/claude-haiku-5-5 --model-role grader=replay/claude-opus-5-5 --log-dir llms/inspect/logs

Live (a paid run): swap `replay/` for `anthropic/` in --model or the grader role. This costs money, and
Inspect has no y/N prompt, so the task refuses any non-replay model unless LUMEN_LIVE_OK=1 is set.
Price the run first (free, calls nothing):
    .venv-inspect/bin/python llms/inspect/summarize.py --estimate
It prints each run's calls and dollars from the lab's PRICES: the v2_rules run over 3 epochs is about $0.08
(worst case $0.20); the judged run adds the Opus grader, about $0.47 (worst case $0.91). If you accept that:
    LUMEN_LIVE_OK=1 .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model anthropic/claude-haiku-5-5 -T variant=v2_rules --epochs 3 --log-dir llms/inspect/logs
There is no run-wide spend cap: Inspect's --cost-limit applies to each sample.
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))   # import the lab from any cwd

import inspect_ai
from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageSystem,
    ChatMessageUser,
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    ResponseSchema,
    get_model,
    modelapi,
)
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, SampleScore, accuracy, metric, scorer, stderr
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolChoice, ToolInfo
from inspect_ai.util import JSONSchema

from triage_lab import (  # noqa: F401  (later sections use the rest)
    DATA_DIR,
    JUDGE_MODEL,
    MAX_TOKENS,
    JUDGE_SCHEMA,
    RUBRIC_IDS,
    TASK_MODEL,
    TRIAGE_SCHEMA,
    ReplayMiss,
    _judge_system,
    judge_key,
    load_jsonl,
    load_prompts,
    load_tickets,
    response_path,
    rubric_text,
    triage_key,
    verdicts,
)

# ── 1. Configuration ────────────────────────────────────────────────────────
INSPECT_VERSION = "0.3.285"        # the version this lesson was written and checked against
REP_HEADER = "x-replay-rep"        # the solver sends the epoch here, so each epoch replays its own rep
LIVE_OK = "LUMEN_LIVE_OK"          # set to 1 to approve a paid (non-replay) model, after pricing the run

if inspect_ai.__version__ != INSPECT_VERSION:
    warnings.warn(f"this lesson was written for inspect-ai {INSPECT_VERSION}, you have "
                  f"{inspect_ai.__version__}; the replay provider may behave differently")


# ── 2. Dataset ──────────────────────────────────────────────────────────────
def dataset(split: str | None = None, root=DATA_DIR) -> list[Sample]:
    """The lab's tickets as Inspect samples: input is the ticket text, target is the true category.
    Priority, split and tags ride along as metadata. split=None means all 60."""
    tickets = load_tickets(Path(root) / "tickets.jsonl")
    return [Sample(input=t["text"], target=t["category"], id=t["id"],
                   metadata={"priority": t["priority"], "split": t["split"], "tags": t["tags"]})
            for t in tickets if split is None or t["split"] == split]


# ── 3. Replay model API ─────────────────────────────────────────────────────
# Inspect asks a "model" for a completion. This one looks the request up in the
# lab's saved responses instead of calling an API. It finds the line by rebuilding
# the lab's key from the request, so a changed prompt or ticket text is a miss, not a wrong answer.
STOP_REASONS = {"end_turn": "stop", "max_tokens": "max_tokens", "refusal": "content_filter"}


def saved_line(kind, model, prompt_version, effort, key, root) -> dict:
    """The latest saved line for `key` (the latest recording wins, as in the lab), or ReplayMiss."""
    path = response_path(kind, model, prompt_version, effort, root)
    if path.exists():
        for line in reversed(load_jsonl(path)):
            if line["key"] == key:
                return line
    raise ReplayMiss(f"no saved response for {kind} / {model} / {prompt_version}. "
                     f"Record it: python triage_lab.py record ...")


REPLY_MARK = "\n\nReply:\n"


def parse_judge_message(text: str, rubric: str) -> tuple[str, str, str]:
    """Recover (ticket, reply, order) from the lab's judge message. The rubric text is known exactly, so find it
    first; then split ticket from reply at the first "\n\nReply:\n" (a reply may itself contain blank lines
    or the word "Reply:"; ticket texts never contain the marker)."""
    rubric_part = "\n\nRubric:\n" + rubric
    # Contrived edge: a "before"-order reply that itself ends with this rubric suffix would parse as "after".
    if not text.startswith("Ticket:\n"):
        raise ReplayMiss(f"unrecognised judge message {text[:40]!r}. Record it: python triage_lab.py record ...")
    body = text[len("Ticket:\n"):]
    if body.endswith(rubric_part):                    # after: ticket, reply, rubric
        order, body = "after", body[: -len(rubric_part)]
        ticket, sep, reply = body.partition(REPLY_MARK)
    elif (rubric_part + REPLY_MARK) in body:          # before: ticket, rubric, reply
        order = "before"
        ticket, _, reply = body.partition(rubric_part + REPLY_MARK)
        sep = REPLY_MARK
    else:
        raise ReplayMiss(f"unrecognised judge message {text[:40]!r}. Record it: python triage_lab.py record ...")
    if not sep:
        raise ReplayMiss(f"judge message has no reply {text[:40]!r}. Record it: python triage_lab.py record ...")
    return ticket, reply, order


def _text(message: ChatMessage) -> str:
    return message.text


@modelapi(name="replay")
class ReplayAPI(ModelAPI):
    """Model `replay/<model>`; `-M root=<data root>` picks the saved data (default: llms/data)."""

    def __init__(self, model_name: str, base_url: str | None = None, api_key: str | None = None,
                 config: GenerateConfig = GenerateConfig(), root: str | Path = DATA_DIR, **model_args) -> None:
        super().__init__(model_name, base_url, api_key, [], config)
        self.root = Path(root)
        self.prompts = load_prompts(self.root / "prompts.json")

    async def generate(self, input: list[ChatMessage], tools: list[ToolInfo],
                       tool_choice: ToolChoice, config: GenerateConfig) -> ModelOutput:
        system = "\n".join(_text(m) for m in input if m.role == "system")
        user = next((_text(m) for m in reversed(input) if m.role == "user"), "")
        rep = int((config.extra_headers or {}).get(REP_HEADER, 0))   # no header means rep 0

        try:
            return self._replay(system, user, rep)
        except ReplayMiss as miss:        # name the case: the rep and the start of the ticket
            raise ReplayMiss(f"{miss} [rep {rep}, ticket {user[:40]!r}]") from None

    def _replay(self, system: str, user: str, rep: int) -> ModelOutput:
        variant = next((v for v, text in self.prompts["triage"].items() if system == text), None)
        if variant is not None:
            key = triage_key(user, variant, self.model_name, None, rep, self.prompts)
            line = saved_line("triage", self.model_name, variant, None, key, self.root)
        elif system == _judge_system(self.prompts):
            ticket, reply, order = parse_judge_message(user, rubric_text(self.prompts["rubric"]))
            key = judge_key(ticket, reply, order, self.model_name, self.prompts)
            line = saved_line("judge", self.model_name, order, None, key, self.root)
        else:
            raise ReplayMiss(f"no saved response for {self.model_name}: unknown system prompt "
                             f"{system[:60]!r}. Record it: python triage_lab.py record ...")
        return self._output(line)

    def _output(self, line: dict) -> ModelOutput:
        """Turn a saved line into Inspect's output: tokens, stop reason, and the JSON as the completion."""
        if line.get("error"):
            raise RuntimeError(f"saved response errored: {line['error']}")
        content = "" if line["output"] is None else json.dumps(line["output"])
        out = ModelOutput.from_content(model=self.model_name, content=content,
                                       stop_reason=STOP_REASONS.get(line["stop_reason"], "unknown"))
        out.usage = ModelUsage(input_tokens=line["input_tokens"], output_tokens=line["output_tokens"],
                               total_tokens=line["input_tokens"] + line["output_tokens"])
        return out


def replay_or_approved(model, flag: str = "--model") -> bool:
    """True for the replay model (free). Any other model, anthropic/ included, pays real money and Inspect
    has no y/N prompt, so it runs only once the learner has priced it and set LUMEN_LIVE_OK=1 (then False)."""
    if str(model).startswith("replay/"):
        return True
    if os.environ.get(LIVE_OK) == "1":
        return False
    raise PermissionError(
        f"{model} is not the replay model: this run makes paid API calls. Price it first (free): "
        f".venv-inspect/bin/python llms/inspect/summarize.py --estimate {flag} {model.name}; "
        f"if you accept the cost, run again with {LIVE_OK}=1 in front of the command")


# ── 4. Solver ───────────────────────────────────────────────────────────────
@solver
def triage_solver(variant: str) -> Solver:
    """The lab's call: the variant's system prompt, the ticket, the lab's JSON schema.
    No temperature, top_p or top_k are set (the lab sets none); max_tokens is the lab's, so the live request is the lab's request. The epoch picks the saved rep."""
    system = ChatMessageSystem(content=load_prompts()["triage"][variant])
    schema = ResponseSchema(name="triage", json_schema=JSONSchema.model_validate(TRIAGE_SCHEMA))

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.messages.insert(0, system)
        # the rep header is the replay provider's test seam: a paid model never sees it
        headers = {REP_HEADER: str(state.epoch - 1)} if replay_or_approved(get_model()) else None
        return await generate(state, extra_headers=headers, response_schema=schema, max_tokens=MAX_TOKENS)

    return solve


# ── 5. Code scorers ─────────────────────────────────────────────────────────
# Category and priority have one right answer, so plain code grades them, as in the lab.
def _field_scorer(field: str):
    async def score(state: TaskState, target: Target) -> Score:
        completion = state.output.completion
        reason = state.output.stop_reason
        try:
            parsed = json.loads(completion) if completion else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):      # no usable reply: wrong, with the reason
            outcome = {"content_filter": "refusal", "max_tokens": "max_tokens"}.get(reason, "invalid_json")
            return Score(value=INCORRECT, answer=completion or None, metadata={"outcome": outcome})
        expected = target.text if field == "category" else state.metadata["priority"]
        ok = parsed.get(field) == expected
        return Score(value=CORRECT if ok else INCORRECT, answer=str(parsed.get(field)),
                     explanation=f"expected {expected}", metadata={"outcome": "ok"})
    return score


@scorer(metrics=[accuracy(), stderr()])
def category_match():
    return _field_scorer("category")


@scorer(metrics=[accuracy(), stderr()])
def priority_match():
    return _field_scorer("priority")


# ── 6. Judge scorer ─────────────────────────────────────────────────────────
# Tone has no exact answer, so a stronger model grades the reply against the rubric, as in the lab.
# The judge is the "grader" model role, so the same task can be pointed at a live model without code changes.
def judge_message(ticket: str, reply: str, rubric: str) -> str:
    """The lab's "after" order: ticket, reply, then the rubric."""
    return f"Ticket:\n{ticket}\n\nReply:\n{reply}\n\nRubric:\n{rubric}"


@metric
def graded_mean():
    """Pass rate over the graded samples only (Inspect hands a dict-valued scorer's metric one key at a time).
    The lab leaves a ticket with no reply and a judge line with no output out of the denominator,
    so a plain mean() (which counts them as fails) is wrong."""
    def compute(scores: list[SampleScore]) -> float:
        graded = [s.score.value for s in scores if s.score.metadata and s.score.metadata["graded"]]
        return sum(graded) / len(graded) if graded else 0.0
    return compute


@scorer(metrics={rid: [graded_mean()] for rid in RUBRIC_IDS})
def rubric_judge():
    """The judge scorer. It reads the rubric from the default prompts.json; the judge request is rebuilt from it,
    so a rubric edited under `-M root` is a loud ReplayMiss, never a wrong answer."""
    prompts = load_prompts()
    rubric = rubric_text(prompts["rubric"])
    schema = ResponseSchema(name="judge", json_schema=JSONSchema.model_validate(JUDGE_SCHEMA))

    async def score(state: TaskState, target: Target) -> Score:
        none = {rid: 0.0 for rid in RUBRIC_IDS}
        try:
            parsed = json.loads(state.output.completion) if state.output.completion else None
        except json.JSONDecodeError:
            parsed = None
        reply = parsed.get("reply") if isinstance(parsed, dict) else None
        if not reply:        # no triage reply to judge: the lab skips these tickets
            return Score(value=none, metadata={"graded": False, "verdicts": dict.fromkeys(RUBRIC_IDS), "reasons": {}})
        grader = get_model(role="grader")
        replay_or_approved(grader, "--grader")   # a paid grader runs only after LUMEN_LIVE_OK=1; price it as the grader
        out = await grader.generate([ChatMessageSystem(content=_judge_system(prompts)),
                                     ChatMessageUser(content=judge_message(state.input_text, reply, rubric))],
                                    config=GenerateConfig(response_schema=schema, max_tokens=MAX_TOKENS))
        try:
            output = json.loads(out.completion) if out.completion else None
        except json.JSONDecodeError:
            output = None
        v = verdicts({"output": output})
        if output is None:   # a judge line with no output: not graded
            return Score(value=none, metadata={"graded": False, "verdicts": v, "reasons": {}})
        return Score(value={rid: 1.0 if v[rid] else 0.0 for rid in RUBRIC_IDS},
                     metadata={"graded": True, "verdicts": v,
                               "reasons": {rid: output[rid]["reason"] for rid in RUBRIC_IDS}})
    return score


# ── 7. The task ─────────────────────────────────────────────────────────────
# The lab command that gives the same numbers (split all; run from llms/python).
LAB_COMMANDS = {
    "v1_basic": "python triage_lab.py run --prompt v1_basic --split all",
    "v2_rules": "python triage_lab.py run --prompt v2_rules --split all --reps 3",
    "v3_examples": "python triage_lab.py run --prompt v3_examples --split all",
    "judge": "python triage_lab.py judge --prompt v2_rules --split test --order after",
}


MAX_ERROR_SHARE = 0.5


@task
def triage(variant: str = "v2_rules", split: str | None = None) -> Task:
    """The lab's triage eval: one prompt variant over a split (None = all 60)."""
    return Task(dataset=dataset(split), solver=triage_solver(variant),
                scorer=[category_match(), priority_match()],
                # The lab leaves an errored call out of the accuracy, but a prompt miss must be loud.
                # Inspect stops the eval on the first sample error by default (too strict for one saved
                # error line); False never stops (hides a miss: every sample would error, yet "success").
                # Inspect cannot treat the two exceptions differently, so we use a threshold: a few
                # errored samples are logged and left out of the accuracy, but if more than half error
                # (an edited prompt makes all of them miss) the eval fails and shows the record hint.
                fail_on_error=MAX_ERROR_SHARE)


@task
def triage_judged() -> Task:
    """The judged run: v2_rules rep-0 replies on the test split, graded by the rubric judge."""
    return Task(dataset=dataset("test"), solver=triage_solver("v2_rules"), scorer=rubric_judge(),
                fail_on_error=MAX_ERROR_SHARE)
