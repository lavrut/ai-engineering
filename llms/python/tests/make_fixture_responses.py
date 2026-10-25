"""Regenerate llms/data/fixtures/responses/ (run: python tests/make_fixture_responses.py).
Outputs below are hand-written, a mix of right and wrong. Only the keys are computed
(with cache_key, from the fixture prompts and tickets) so that replay over fixtures hits."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import triage_lab as lab

ROOT = lab.DATA_DIR / "fixtures"
R = lambda c, p, reply: {"category": c, "priority": p, "reply": reply}
OK = "Thanks for writing in. We are looking into this."

# variant, rep -> {ticket id: output | "refusal" | "error"}
RUNS = {
    ("v1_basic", 0): {  # weak prompt: several wrong answers
        "f001": R("billing", "normal", "Sorry about the double charge. We will check."),
        "f002": R("bug", "normal", OK),
        "f003": R("account", "normal", "Lamentamos el problema. Revisaremos su cuenta."),
        "f004": R("bug", "high", OK),
        "f005": R("billing", "low", "Invoices are under Settings > Billing."),
        "f006": R("bug", "normal", OK),
        "f007": "refusal",
        "f008": "error",
    },
    ("v2_rules", 0): {  # better prompt: mostly right
        "f001": R("billing", "high", "Sorry about the double charge. We are reviewing your billing."),
        "f002": R("bug", "normal", "Thanks for the report. We are looking into the crash with tables."),
        "f003": R("account", "normal", "Lamentamos las molestias. Revisaremos el acceso a su cuenta."),
        "f004": R("outage", "urgent", "We are sorry. We are checking the disruption now."),
        "f005": R("billing", "low", "You can download the May invoice under Settings > Billing."),
        "f006": R("bug", "normal", OK),
        "f007": R("account", "low", "Use Forgot password on the login page to reset it."),
        "f008": R("account", "high", "Sorry about the login trouble. We are checking."),
    },
    ("v2_rules", 1): {  # same prompt, second sample: a few answers flip
        "f001": R("billing", "high", "Sorry about the double charge. We are reviewing your billing."),
        "f002": R("bug", "low", OK),
        "f003": R("account", "normal", "Lamentamos las molestias. Revisaremos el acceso a su cuenta."),
        "f004": R("outage", "urgent", "We are sorry. We are checking the disruption now."),
        "f005": R("billing", "low", "You can download the May invoice under Settings > Billing."),
        "f006": R("bug", "high", "Thanks. We are investigating the search problem."),
        "f007": R("account", "low", "Use Forgot password on the login page to reset it."),
        "f008": R("outage", "urgent", "We are sorry. We are checking the login problem now."),
    },
}

prompts = lab.load_prompts(ROOT / "prompts.json")
tickets = {t["id"]: t for t in lab.load_tickets(ROOT / "tickets.jsonl")}
for (variant, rep), cases in RUNS.items():
    path = lab.response_path("triage", lab.TASK_MODEL, variant, None, ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for tid, out in cases.items():
        t = tickets[tid]
        key = lab.cache_key("triage", lab.TASK_MODEL, None, variant, prompts["triage"][variant], t["text"], rep)
        line = {"key": key,
                "config": {"kind": "triage", "model": lab.TASK_MODEL, "effort": None,
                           "prompt_version": variant, "rep": rep},
                "case_id": tid, "output": out, "stop_reason": "end_turn",
                "input_tokens": 90, "output_tokens": 40, "latency_ms": 600, "error": None}
        if out == "refusal":
            line.update(output=None, stop_reason="refusal", output_tokens=0)
        elif out == "error":
            line.update(output=None, stop_reason=None, input_tokens=0, output_tokens=0, latency_ms=0,
                        error="Connection error.")
        lines.append(json.dumps(line, ensure_ascii=False))
    with path.open("a" if rep else "w", encoding="utf-8") as f:   # rep 1 shares rep 0's file
        f.write("\n".join(lines) + "\n")
