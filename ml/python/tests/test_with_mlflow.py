import pytest

pytest.importorskip("mlflow")        # optional dependency: the course runs without it

import mlops
import with_mlflow


def test_run_registered_and_read_back_by_alias(tmp_path):
    ours = mlops.load_run("logreg-C1-2019-2021")           # the committed C=1 logistic regression
    model = mlops.ML_DIR / ours["model"]["path"]
    # our own tracker, on the same settings, into a scratch folder
    run_id = mlops.log_run(ours["params"], ours["metrics"], model, ours["data_version"],
                           windows=ours["windows"], runs_dir=tmp_path / "runs", time=ours["time"])
    mine = mlops.load_run(run_id, tmp_path / "runs")

    with_mlflow.configure(tmp_path / "mlflow")
    mlflow_run = with_mlflow.log_run(mine, model)
    version = with_mlflow.register(mlflow_run, "complaints-classifier")
    seen = with_mlflow.read_back("complaints-classifier", "production", dst=tmp_path / "downloads")

    assert seen["version"] == version
    assert seen["params"] == {k: str(v) for k, v in mine["params"].items()}   # MLflow stores strings
    assert seen["metrics"] == with_mlflow.headline(mine)
    assert seen["metrics"]["test.macro_f1"] == mine["metrics"]["test"]["macro_f1"]
    assert seen["sha256"] == mine["model"]["sha256"]
    assert not (mlops.ML_DIR / "python" / "mlruns").exists()   # nothing leaked into the repo
