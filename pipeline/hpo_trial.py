#!/usr/bin/env python3
import json
import numbers
import os
import re
from pathlib import Path

import boto3
import mlflow
import optuna
import yaml
from ultralytics import YOLO, settings


def metric(metrics: dict, *needles: str) -> float:
    normalized = {
        re.sub(r"[^a-z0-9]", "", key.lower()): float(value)
        for key, value in metrics.items()
        if isinstance(value, numbers.Number)
    }
    for needle in needles:
        wanted = re.sub(r"[^a-z0-9]", "", needle.lower())
        for key, value in normalized.items():
            if wanted in key:
                return value
    raise KeyError(f"metric not found: {needles}")


def main() -> None:
    candidate_id = os.environ["CANDIDATE_ID"]
    dataset_version = os.environ["DATASET_VERSION"]
    workflow_id = os.environ["WORKFLOW_ID"]
    requested_trial = int(os.environ["REQUESTED_TRIAL"])
    study_name = os.environ["OPTUNA_STUDY_NAME"]
    storage = os.getenv("OPTUNA_STORAGE", "sqlite:////work/optuna/study.db")
    Path("/work/optuna").mkdir(parents=True, exist_ok=True)
    dataset = Path(os.getenv("DATASET_PATH", "/work/dataset"))
    descriptor_path = dataset / "data.yaml"
    descriptor = yaml.safe_load(descriptor_path.read_text())
    descriptor.update({
        "train": str(dataset / "train/images"),
        "val": str(dataset / "valid/images"),
        "test": str(dataset / "test/images"),
    })
    descriptor_path.write_text(yaml.safe_dump(descriptor, sort_keys=False))

    study = optuna.create_study(
        study_name=study_name, storage=storage, direction="maximize", load_if_exists=True,
        # Each Argo trial is a separate process. A per-process seed prevents all
        # startup trials from replaying the same first TPE suggestion.
        sampler=optuna.samplers.TPESampler(seed=20260831 + requested_trial),
    )
    trial = study.ask()
    params = {
        "learning_rate": trial.suggest_float("learning_rate", 1e-4, 1e-2, log=True),
        "batch_size": trial.suggest_categorical("batch_size", [4, 8]),
        "optimizer": trial.suggest_categorical("optimizer", ["SGD", "AdamW"]),
        "weight_decay": trial.suggest_float("weight_decay", 1e-5, 1e-3, log=True),
        "momentum": trial.suggest_float("momentum", 0.85, 0.95),
        "image_size": trial.suggest_categorical("image_size", [512, 640]),
    }
    base_model = os.getenv("BASE_MODEL", "yolov8s.pt")
    epochs = int(os.getenv("HPO_EPOCHS", "10"))
    output = Path("/work/hpo") / f"trial-{trial.number}"
    output.mkdir(parents=True, exist_ok=True)

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(f"safety-hpo-{candidate_id}")
    settings.update({"mlflow": False})
    try:
        with mlflow.start_run(run_name=f"trial-{trial.number}") as run:
            mlflow.log_params({
                **params, "candidate_id": candidate_id, "dataset_version": dataset_version,
                "workflow_id": workflow_id, "optuna_trial_number": trial.number,
                "trial_id": f"{workflow_id}-{trial.number}", "model": base_model,
                "epochs": epochs, "run_type": "HPO_TRIAL",
                "gpu_visible_devices": os.getenv("NVIDIA_VISIBLE_DEVICES", "unknown"),
            })
            result = YOLO(base_model).train(
                data=str(descriptor_path), epochs=epochs, imgsz=params["image_size"],
                batch=params["batch_size"], optimizer=params["optimizer"],
                lr0=params["learning_rate"], weight_decay=params["weight_decay"],
                momentum=params["momentum"], device=0, workers=2,
                project=str(output), name=run.info.run_id, exist_ok=True, patience=5,
            )
            metrics = {
                re.sub(r"[^A-Za-z0-9_. /:-]", "_", key): float(value)
                for key, value in result.results_dict.items()
                if isinstance(value, numbers.Number)
            }
            objective = metric(metrics, "metrics/mAP50-95", "map5095")
            mlflow.log_metrics(metrics)
            mlflow.log_metric("objective_map50_95", objective)
            summary = {
                "trial_number": trial.number, "params": params, "metrics": metrics,
                "objective_map50_95": objective, "mlflow_run_id": run.info.run_id,
            }
            summary_path = output / "trial_result.json"
            summary_path.write_text(json.dumps(summary, indent=2) + "\n")
            bucket = os.environ["S3_BUCKET"]
            key = f"artifacts/hpo/{candidate_id}/trials/{trial.number}/trial_result.json"
            boto3.client("s3").upload_file(str(summary_path), bucket, key)
            mlflow.log_dict(summary, "trial_result.json")
            study.tell(trial, objective)
            print(json.dumps(summary, indent=2))
    except Exception:
        study.tell(trial, state=optuna.trial.TrialState.FAIL)
        raise


if __name__ == "__main__":
    main()
