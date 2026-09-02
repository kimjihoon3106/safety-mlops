#!/usr/bin/env python3
import json
import numbers
import os
import re
import threading
import time
from pathlib import Path

import boto3
import mlflow
import optuna
import yaml
from ultralytics import YOLO, settings


SAMPLER_SEED = int(os.getenv("OPTUNA_SAMPLER_SEED", "20260831"))
MODEL_SEED = int(os.getenv("MODEL_TRAINING_SEED", "20260831"))


def heartbeat(storage: optuna.storages.RDBStorage, trial_id: int, stop: threading.Event) -> None:
    interval = int(os.getenv("OPTUNA_HEARTBEAT_INTERVAL", "60"))
    while not stop.wait(interval):
        storage.record_heartbeat(trial_id)


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
    storage_url = os.environ["OPTUNA_STORAGE"]
    dataset = Path(os.getenv("DATASET_PATH", "/work/dataset"))
    descriptor_path = dataset / "data.yaml"
    descriptor = yaml.safe_load(descriptor_path.read_text())
    descriptor.update({
        "train": str(dataset / "train/images"),
        "val": str(dataset / "valid/images"),
        "test": str(dataset / "test/images"),
    })
    descriptor_path.write_text(yaml.safe_dump(descriptor, sort_keys=False))

    storage = optuna.storages.RDBStorage(
        url=storage_url,
        heartbeat_interval=int(os.getenv("OPTUNA_HEARTBEAT_INTERVAL", "60")),
        grace_period=int(os.getenv("OPTUNA_HEARTBEAT_GRACE_PERIOD", "180")),
    )
    study = optuna.create_study(
        study_name=study_name,
        storage=storage,
        direction="maximize",
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(seed=SAMPLER_SEED, n_startup_trials=1),
    )
    optuna.storages.fail_stale_trials(study)
    completed_before_suggestion = len([
        item for item in study.trials if item.state == optuna.trial.TrialState.COMPLETE
    ])
    study.set_user_attr("sampler", "TPESampler")
    study.set_user_attr("sampler_seed", SAMPLER_SEED)
    study.set_user_attr("n_startup_trials", 1)
    trial = study.ask()
    stop_heartbeat = threading.Event()
    heartbeat_thread = threading.Thread(
        target=heartbeat, args=(storage, trial._trial_id, stop_heartbeat), daemon=True
    )
    heartbeat_thread.start()
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
    dataloader_workers = int(os.getenv("HPO_DATALOADER_WORKERS", "0"))
    output = Path("/work/hpo") / f"trial-{trial.number}"
    output.mkdir(parents=True, exist_ok=True)

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment(f"safety-hpo-{candidate_id}")
    settings.update({"mlflow": False})
    started = time.monotonic()
    try:
        with mlflow.start_run(run_name=f"trial-{trial.number}") as run:
            mlflow.log_params({
                **params, "candidate_id": candidate_id, "dataset_version": dataset_version,
                "workflow_id": workflow_id, "optuna_trial_number": trial.number,
                "argo_sequence": requested_trial,
                "trial_id": f"{workflow_id}-{trial.number}", "model": base_model,
                "epochs": epochs, "run_type": "HPO_TRIAL",
                "dataloader_workers": dataloader_workers,
                "sampler_seed": SAMPLER_SEED, "model_training_seed": MODEL_SEED + trial.number,
                "completed_trials_before_suggestion": completed_before_suggestion,
                "gpu_visible_devices": os.getenv("NVIDIA_VISIBLE_DEVICES", "unknown"),
            })
            result = YOLO(base_model).train(
                data=str(descriptor_path), epochs=epochs, imgsz=params["image_size"],
                batch=params["batch_size"], optimizer=params["optimizer"],
                lr0=params["learning_rate"], weight_decay=params["weight_decay"],
                momentum=params["momentum"], device=0, workers=dataloader_workers,
                seed=MODEL_SEED + trial.number,
                project=str(output), name=run.info.run_id, exist_ok=True, patience=5,
            )
            metrics = {
                re.sub(r"[^A-Za-z0-9_. /:-]", "_", key): float(value)
                for key, value in result.results_dict.items()
                if isinstance(value, numbers.Number)
            }
            objective = metric(metrics, "metrics/mAP50-95", "map5095")
            training_duration = time.monotonic() - started
            mlflow.log_metrics(metrics)
            mlflow.log_metric("objective_map50_95", objective)
            mlflow.log_metric("training_duration_seconds", training_duration)
            summary = {
                "trial_number": trial.number, "params": params, "metrics": metrics,
                "objective_map50_95": objective,
                "training_duration_seconds": training_duration,
                "completed_trials_before_suggestion": completed_before_suggestion,
                "mlflow_run_id": run.info.run_id,
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
        if study.trials[trial.number].state == optuna.trial.TrialState.RUNNING:
            study.tell(trial, state=optuna.trial.TrialState.FAIL)
        raise
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=5)


if __name__ == "__main__":
    main()
