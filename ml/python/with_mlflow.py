#!/usr/bin/env python3
"""
with_mlflow — the same tracked run and registration as mlops.py, done with MLflow.

MLflow is optional: nothing else in the course imports it. Install it only if you want to
run this file (a virtual environment keeps it out of your system Python):
    pip install mlflow

Run (from ml/python):
    python with_mlflow.py                    # log, register, alias, then compare with our run
    python with_mlflow.py --workdir mlflow-demo
    mlflow ui --backend-store-uri sqlite:///mlflow-demo/mlflow.db     # browse it at :5000

Without --workdir everything goes to a temporary folder that is deleted at the end, so
nothing lands in the repository. Tested with MLflow 3.17: its model registry needs a
database, so the tracking store is a local SQLite file (the old plain-files store is
deprecated and refuses to start). Still no server: SQLite is one file on disk.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from pathlib import Path

try:
    import mlflow
    from mlflow import MlflowClient
except ImportError:
    sys.exit("MLflow is not installed. It is optional: pip install mlflow")

import mlops

EXPERIMENT = "complaints-classifier"
MODEL_NAME = "complaints-classifier"
OUR_RUN = "logreg-C1-2019-2021"                  # the committed run we repeat in MLflow


# ── 1. Configuration ────────────────────────────────────────────────────────
def configure(workdir: str | Path) -> MlflowClient:
    """Point MLflow at a folder: a SQLite file for runs and the registry, and a sub-folder
    for artifacts. We set the artifact folder ourselves; the default is ./mlruns."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(f"sqlite:///{workdir / 'mlflow.db'}")
    mlflow.set_registry_uri(mlflow.get_tracking_uri())
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=(workdir / "artifacts").as_uri())
    mlflow.set_experiment(EXPERIMENT)
    return MlflowClient()


def headline(run: dict) -> dict:
    """The numbers worth a column: every number in the run's metrics except the per-class
    ones, named like 'test.macro_f1'. (Class names contain commas, which metric names refuse.)"""
    flat = mlops._flatten(run["metrics"])
    return {k: v for k, v in flat.items()
            if "per_class" not in k and isinstance(v, (int, float)) and not isinstance(v, bool)}


# ── 2. Log the run ──────────────────────────────────────────────────────────
def log_run(run: dict, model_path: str | Path) -> str:
    """One MLflow run for one of our run records: params, metrics, and the model file as a
    plain artifact (our model is JSON, so no pickled 'flavour'). Tags carry what MLflow has
    no field for: our run id, the data hash and the git commit."""
    with mlflow.start_run(run_name=run["run_id"]) as active:
        mlflow.log_params(run["params"])
        mlflow.log_metrics(headline(run))
        mlflow.set_tags({"our_run_id": run["run_id"], "data_version": run["data_version"],
                         "git_commit": str(run["git_commit"])})
        mlflow.log_artifact(str(model_path), artifact_path="model")
        return active.info.run_id


# ── 3. Register and alias ───────────────────────────────────────────────────
def register(run_id: str, name: str = MODEL_NAME, alias: str = "production") -> str:
    """Add the run's model folder to the registry as a new version, then point an alias at
    it. Aliases are MLflow's replacement for the old fixed stages: any name you like,
    moved with one call, and a server loads 'models:/name@production'."""
    client = MlflowClient()
    if not client.search_registered_models(filter_string=f"name = '{name}'"):
        client.create_registered_model(name)
    source = f"{client.get_run(run_id).info.artifact_uri}/model"
    version = client.create_model_version(name, source=source, run_id=run_id).version
    client.set_registered_model_alias(name, alias, version)
    return version


def read_back(name: str, alias: str = "production") -> dict:
    """Follow the alias to a version, its run, and the model file; hash the file we get."""
    client = MlflowClient()
    mv = client.get_model_version_by_alias(name, alias)
    run = client.get_run(mv.run_id)
    folder = Path(client.download_artifacts(mv.run_id, "model"))
    model_file = next(folder.glob("*.json"))
    return {"version": mv.version, "params": dict(run.data.params), "metrics": dict(run.data.metrics),
            "sha256": hashlib.sha256(model_file.read_bytes()).hexdigest()}


# ── 4. Compare with our tracker ─────────────────────────────────────────────
def side_by_side(ours: dict, theirs: dict) -> bool:
    """Print what each tool recorded for the same settings. True when everything agrees.
    MLflow keeps params as text, so we compare text."""
    rows = [(f"param {k}", str(v), theirs["params"].get(k)) for k, v in ours["params"].items()]
    rows += [(f"metric {k}", v, theirs["metrics"].get(k)) for k, v in headline(ours).items()]
    rows.append(("model sha256", ours["model"]["sha256"], theirs["sha256"]))
    print(f"{'':<28}{'mlops.py':<22}{'MLflow':<22}")
    for label, a, b in rows:
        show = lambda x: f"{x:.6g}" if isinstance(x, float) else str(x)[:20]
        print(f"{label:<28}{show(a):<22}{show(b):<22}{'ok' if a == b else 'DIFFERENT'}")
    return all(a == b for _, a, b in rows)


# ── 5. Entry point ──────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repeat the C=1 run and its registration in MLflow.")
    ap.add_argument("--workdir", type=Path,
                    help="keep MLflow's files here (default: a temp folder, deleted at the end)")
    args = ap.parse_args(argv)
    ours = mlops.load_run(OUR_RUN)
    model = mlops.ML_DIR / ours["model"]["path"]

    def go(workdir: Path) -> bool:
        configure(workdir)
        run_id = log_run(ours, model)
        version = register(run_id)
        print(f"logged run {run_id[:8]}, registered {MODEL_NAME} v{version}, alias 'production'\n")
        return side_by_side(ours, read_back(MODEL_NAME, "production"))

    if args.workdir:
        agree = go(args.workdir)
        print(f"\nbrowse: mlflow ui --backend-store-uri sqlite:///{args.workdir.resolve()}/mlflow.db")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            agree = go(Path(tmp))
    print("\nthe two records agree" if agree else "\nthe two records DIFFER")
    return 0 if agree else 1


if __name__ == "__main__":
    sys.exit(main())
