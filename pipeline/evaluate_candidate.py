#!/usr/bin/env python3
import json
import os
import statistics
import time
from pathlib import Path

import boto3
import numpy as np
import onnxruntime as ort
import yaml
from kubernetes import client, config


NAMESPACE = "mlops"
CANDIDATE_CONFIGMAP = "training-candidate"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"invalid S3 URI: {uri}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key.rstrip("/")


def metric(metrics: dict, *names: str) -> float:
    normalized = {key.lower().replace("_", ""): value for key, value in metrics.items()}
    for name in names:
        needle = name.lower().replace("_", "")
        for key, value in normalized.items():
            if needle in key:
                return float(value)
    raise KeyError(f"metric not found: {names}")


def patch_candidate(values: dict) -> None:
    config.load_incluster_config()
    api = client.CoreV1Api()
    current = api.read_namespaced_config_map(CANDIDATE_CONFIGMAP, NAMESPACE)
    data = dict(current.data or {})
    data.update({key: str(value) for key, value in values.items()})
    current.data = data
    api.replace_namespaced_config_map(CANDIDATE_CONFIGMAP, NAMESPACE, current)


def benchmark(model_path: Path, image_size: int) -> dict:
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        raise RuntimeError(f"CUDAExecutionProvider unavailable: {available}")
    session = ort.InferenceSession(
        str(model_path), providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    input_meta = session.get_inputs()[0]
    output_meta = session.get_outputs()[0]
    tensor = np.random.random((1, 3, image_size, image_size)).astype(np.float32)
    for _ in range(10):
        session.run(None, {input_meta.name: tensor})
    timings = []
    for _ in range(50):
        started = time.perf_counter()
        session.run(None, {input_meta.name: tensor})
        timings.append((time.perf_counter() - started) * 1000)
    timings.sort()
    return {
        "latency_ms_mean": round(statistics.mean(timings), 3),
        "latency_ms_p95": round(timings[int(len(timings) * 0.95) - 1], 3),
        "provider": session.get_providers()[0],
        "input_tensor": input_meta.name,
        "input_shape": input_meta.shape,
        "output_tensor": output_meta.name,
        "output_shape": output_meta.shape,
    }


def main() -> None:
    candidate_uri = os.environ["CANDIDATE_S3_URI"]
    production_uri = os.environ["PRODUCTION_METADATA_URI"]
    policy = yaml.safe_load(Path(os.getenv("POLICY_PATH", "/policy/policy.yaml")).read_text())
    bucket, prefix = parse_s3_uri(candidate_uri)
    prod_bucket, prod_key = parse_s3_uri(production_uri)
    s3 = boto3.client("s3")

    objects = {
        item["Key"].removeprefix(prefix + "/")
        for item in s3.list_objects_v2(Bucket=bucket, Prefix=prefix + "/").get("Contents", [])
    }
    required_before_conversion = {"best.pt", "model.onnx", "candidate_metadata.json"}
    missing = sorted(required_before_conversion - objects)
    if missing:
        raise RuntimeError(f"candidate artifacts missing: {missing}")

    work = Path("/work")
    work.mkdir(parents=True, exist_ok=True)
    candidate_metadata_path = work / "candidate_metadata.json"
    model_path = work / "model.onnx"
    production_metadata_path = work / "production_metadata.json"
    s3.download_file(bucket, f"{prefix}/candidate_metadata.json", str(candidate_metadata_path))
    s3.download_file(bucket, f"{prefix}/model.onnx", str(model_path))
    s3.download_file(prod_bucket, prod_key, str(production_metadata_path))
    candidate = json.loads(candidate_metadata_path.read_text())
    production = json.loads(production_metadata_path.read_text())

    image_size = int(candidate.get("input_size", [640, 640])[0])
    latency = benchmark(model_path, image_size)
    candidate_metrics = candidate["metrics"]
    production_metrics = production["metrics"]
    checks = {
        "map50_delta": metric(candidate_metrics, "map50") - metric(production_metrics, "map50"),
        "map50_95_delta": metric(candidate_metrics, "map50-95", "map5095") - metric(production_metrics, "map50_95"),
        "recall": metric(candidate_metrics, "recall"),
        "latency_ms_p95": latency["latency_ms_p95"],
    }
    limits = policy["metrics"]
    decisions = {
        "map50": checks["map50_delta"] >= float(limits["map50"]["minimum_delta"]),
        "map50_95": checks["map50_95_delta"] >= float(limits["map50_95"]["minimum_delta"]),
        "recall": checks["recall"] >= float(limits["recall"]["minimum"]),
        "latency": checks["latency_ms_p95"] <= float(limits["latency_ms"]["maximum"]),
    }
    passed = all(decisions.values())
    report = {
        "status": "EVALUATION_PASSED" if passed else "EVALUATION_REJECTED",
        "candidate_s3_uri": candidate_uri,
        "production_version": production["model_version"],
        "checks": checks,
        "decisions": decisions,
        "latency": latency,
    }
    report_path = work / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    status_path = Path(os.getenv("EVALUATION_STATUS_PATH", "/work/evaluation_status.txt"))
    status_path.write_text(report["status"] + "\n")
    s3.upload_file(str(report_path), bucket, f"{prefix}/evaluation_report.json")
    patch_candidate({
        "status": report["status"],
        "evaluation_report_uri": f"s3://{bucket}/{prefix}/evaluation_report.json",
    })
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        try:
            patch_candidate({"status": "EVALUATION_ERROR", "evaluation_error": str(error)[:500]})
        finally:
            raise
