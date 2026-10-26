"""Regenerate llms/data/fixtures/reply_labels.jsonl and the judge fixture responses
(run: python tests/make_fixture_judge.py). The replies, labels and verdicts below are hand-written,
chosen so the judge disagrees with the labels and flips between orders. Only the keys are computed:
the lines are produced by judge() itself, with a fake client that returns the hand-written verdicts."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import triage_lab as lab

ROOT = lab.DATA_DIR / "fixtures"
T, F = True, False
# id, ticket id, reply, human labels (addresses_issue, tone, no_false_promises)
REPLIES = [
    ("r001", "f001", "Sorry about the double charge. I have refunded the duplicate, you will see it by tomorrow.", (T, T, F)),
    ("r002", "f002", "Thanks for the report. Please send us the note so we can reproduce the crash with tables.", (T, T, T)),
    ("r003", "f004", "No worries, these things happen. Have a great day!", (F, F, T)),
    ("r004", "f007", "Use Forgot password on the login page and we will email you a reset link.", (T, T, T)),
]
# Judge verdicts per order, per reply: (addresses_issue, tone, no_false_promises)
VERDICTS = {
    "after":  {"r001": (T, T, T), "r002": (T, T, T), "r003": (F, F, T), "r004": (F, T, T)},
    "before": {"r001": (T, T, F), "r002": (F, T, T), "r003": (F, F, T), "r004": (T, T, T)},
}


class Hand:
    """Stands in for the API: returns the hand-written verdict for the reply it is asked about."""
    def __init__(self, verdict):
        self.messages = SimpleNamespace(create=self.create)
        self.verdict = verdict

    def create(self, **params):
        out = {c: {"pass": v, "reason": f"Hand-written fixture verdict for {c}."}
               for c, v in zip(lab.RUBRIC_IDS, self.verdict)}
        return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=220, output_tokens=60),
                               content=[SimpleNamespace(type="text", text=json.dumps(out))])


tickets = {t["id"]: t for t in lab.load_tickets(ROOT / "tickets.jsonl")}
labels = [{"id": rid, "ticket_id": tid, "reply": reply, "labels": dict(zip(lab.RUBRIC_IDS, human))}
          for rid, tid, reply, human in REPLIES]
(ROOT / "reply_labels.jsonl").write_text("".join(json.dumps(l, ensure_ascii=False) + "\n" for l in labels), encoding="utf-8")

for order, per_reply in VERDICTS.items():
    path = lab.response_path("judge", lab.JUDGE_MODEL, order, None, ROOT)
    path.unlink(missing_ok=True)
    for l in labels:
        line = lab.judge(tickets[l["ticket_id"]]["text"], l["reply"], order=order, mode="record",
                         client=Hand(per_reply[l["id"]]), root=ROOT, case_id=l["id"])
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line["latency_ms"] = 1200          # the fake client answers instantly; use a plausible number
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")
