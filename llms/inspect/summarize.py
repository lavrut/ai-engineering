#!/usr/bin/env python3
"""
summarize — run the four canonical replay evals through Inspect, set each result beside the lab's own
number, and write llms/data/inspect_summary.json (the file the lesson reads).

Run (from anywhere, in the venv):
    .venv-inspect/bin/python llms/inspect/summarize.py [--out PATH] [--log-dir DIR]

Exit codes: 0 written, 1 a row did not match the lab, 2 inspect-ai is not installed.
The lab numbers are computed here from the lab's replay functions, never typed in. No API calls.

Price a live run first (free, runs nothing): --estimate [--model anthropic/<model>] [--grader anthropic/<model>]
prints each run's calls and dollars from the lab's PRICES and EST_TOKENS, the same figures the lab's estimate uses.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "python"))     # the lab
sys.path.insert(0, str(HERE))                       # triage_inspect

PIP_HINT = "inspect-ai is not installed. Run: .venv-inspect/bin/pip install -r llms/inspect/requirements.txt"
try:
    from inspect_ai import eval as inspect_eval
    from inspect_ai.log import read_eval_log
except ImportError:
    if __name__ == "__main__":
        print(PIP_HINT)
        raise SystemExit(2)
    raise

import triage_inspect as ti
import triage_lab as lab

DEFAULT_OUT = HERE.parent / "data" / "inspect_summary.json"
DEFAULT_LOG_DIR = HERE / "logs"
MODEL = "replay/claude-haiku-5-5"
GRADER = "replay/claude-opus-5-5"
CLI = "inspect eval llms/inspect/triage_inspect.py"
LOG_DIR_ARG = "--log-dir llms/inspect/logs"

# id, label, variant, epochs
TRIAGE_RUNS = [("v1_basic", "v1_basic, all 60 tickets", "v1_basic", 1),
               ("v2_rules", "v2_rules, 3 epochs, all 60 tickets", "v2_rules", 3),
               ("v3_examples", "v3_examples, all 60 tickets", "v3_examples", 1)]

FRAMEWORK_GIVES = [
    "The dataset and sample format: tickets become samples with an input, a target and metadata.",
    "Epochs and reducers: repeat each sample and combine the repeats into one score.",
    "Scorers with metrics and stderr: accuracy and its standard error come with the scorer.",
    "Logs and inspect view: every run is saved as a log you can open and browse.",
]
WE_STILL_OWN = [
    "The replay keys: what makes a saved response match a request is our code, not Inspect's.",
    "The cost estimate and the spend cap before any paid run.",
    "The judge position check: does the verdict change when the rubric moves before the reply.",
    "Deciding which numbers matter: which metric, which split, and how big a gap counts.",
]


def run_evals(log_dir) -> dict:
    """Run the four canonical replay evals into log_dir; return {run id: EvalLog}."""
    log_dir = str(log_dir)
    logs = {}
    for rid, _, variant, epochs in TRIAGE_RUNS:
        logs[rid] = inspect_eval(ti.triage(variant=variant, split=None), model=MODEL, epochs=epochs,
                                 log_dir=log_dir, display="none")[0]
    logs["judge"] = inspect_eval(ti.triage_judged(), model=MODEL, model_roles={"grader": GRADER},
                                 log_dir=log_dir, display="none")[0]
    return {k: read_eval_log(v.location) for k, v in logs.items()}     # the log file is the record


def _metric(log, scorer: str, name: str) -> float:
    return next(s for s in log.results.scores if s.name == scorer).metrics[name].value


def _row(metric, lab_value, inspect_value, stderr, n) -> dict:
    return {"metric": metric, "lab": round(lab_value, 4), "inspect": round(inspect_value, 4),
            "stderr": round(stderr, 4), "n": n, "match": math.isclose(lab_value, inspect_value, abs_tol=1e-9)}


def _triage_rows(log, variant: str, epochs: int) -> list[dict]:
    tickets = lab.load_tickets()
    by_id = {t["id"]: t for t in tickets}
    lines = lab.run(tickets, lambda t, rep: lab.triage(t, variant, rep=rep), reps=epochs, workers=4)
    expected = lab.report([lab.grade(by_id[l["case_id"]], l) for l in lines])
    return [_row(field, expected[field], _metric(log, f"{field}_match", "accuracy"),
                 _metric(log, f"{field}_match", "stderr"), expected["n"])
            for field in ("category", "priority")]


def _judge_rows(log) -> list[dict]:
    cases = lab.split(lab.load_tickets(), "test")
    rows = []
    expected = {rid: [] for rid in lab.RUBRIC_IDS}
    for t in cases:
        out = lab.triage(t, "v2_rules", rep=0)["output"]
        if out:
            v = lab.verdicts(lab.judge(t["text"], out["reply"], order="after"))
            for rid in lab.RUBRIC_IDS:
                if v[rid] is not None:
                    expected[rid].append(v[rid])
    graded = sum(1 for s in log.samples if s.scores["rubric_judge"].metadata["graded"])
    for rid in lab.RUBRIC_IDS:
        vals = [float(x) for x in expected[rid]]
        n = len(vals)
        mean = sum(vals) / n
        # sample standard error (n - 1), the convention of Inspect's stderr
        se = math.sqrt(sum((x - mean) ** 2 for x in vals) / (n - 1) / n) if n > 1 else 0.0
        row = _row(rid, mean, _metric(log, rid, "graded_mean"), se, graded)
        row["match"] = row["match"] and graded == n
        rows.append(row)
    return rows


def build_summary(log_dir, logs: dict | None = None) -> dict:
    """The summary dict. Runs the evals into log_dir unless `logs` is given."""
    logs = run_evals(log_dir) if logs is None else logs
    runs = []
    for rid, label, variant, epochs in TRIAGE_RUNS:
        extra = f" --epochs {epochs}" if epochs > 1 else ""
        runs.append({"id": rid, "label": label, "task": "triage", "epochs": epochs,
                     "command": f"{CLI}@triage --model {MODEL} -T variant={variant}{extra} {LOG_DIR_ARG}",
                     "lab_command": ti.LAB_COMMANDS[rid],
                     "rows": _triage_rows(logs[rid], variant, epochs)})
    runs.append({"id": "judge", "label": "judge, v2_rules rep-0 replies, 20 test tickets", "task": "triage_judged",
                 "epochs": 1,
                 "command": f"{CLI}@triage_judged --model {MODEL} --model-role grader={GRADER} {LOG_DIR_ARG}",
                 "lab_command": ti.LAB_COMMANDS["judge"], "rows": _judge_rows(logs["judge"])})
    return {"inspect_version": ti.INSPECT_VERSION, "lab": "llms/python/triage_lab.py", "split": "all",
            "runs": runs, "framework_gives": FRAMEWORK_GIVES, "we_still_own": WE_STILL_OWN}


def estimate_lines(model: str = lab.TASK_MODEL, grader: str = lab.JUDGE_MODEL) -> list[str]:
    """The four runs as live runs: calls = tickets x epochs, dollars from the lab's PRICES (ValueError if unpriced)."""
    model, grader = model.removeprefix("anthropic/"), grader.removeprefix("anthropic/")
    tickets = lab.load_tickets()
    n_test = len(lab.split(tickets, "test"))
    rows = [(rid, lab._row("triage", model, variant, None, len(tickets) * epochs))
            for rid, _, variant, epochs in TRIAGE_RUNS]
    rows += [("judge", lab._row("triage", model, "v2_rules", None, n_test)),
             ("judge", lab._row("judge", grader, "after", None, n_test))]
    out = ["Live estimate from the lab's PRICES and EST_TOKENS (nothing is called):"]
    out += [f"  {rid:<12} {r['kind']:<7} {r['model']:<18} {r['calls']:>3} calls  ${r['cost']:.4f}  worst ${r['worst']:.4f}"
            for rid, r in rows]
    out.append(f"Total for all four: ${sum(r['cost'] for _, r in rows):.4f} (worst ${sum(r['worst'] for _, r in rows):.4f}).")
    out.append("If you accept the cost of one run, put LUMEN_LIVE_OK=1 in front of its inspect eval command.")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    ap.add_argument("--estimate", action="store_true", help="print what the runs would cost live, run nothing")
    ap.add_argument("--model", default=lab.TASK_MODEL, help="with --estimate: the live task model")
    ap.add_argument("--grader", default=lab.JUDGE_MODEL, help="with --estimate: the live grader model")
    args = ap.parse_args(argv)
    if args.estimate:
        try:
            print("\n".join(estimate_lines(args.model, args.grader)))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return 0
    args.log_dir.mkdir(parents=True, exist_ok=True)
    summary = build_summary(args.log_dir)
    bad = [(r["id"], row["metric"]) for r in summary["runs"] for row in r["rows"] if not row["match"]]
    if bad:
        print("match is false for: " + ", ".join(f"{i}/{m}" for i, m in bad) + ". Not writing the summary.")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
