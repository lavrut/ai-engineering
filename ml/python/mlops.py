#!/usr/bin/env python3
"""
mlops — the smallest useful run tracker, model registry and promotion gate.

Everything is plain JSON in the repository: one file per run in ml/runs/, and
ml/registry.json with the versions, their stages and an append-only history.
MLflow does the same job at scale (see with_mlflow.py); here every moving part
is visible.

Run (from ml/python):
    python mlops.py runs                          # the run table
    python mlops.py --root /tmp/ml-copy rollback  # any command, on a copy of ml/
    python mlops.py compare RUN_A RUN_B           # what changed between two runs
    python mlops.py register models/v1.json       # path relative to ml/; lands in staging
    python mlops.py promote v1 production         # the old production version is archived
    python mlops.py rollback                      # back to the previous production version
    python mlops.py gate v2                       # candidate vs production on 2023 H1
    python mlops.py retrain                       # train v2, register, gate, promote if it passed

Times: the committed runs and history are dated by the data, not the clock (a run is
dated the day after the last complaint it used), so a rebuild gives the same bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import train

ML_DIR = train.ML_DIR
STAGES = ("staging", "production", "archived")
HOLDOUT = ("2023-01-01", "2023-06-30")   # 2023 H1: neither v1 nor v2 trained on it
FLOOR = 0.5                              # no category's F1 may fall below this


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_after(iso_day: str) -> str:
    """The data-derived time of a step: midnight after the last complaint it used."""
    return (date.fromisoformat(iso_day) + timedelta(days=1)).isoformat() + "T00:00:00Z"


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump_json(data, compact: bool = False) -> str:
    """The one text format: indented for runs and the registry, compact for model files."""
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":")) if compact
            else json.dumps(data, ensure_ascii=False, indent=1)) + "\n"


def write_json_atomic(path: str | Path, data, compact: bool = False) -> None:
    """Write the whole file or nothing. A crash mid-write would leave half a JSON file, so we
    write a temp file in the same directory, flush it to disk, then swap it in with
    os.replace, which is atomic on one filesystem: a reader sees the old file or the new one.
    Last, fsync the directory so the rename itself survives a power cut (POSIX only)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dump_json(data, compact))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)                         # mkstemp makes it private (0600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:                                  # e.g. Windows cannot open a directory
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


# ── 1. Run tracker ──────────────────────────────────────────────────────────
def git_state() -> tuple[str | None, bool | None]:
    """The commit the code came from, and whether the code (ml/python) or the data (ml/data)
    differ from it, counting new untracked files there: an unsaved new script is uncommitted
    code. Files outside them (ml/runs, the registry, models, the rest of the repo) are not
    counted: they are what runs write. Without git both are None: still worth logging.
    The habit this supports: commit the code, then run, then commit the artefacts."""
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=ML_DIR, capture_output=True,
                                  text=True, check=True).stdout.strip()
        return git("rev-parse", "HEAD"), bool(git("status", "--porcelain", "--", "python", "data"))
    except (OSError, subprocess.CalledProcessError):
        return None, None


def settings(**knobs) -> dict:
    """A run's params: the model's own knobs plus the shared feature settings and seed."""
    return {**knobs, "min_df": train.FEATURES["min_df"],
            "max_features": train.FEATURES["max_features"], "seed": train.SEED}


def run_id_for(params: dict, train_window: dict) -> str:
    """A readable id from what makes the run different: logreg-C0.3-2019-2021. Keyed by the
    settings, so rebuilding the committed set gives the same files. Real trackers (MLflow)
    append every run under a fresh id instead; here a rerun that changes a record is refused
    unless asked to replace it."""
    knob = {"logreg": "C", "nb": "alpha"}.get(params.get("model"), "")
    value = f"{params[knob]:g}" if knob in params else ""
    return f"{params.get('model', 'run')}-{knob}{value}-{train_window['from'][:4]}-{train_window['to'][:4]}"


def log_run(params: dict, metrics: dict, model_path: str | Path, data_version: str, *,
            windows: dict | None = None, runs_dir: str | Path = ML_DIR / "runs",
            time: str | None = None, keep_path: bool = True, replace: bool = False) -> str:
    """Save one run: what we set (params), what we got (metrics), which data (its hash),
    which model file (its hash), which code (git commit) and which time windows.
    keep_path=False is for models we throw away: the hash still says what was built.
    An existing run file with different bytes is kept unless replace=True."""
    windows = windows or {"train": {"from": train.SPLITS["train"][0], "to": train.SPLITS["train"][1]}}
    runs_dir = Path(runs_dir)
    run_id = run_id_for(params, windows["train"])
    commit, dirty = git_state()
    path = None
    if keep_path:
        path = Path(os.path.relpath(Path(model_path).resolve(), runs_dir.resolve().parent)).as_posix()
    run = {"run_id": run_id, "time": time or now(), "params": params, "windows": windows,
           "data_version": data_version, "model": {"path": path, "sha256": sha256_file(model_path)},
           "metrics": metrics, "git_commit": commit, "git_dirty": dirty}
    target = runs_dir / f"{run_id}.json"
    if target.exists() and not replace and target.read_text(encoding="utf-8") != dump_json(run):
        raise FileExistsError(f"run {run_id} exists with different contents; pass replace=True "
                              "(CLI: --replace) to overwrite it")
    write_json_atomic(target, run)
    return run_id


def load_run(run_id: str, runs_dir: str | Path = ML_DIR / "runs") -> dict:
    return read_json(Path(runs_dir) / f"{run_id}.json")


def list_runs(runs_dir: str | Path = ML_DIR / "runs") -> list[dict]:
    runs = [read_json(p) for p in Path(runs_dir).glob("*.json")]
    return sorted(runs, key=lambda r: (r["time"], r["run_id"]))


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def compare_runs(a: str | dict, b: str | dict, *, runs_dir: str | Path = ML_DIR / "runs") -> dict:
    """Only what differs: params as [a, b]; numeric metrics as [a, b, b - a]."""
    a, b = (load_run(r, runs_dir) if isinstance(r, str) else r for r in (a, b))
    pa, pb = a["params"], b["params"]
    params = {k: [pa.get(k), pb.get(k)] for k in sorted(pa.keys() | pb.keys()) if pa.get(k) != pb.get(k)}
    ma, mb = _flatten(a["metrics"]), _flatten(b["metrics"])
    metrics = {}
    for k in sorted(ma.keys() | mb.keys()):
        x, y = ma.get(k), mb.get(k)
        if x == y:
            continue
        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y))
        metrics[k] = [x, y, y - x] if numeric else [x, y]
    return {"a": a["run_id"], "b": b["run_id"], "params": params, "metrics": metrics}


# ── 2. Registry ─────────────────────────────────────────────────────────────
def load_registry(registry: str | Path = ML_DIR / "registry.json") -> dict:
    return read_json(registry) if Path(registry).exists() else {"versions": {}, "history": []}


def production_version(data: dict) -> str | None:
    return next((v for v, e in data["versions"].items() if e["stage"] == "production"), None)


def _move(data: dict, version: str, stage: str, action: str, time: str, note: str = "") -> None:
    """Change one version's stage and append the event. History is never edited."""
    entry = data["versions"][version]
    data["history"].append({"time": time, "action": action, "version": version,
                            "from_stage": entry["stage"], "to_stage": stage, "note": note})
    entry["stage"] = stage


def register(model_path: str | Path, *, registry: str | Path = ML_DIR / "registry.json",
             time: str | None = None, run: str | None = None, note: str = "") -> str:
    """Add an exported model file as a new version, in staging. The version name comes from
    the file; the SHA-256 lets anyone check later that the file was not swapped."""
    registry, model_path = Path(registry), Path(model_path)
    model = read_json(model_path)
    version, data = model["version"], load_registry(registry)
    if version in data["versions"]:
        raise ValueError(f"{version} is already registered")
    time = time or now()
    data["versions"][version] = {
        "path": Path(os.path.relpath(model_path.resolve(), registry.resolve().parent)).as_posix(),
        "sha256": sha256_file(model_path), "model": model["model"],
        "trained_on": model["trained_on"], "metrics": model["metrics"],
        "stage": None, "registered": time, "run": run}
    _move(data, version, "staging", "register", time, note)
    write_json_atomic(registry, data)
    return version


def promote(version: str, stage: str, *, registry: str | Path = ML_DIR / "registry.json",
            time: str | None = None, note: str = "") -> None:
    """Move a version to a stage. Only one version is in production: the one it replaces
    is archived first, so a rollback can find it again."""
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}; use one of {', '.join(STAGES)}")
    data, time = load_registry(registry), time or now()
    if version not in data["versions"]:
        raise KeyError(f"unknown version {version!r}")
    if data["versions"][version]["stage"] == stage:
        raise ValueError(f"{version} is already in {stage}")
    current = production_version(data)
    if stage == "production" and current:
        _move(data, current, "archived", "archive", time, f"replaced by {version}")
    _move(data, version, stage, "promote", time, note)
    write_json_atomic(registry, data)


def rollback(*, registry: str | Path = ML_DIR / "registry.json", time: str | None = None) -> str:
    """Put the previous production version back. 'Previous' comes from the history: the last
    version, other than the current one, that was ever promoted to production."""
    data, time = load_registry(registry), time or now()
    current = production_version(data)
    if current is None:
        raise ValueError("no production version to roll back from")
    earlier = [h["version"] for h in data["history"]
               if h["to_stage"] == "production" and h["version"] != current]
    if not earlier:
        raise ValueError(f"no previous production version before {current}")
    previous = earlier[-1]
    _move(data, current, "archived", "rollback", time, f"rolled back to {previous}")
    _move(data, previous, "production", "rollback", time, f"replaces {current}")
    write_json_atomic(registry, data)
    return previous


# ── 3. Promotion gate ───────────────────────────────────────────────────────
def gate(candidate_metrics: dict, production_metrics: dict, *, floor: float = FLOOR) -> dict:
    """Promote only on evidence. Both metric summaries come from the same cases. The candidate
    must beat production's macro-F1 by more than the noise band (1/sqrt(n)), and no
    category's F1 may fall below the floor: a better average must not hide a broken class."""
    n = candidate_metrics["n"]
    if production_metrics["n"] != n:
        raise ValueError("the gate needs both models scored on the same cases")
    band = train.noise_band(n)
    gain = candidate_metrics["macro_f1"] - production_metrics["macro_f1"]
    beats = gain > band
    reasons = [f"macro-F1 {candidate_metrics['macro_f1']:.3f} vs {production_metrics['macro_f1']:.3f}: "
               f"gain {gain:+.3f} {'>' if beats else '<='} band {band:.3f} (n={n})"]
    low = {k: f for k, f in candidate_metrics["per_class_f1"].items() if f < floor}
    reasons += [f"{k}: F1 {f:.3f} is below the floor {floor:.2f}" for k, f in sorted(low.items())]
    if not low:
        reasons.append(f"every category's F1 is at or above the floor {floor:.2f}")
    return {"passed": beats and not low, "gain": gain, "band": band, "floor": floor,
            "reasons": reasons}


def summarize(m: dict, split: str) -> dict:
    """The few numbers a run, a model file and the gate need from per_class_metrics."""
    return {"split": split, "n": m["n"], "accuracy": m["accuracy"], "band": m["band"],
            "macro_f1": m["macro"]["f1"], "weighted_f1": m["weighted"]["f1"],
            "per_class_f1": {k: v["f1"] for k, v in m["per_class"].items()}}


def evaluate(model: dict, rows: list[dict], split: str) -> dict:
    """Score rows from the model JSON alone, exactly as the servers will."""
    pred = [train.score(model, r["narrative"])["label"] for r in rows]
    return summarize(train.per_class_metrics([r["product"] for r in rows], pred, model["classes"]), split)


def holdout_rows(window: tuple[str, str] = HOLDOUT) -> list[dict]:
    rows = train.load_complaints(train.PATHS["complaints"])
    return [r for r in rows if window[0] <= r["date"] <= window[1]]


def run_gate(version: str, root: Path, *, window=HOLDOUT, floor: float = FLOOR,
             time: str | None = None) -> dict:
    """Score the candidate and the production version on the same window, decide, and write
    the decision into the history whatever it is."""
    registry = root / "registry.json"
    data = load_registry(registry)
    current = production_version(data)
    if version not in data["versions"] or current is None:
        raise KeyError(f"need a registered candidate and a production version (got {version!r})")
    rows, split = holdout_rows(window), f"holdout {window[0]}..{window[1]}"
    cand = evaluate(read_json(root / data["versions"][version]["path"]), rows, split)
    prod = evaluate(read_json(root / data["versions"][current]["path"]), rows, split)
    result = {**gate(cand, prod, floor=floor), "candidate": cand, "production": prod,
              "production_version": current}
    note = f"{'passed' if result['passed'] else 'failed'} vs {current}: " + "; ".join(result["reasons"])
    _move(data, version, data["versions"][version]["stage"], "gate", time or now(), note)
    write_json_atomic(registry, data)
    return result


# ── 4. Retrain ──────────────────────────────────────────────────────────────
def experiments(root: Path, *, replace: bool = False) -> list[str]:
    """The experiment set behind v1: Naive Bayes and logistic regression with three values
    of C, all trained on 2019-2021 and scored on 2022. Model files go to a temp dir and the
    runs keep each file's hash, except the run with v1's settings: it is exported exactly as
    train.py exported v1, must reproduce models/v1.json byte for byte, and is logged against
    that file, so the run and the registry name the same SHA-256."""
    rows = train.load_complaints(train.PATHS["complaints"])
    dv, ids = train.data_version(train.PATHS["complaints"]), []
    windows = {"train": dict(zip(("from", "to"), train.SPLITS["train"])),
               "eval": {"name": "test", **dict(zip(("from", "to"), train.SPLITS["test"]))}}
    created = max(r["date"] for r in train.split_by_time(rows)["train"])   # as train.py
    v1 = root / "models" / "v1.json"
    with tempfile.TemporaryDirectory() as tmp:
        for c in (0.3, 1.0, 3.0):
            run = train.fit_and_evaluate(rows, c=c)
            fitted = run["fitted"]
            kinds = {"logreg": {"C": c, "max_iter": train.MAX_ITER}}
            if c == train.C:                         # Naive Bayes ignores C: log it once
                kinds["nb"] = {"alpha": train.NB_ALPHA}
            for kind, knobs in kinds.items():
                params = settings(model=kind, **knobs)
                run_id = run_id_for(params, windows["train"])
                m = run["metrics"][kind]
                is_v1 = kind == "logreg" and c == train.C
                meta = {"version": "v1" if is_v1 else run_id, "created": created, "data_version": dv,
                        "majority_class": fitted["majority_class"], "metrics": summarize(m["test"], "test")}
                path = Path(tmp) / f"{run_id}.json"
                write_json_atomic(path, train.export_model(fitted["models"][kind], fitted["vocabulary"],
                                                           fitted["idf"], meta), compact=True)
                if is_v1:
                    if not v1.exists() or v1.read_bytes() != path.read_bytes():
                        raise ValueError(f"{run_id} does not reproduce {v1}")
                    path = v1
                metrics = {"test": meta["metrics"], "train": summarize(m["train"], "train")}
                ids.append(log_run(params, metrics, path, dv, windows=windows, runs_dir=root / "runs",
                                   time=day_after(windows["eval"]["to"]), keep_path=is_v1,
                                   replace=replace))
    return ids


def retrain(root: Path, *, floor: float = FLOOR, time: str | None = None,
            replace: bool = False) -> dict:
    """The loop the capstone walks through: train v2 on 2019-2022 with v1's settings, register
    it in staging, gate it against production on 2023 H1, and promote only if it passed.
    models/v2.json is kept (and committed) even when the gate fails: a registered version is
    an artefact, and the gate, rollback and "promote anyway" exercises need the file."""
    time = time or day_after(HOLDOUT[1])
    if "v2" in load_registry(root / "registry.json")["versions"]:
        raise ValueError("v2 is already registered; retrain writes it once")
    rows = train.load_complaints(train.PATHS["complaints"])
    splits = train.split_by_time(rows)
    fitted = train.train_models(splits["train"] + splits["test"])
    window = {"from": train.SPLITS["train"][0], "to": train.SPLITS["test"][1]}
    dv = train.data_version(train.PATHS["complaints"])
    meta = {"version": "v2", "created": max(r["date"] for r in splits["train"] + splits["test"]),
            "data_version": dv, "majority_class": fitted["majority_class"], "metrics": {}}
    model = train.export_model(fitted["models"]["logreg"], fitted["vocabulary"], fitted["idf"], meta)
    model["trained_on"] = window                     # export_model assumes the v1 window
    split = f"holdout {HOLDOUT[0]}..{HOLDOUT[1]}"
    model["metrics"] = evaluate(model, holdout_rows(), split)
    path = root / "models" / "v2.json"
    write_json_atomic(path, model, compact=True)

    params = settings(model="logreg", C=train.C, max_iter=train.MAX_ITER)
    run_id = run_id_for(params, window)
    register(path, registry=root / "registry.json", time=time, run=run_id,
             note="retrained on 2019-2022 with v1's settings")
    result = run_gate("v2", root, floor=floor, time=time)
    windows = {"train": window, "eval": {"name": "holdout", "from": HOLDOUT[0], "to": HOLDOUT[1]}}
    metrics = {"holdout": result["candidate"], "production_holdout": result["production"],
               "gate": {k: result[k] for k in ("passed", "gain", "band", "floor", "reasons",
                                               "production_version")}}
    log_run(params, metrics, path, dv, windows=windows, runs_dir=root / "runs", time=time,
            replace=replace)
    if result["passed"]:
        promote("v2", "production", registry=root / "registry.json", time=time, note="gate passed")
    return result


# ── 5. Entry point ──────────────────────────────────────────────────────────
# The commands that built the committed ml/runs/ and ml/registry.json, in order (run from
# ml/python). The tests re-run them in an empty copy and compare the bytes.
CANONICAL = [
    ["experiments"],
    ["register", "models/v1.json", "--run", "logreg-C1-2019-2021", "--time", "2023-01-01T00:00:00Z"],
    ["promote", "v1", "production", "--time", "2023-01-01T00:00:00Z"],
    ["retrain"],
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run tracker, model registry and promotion gate.")
    ap.add_argument("--root", type=Path, default=ML_DIR,
                    help="the ml/ directory to work in (default: the repo's); try things on a copy")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("runs", help="list the tracked runs")
    sub.add_parser("experiments", help="log the NB / logreg experiment set").add_argument(
        "--replace", action="store_true", help="overwrite run files whose contents changed")
    p = sub.add_parser("compare", help="show what differs between two runs")
    p.add_argument("a"), p.add_argument("b")
    p = sub.add_parser("register", help="register a model file (path relative to ml/) in staging")
    p.add_argument("path"), p.add_argument("--run")
    p = sub.add_parser("promote", help="move a version to a stage")
    p.add_argument("version"), p.add_argument("stage", choices=STAGES)
    sub.add_parser("rollback", help="restore the previous production version")
    p = sub.add_parser("gate", help="gate a candidate against production on a holdout window")
    p.add_argument("version"), p.add_argument("--from", dest="start", default=HOLDOUT[0])
    p.add_argument("--to", dest="end", default=HOLDOUT[1])
    sub.add_parser("retrain", help="train v2, register, gate, promote if it passed").add_argument(
        "--replace", action="store_true", help="overwrite its run file if the contents changed")
    for p in sub.choices.values():
        p.add_argument("--time", help="event time (default: now; retrain: the data's date)")
        p.add_argument("--floor", type=float, default=FLOOR)
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    root = args.root.resolve()
    reg, runs_dir = root / "registry.json", root / "runs"

    if args.cmd == "runs":
        print(f"{'run':<26}{'time':<22}{'eval':<9}{'macro F1':>9}{'accuracy':>10}")
        for r in list_runs(runs_dir):
            m = r["metrics"].get("test") or r["metrics"].get("holdout")
            print(f"{r['run_id']:<26}{r['time']:<22}{r['windows']['eval']['name']:<9}"
                  f"{m['macro_f1']:>9.3f}{m['accuracy']:>10.3f}")
    elif args.cmd == "experiments":
        print("\n".join(f"logged {i}" for i in experiments(root, replace=args.replace)))
    elif args.cmd == "compare":
        print(json.dumps(compare_runs(args.a, args.b, runs_dir=runs_dir), indent=1))
    elif args.cmd == "register":
        path = Path(args.path) if Path(args.path).is_absolute() else root / args.path
        print(f"registered {register(path, registry=reg, time=args.time, run=args.run)}")
    elif args.cmd == "promote":
        promote(args.version, args.stage, registry=reg, time=args.time)
        print(f"{args.version} -> {args.stage}")
    elif args.cmd == "rollback":
        print(f"production is now {rollback(registry=reg, time=args.time)}")
    elif args.cmd in ("gate", "retrain"):
        result = (run_gate(args.version, root, window=(args.start, args.end), floor=args.floor,
                           time=args.time) if args.cmd == "gate"
                  else retrain(root, floor=args.floor, time=args.time, replace=args.replace))
        print(("PASSED" if result["passed"] else "FAILED") + " vs " + result["production_version"])
        print("\n".join(f"  {r}" for r in result["reasons"]))
        return 0 if result["passed"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
