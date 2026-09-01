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
from botocore.exceptions import ClientError
from dataset_basic import validate_dataset
from dataset_quality import analyze_dataset_quality, class_schema, load_policy, write_report
from kubernetes import client, config
from kubernetes.client.rest import ApiException


NAMESPACE = os.getenv("NAMESPACE", "mlops")
STATE_NAME = "roboflow-dataset-state"
CANDIDATE_NAME = "training-candidate"
QUALITY_POLICY_PATH = Path(os.getenv("DATASET_QUALITY_POLICY_PATH", "/policy/policy.yaml"))


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


def upload_directory(root: Path, bucket: str, prefix: str) -> None:
    s3 = boto3.client("s3")
    for path in root.rglob("*"):
        if path.is_file():
            s3.upload_file(str(path), bucket, f"{prefix}/{path.relative_to(root).as_posix()}")


def previous_dataset_reference(bucket: str, project: str, version: int) -> dict | None:
    if version <= 0:
        return None
    s3 = boto3.client("s3")
    for prefix in (f"datasets/{project}/v{version}", f"datasets/{project}-{version}"):
        try:
            report = json.loads(s3.get_object(Bucket=bucket, Key=f"{prefix}/validation_report.json")["Body"].read())
            return {
                "dataset_version": version,
                "image_count": report["dataset"]["image_count"],
                "class_schema": report["class_schema"]["current"],
            }
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in {"NoSuchKey", "404"}:
                raise
        try:
            descriptor = yaml.safe_load(s3.get_object(Bucket=bucket, Key=f"{prefix}/data.yaml")["Body"].read())
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                continue
            raise
        image_count = 0
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=f"{prefix}/"):
            image_count += sum(
                "/images/" in item["Key"] and not item["Key"].endswith("/")
                for item in page.get("Contents", [])
            )
        return {"dataset_version": version, "image_count": image_count, "class_schema": class_schema(descriptor)}
    return None


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
        policy = load_policy(QUALITY_POLICY_PATH)
        previous = previous_dataset_reference(bucket, project, processed)
        quality_report = analyze_dataset_quality(root, latest, policy, previous)
        write_report(quality_report, root / "validation_report.json")
        prefix = f"datasets/{project}/v{latest}"
        upload_directory(root, bucket, prefix)

    quality_summary = quality_report["summary"]
    upsert_configmap(api, CANDIDATE_NAME, {
        "status": "AWAITING_APPROVAL",
        "dataset_version": latest,
        "s3_uri": f"s3://{bucket}/{prefix}/",
        "validation_summary": json.dumps(summary, separators=(",", ":")),
        "quality_status": quality_summary["status"],
        "quality_warnings": quality_summary["warnings"],
        "quality_errors": quality_summary["errors"],
        "quality_manual_reviews": quality_summary["manual_reviews"],
        "quality_summary": json.dumps(quality_summary, separators=(",", ":")),
        "validation_report_uri": f"s3://{bucket}/{prefix}/validation_report.json",
    })
    upsert_configmap(api, STATE_NAME, {
        "last_processed_version": latest,
        "last_status": "AWAITING_APPROVAL",
    })
    print(json.dumps({
        "status": "candidate-created", "version": latest,
        "summary": summary, "quality": quality_summary,
    }))


if __name__ == "__main__":
    main()
