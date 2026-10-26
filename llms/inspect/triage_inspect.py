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
API itself. Live mode is documented in the lesson, not run here.

Run (from the repo root, in the venv):
    .venv-inspect/bin/pip install -r llms/inspect/requirements.txt

    # the triage eval, free, from saved responses (add -T split=dev or -T split=test to limit it)
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v1_basic
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v2_rules --epochs 3
    .venv-inspect/bin/inspect eval llms/inspect/triage_inspect.py@triage --model replay/claude-haiku-5-5 -T variant=v3_examples

Live (a paid run): swap `replay/` for `anthropic/` in --model. This costs money.
Check the lab's estimate for the same run first (from llms/python):
    python triage_lab.py run --prompt v2_rules --split all --reps 3 --mode live
It prints the estimate and asks "Make these paid calls? [y/N]"; answering N spends nothing.
The spend cap defaults to --max-cost 10 (dollars).
"""
from __future__ import annotations

import json
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
    GenerateConfig,
    ModelAPI,
    ModelOutput,
    ModelUsage,
    ResponseSchema,
    modelapi,
)
from inspect_ai.scorer import CORRECT, INCORRECT, Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.tool import ToolChoice, ToolInfo
from inspect_ai.util import JSONSchema

from triage_lab import (  # noqa: F401  (later sections use the rest)
    DATA_DIR,
    JUDGE_MODEL,
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
    triage_key,
)

# ── 1. Configuration ────────────────────────────────────────────────────────
INSPECT_VERSION = "0.3.285"        # the version this lesson was written and checked against
REP_HEADER = "x-replay-rep"        # the solver sends the epoch here, so each epoch replays its own rep

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
            raise NotImplementedError("the judge branch arrives with the grader (Task 3)")
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


# ── 4. Solver ───────────────────────────────────────────────────────────────
@solver
def triage_solver(variant: str) -> Solver:
    """The lab's call: the variant's system prompt, the ticket, the lab's JSON schema.
    No temperature, top_p or top_k are set (the lab sets none). The epoch picks the saved rep."""
    system = ChatMessageSystem(content=load_prompts()["triage"][variant])
    schema = ResponseSchema(name="triage", json_schema=JSONSchema.model_validate(TRIAGE_SCHEMA))

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        state.messages.insert(0, system)
        return await generate(state, extra_headers={REP_HEADER: str(state.epoch - 1)}, response_schema=schema)

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
