#!/usr/bin/env python3
import json
import numbers
import os
import re
from pathlib import Path

import boto3
import mlflow
import yaml
from kubernetes import client, config
from ultralytics import YOLO


def download_prefix(s3_uri: str, destination: Path) -> None:
    if not s3_uri.startswith("s3://"):
        raise ValueError("DATASET_S3_URI must start with s3://")
    bucket, _, prefix = s3_uri[5:].partition("/")
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            relative = item["Key"][len(prefix):].lstrip("/")
            if not relative:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, item["Key"], str(target))


def update_candidate(values: dict) -> None:
    config.load_incluster_config()
    api = client.CoreV1Api()
    current = api.read_namespaced_config_map("training-candidate", "mlops")
    data = dict(current.data or {})
    data.update({key: str(value) for key, value in values.items()})
    current.data = data
    api.replace_namespaced_config_map("training-candidate", "mlops", current)


def main() -> None:
    dataset_uri = os.environ["DATASET_S3_URI"]
    dataset_version = os.environ["DATASET_VERSION"]
    bucket = os.environ["S3_BUCKET"]
    epochs = int(os.getenv("EPOCHS", "50"))
    image_size = int(os.getenv("IMAGE_SIZE", "640"))
    batch = int(os.getenv("BATCH_SIZE", "8"))
    model_name = os.getenv("BASE_MODEL", "yolov8s.pt")
    hyperparameters = {}
    if os.getenv("HYPERPARAMETERS_JSON"):
        hyperparameters = json.loads(os.environ["HYPERPARAMETERS_JSON"])
    elif os.getenv("HYPERPARAMETERS_PATH"):
        hyperparameters = json.loads(Path(os.environ["HYPERPARAMETERS_PATH"]).read_text())
    image_size = int(hyperparameters.get("image_size", image_size))
    batch = int(hyperparameters.get("batch_size", batch))
    learning_rate = float(hyperparameters.get("learning_rate", 0.01))
    optimizer = str(hyperparameters.get("optimizer", "auto"))
    weight_decay = float(hyperparameters.get("weight_decay", 0.0005))
    momentum = float(hyperparameters.get("momentum", 0.937))
    work = Path("/work")
    dataset = work / "dataset"
    output = work / "runs"
    if os.getenv("DATASET_PATH"):
        dataset = Path(os.environ["DATASET_PATH"])
    else:
        download_prefix(dataset_uri, dataset)
    descriptor_path = dataset / "data.yaml"
    descriptor = yaml.safe_load(descriptor_path.read_text())
    descriptor.update({
        "train": str(dataset / "train/images"),
        "val": str(dataset / "valid/images"),
        "test": str(dataset / "test/images"),
    })
    descriptor_path.write_text(yaml.safe_dump(descriptor, sort_keys=False))

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
    mlflow.set_experiment("yolov8-safety-candidates")
    with mlflow.start_run(run_name=f"dataset-{dataset_version}") as run:
        mlflow.log_params({
            "dataset_version": dataset_version, "base_model": model_name,
            "epochs": epochs, "image_size": image_size, "batch_size": batch,
            "learning_rate": learning_rate, "optimizer": optimizer,
            "weight_decay": weight_decay, "momentum": momentum,
            "run_type": "FINAL_TRAINING",
            "candidate_id": os.getenv("CANDIDATE_ID", dataset_version),
            "workflow_id": os.getenv("WORKFLOW_ID", "manual"),
            "git_commit": os.getenv("GIT_COMMIT", "unknown"),
        })
        result = YOLO(model_name).train(
            data=str(descriptor_path), epochs=epochs, imgsz=image_size, batch=batch,
            device=0, workers=2, project=str(output), name=run.info.run_id,
            exist_ok=True, patience=15, lr0=learning_rate, optimizer=optimizer,
            weight_decay=weight_decay, momentum=momentum,
        )
        metrics = {
            re.sub(r"[^A-Za-z0-9_. /:-]", "_", key): float(value)
            for key, value in result.results_dict.items()
            if isinstance(value, numbers.Number)
        }
        mlflow.log_metrics(metrics)
        best_pt = Path(result.save_dir) / "weights/best.pt"
        exported = Path(YOLO(str(best_pt)).export(
            format="onnx", dynamic=True, simplify=True, imgsz=image_size, opset=17,
        ))
        candidate_prefix = f"models/safety/candidates/{run.info.run_id}"
        s3 = boto3.client("s3")
        s3.upload_file(str(best_pt), bucket, f"{candidate_prefix}/best.pt")
        s3.upload_file(str(exported), bucket, f"{candidate_prefix}/model.onnx")
        metadata = {
            "status": "EVALUATING", "training_run_id": run.info.run_id,
            "dataset_version": dataset_version, "model_type": model_name,
            "epochs": epochs, "input_size": [image_size, image_size], "metrics": metrics,
            "hyperparameters": hyperparameters,
            "workflow_id": os.getenv("WORKFLOW_ID", "manual"),
        }
        metadata_path = work / "candidate_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        s3.upload_file(str(metadata_path), bucket, f"{candidate_prefix}/candidate_metadata.json")
        mlflow.log_artifact(str(best_pt), artifact_path="models")
        mlflow.log_artifact(str(exported), artifact_path="models")
        mlflow.log_dict(metadata, "candidate_metadata.json")
        update_candidate({
            "status": "EVALUATING", "training_run_id": run.info.run_id,
            "candidate_s3_uri": f"s3://{bucket}/{candidate_prefix}/",
        })
        print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
