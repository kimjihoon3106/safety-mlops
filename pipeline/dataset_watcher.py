#!/usr/bin/env python3
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import boto3
import requests
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from PIL import Image


NAMESPACE = os.getenv("NAMESPACE", "mlops")
STATE_NAME = "roboflow-dataset-state"
CANDIDATE_NAME = "training-candidate"


def project_info(workspace: str, project: str, key: str) -> dict:
    response = requests.get(
        f"https://api.roboflow.com/{workspace}/{project}",
        params={"api_key": key}, timeout=30,
    )
    response.raise_for_status()
    return response.json()["project"]


def download_dataset(workspace: str, project: str, version: int, key: str, output: Path) -> None:
    url = f"https://universe.roboflow.com/{workspace}/{project}/dataset/{version}/download/yolov8"
    with tempfile.NamedTemporaryFile(suffix=".zip") as archive:
        with requests.get(url, params={"api_key": key}, stream=True, timeout=180) as response:
            response.raise_for_status()
            with open(archive.name, "wb") as destination:
                shutil.copyfileobj(response.raw, destination)
        if not zipfile.is_zipfile(archive.name):
            raise RuntimeError("Roboflow response is not a ZIP archive")
        with zipfile.ZipFile(archive.name) as source:
            source.extractall(output)


def validate_dataset(root: Path) -> dict:
    descriptor_path = root / "data.yaml"
    if not descriptor_path.is_file():
        raise RuntimeError("data.yaml is missing")
    descriptor = yaml.safe_load(descriptor_path.read_text())
    classes = descriptor.get("names", [])
    if not classes or int(descriptor.get("nc", len(classes))) != len(classes):
        raise RuntimeError("data.yaml class metadata is inconsistent")
    descriptor.update({"train": "train/images", "val": "valid/images", "test": "test/images"})
    descriptor_path.write_text(yaml.safe_dump(descriptor, sort_keys=False))

    summary = {"classes": len(classes), "splits": {}, "images": 0, "labels": 0, "boxes": 0}
    for split in ("train", "valid", "test"):
        image_dir, label_dir = root / split / "images", root / split / "labels"
        if not image_dir.is_dir() or not label_dir.is_dir():
            raise RuntimeError(f"missing {split} images or labels directory")
        images = [path for path in image_dir.iterdir() if path.is_file()]
        labels = [path for path in label_dir.glob("*.txt")]
        image_stems = {path.stem for path in images}
        missing_images = [path.name for path in labels if path.stem not in image_stems]
        if missing_images:
            raise RuntimeError(f"{split}: labels without images: {missing_images[:5]}")
        for image_path in images:
            with Image.open(image_path) as image:
                image.verify()
        boxes = 0
        for label_path in labels:
            for line_number, line in enumerate(label_path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                values = line.split()
                if len(values) != 5:
                    raise RuntimeError(f"{label_path}:{line_number}: expected 5 values")
                class_id = int(values[0])
                coordinates = [float(value) for value in values[1:]]
                if not 0 <= class_id < len(classes) or any(value < 0 or value > 1 for value in coordinates):
                    raise RuntimeError(f"{label_path}:{line_number}: invalid class or box")
                boxes += 1
        summary["splits"][split] = {"images": len(images), "labels": len(labels), "boxes": boxes}
        summary["images"] += len(images)
        summary["labels"] += len(labels)
        summary["boxes"] += boxes
    if summary["images"] == 0 or summary["boxes"] == 0:
        raise RuntimeError("dataset is empty")
    return summary


def upload_directory(root: Path, bucket: str, prefix: str) -> None:
    s3 = boto3.client("s3")
    for path in root.rglob("*"):
        if path.is_file():
            s3.upload_file(str(path), bucket, f"{prefix}/{path.relative_to(root).as_posix()}")


def upsert_configmap(api: client.CoreV1Api, name: str, data: dict) -> None:
    body = client.V1ConfigMap(metadata=client.V1ObjectMeta(name=name), data={k: str(v) for k, v in data.items()})
    try:
        api.replace_namespaced_config_map(name, NAMESPACE, body)
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_namespaced_config_map(NAMESPACE, body)


def main() -> None:
    workspace = os.environ["ROBOFLOW_WORKSPACE"]
    project = os.environ["ROBOFLOW_PROJECT"]
    key = os.environ["ROBOFLOW_API_KEY"]
    bucket = os.environ["S3_BUCKET"]
    config.load_incluster_config()
    api = client.CoreV1Api()
    state = api.read_namespaced_config_map(STATE_NAME, NAMESPACE)
    processed = int(state.data.get("last_processed_version", "0"))
    latest = int(project_info(workspace, project, key)["versions"])
    if latest <= processed:
        print(json.dumps({"status": "up-to-date", "latest": latest, "processed": processed}))
        return

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / f"{project}-v{latest}"
        root.mkdir()
        download_dataset(workspace, project, latest, key, root)
        summary = validate_dataset(root)
        prefix = f"datasets/{project}/v{latest}"
        upload_directory(root, bucket, prefix)

    upsert_configmap(api, CANDIDATE_NAME, {
        "status": "AWAITING_APPROVAL",
        "dataset_version": latest,
        "s3_uri": f"s3://{bucket}/{prefix}/",
        "validation_summary": json.dumps(summary, separators=(",", ":")),
    })
    upsert_configmap(api, STATE_NAME, {
        "last_processed_version": latest,
        "last_status": "AWAITING_APPROVAL",
    })
    print(json.dumps({"status": "candidate-created", "version": latest, "summary": summary}))


if __name__ == "__main__":
    main()
