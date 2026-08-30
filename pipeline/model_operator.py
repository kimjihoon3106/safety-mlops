#!/usr/bin/env python3
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import boto3
from kubernetes import client, config


NS = "mlops"
CM = "training-candidate"


def s3_uri(uri):
    if not uri.startswith("s3://"):
        raise ValueError(f"invalid S3 URI: {uri}")
    bucket, _, key = uri[5:].partition("/")
    return bucket, key.rstrip("/")


def candidate():
    config.load_incluster_config()
    api = client.CoreV1Api()
    cm = api.read_namespaced_config_map(CM, NS)
    return api, cm, dict(cm.data or {})


def patch(api, cm, values):
    data = dict(cm.data or {})
    data.update({key: str(value) for key, value in values.items()})
    cm.data = data
    api.replace_namespaced_config_map(CM, NS, cm)


def convert():
    api, cm, data = candidate()
    if data.get("status") != "EVALUATION_PASSED":
        raise RuntimeError(f"expected EVALUATION_PASSED, got {data.get('status')}")
    patch(api, cm, {"status": "CONVERTING"})
    bucket, prefix = s3_uri(data["candidate_s3_uri"])
    s3 = boto3.client("s3")
    work = Path("/work")
    work.mkdir(parents=True, exist_ok=True)
    onnx = work / "model.onnx"
    plan = work / "model.plan"
    s3.download_file(bucket, f"{prefix}/model.onnx", str(onnx))
    command = [
        "/usr/src/tensorrt/bin/trtexec", f"--onnx={onnx}", f"--saveEngine={plan}",
        "--fp16", "--minShapes=images:1x3x320x320", "--optShapes=images:4x3x640x640",
        "--maxShapes=images:8x3x640x640", "--skipInference",
    ]
    subprocess.run(command, check=True)
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    s3.upload_file(str(plan), bucket, f"{prefix}/model.plan")
    patch(api, cm, {"status": "READY_FOR_PROMOTION", "engine_sha256": digest})
    print(json.dumps({"status": "READY_FOR_PROMOTION", "engine_sha256": digest}))


def promote():
    api, cm, data = candidate()
    if data.get("status") != "READY_FOR_PROMOTION":
        raise RuntimeError(f"expected READY_FOR_PROMOTION, got {data.get('status')}")
    patch(api, cm, {"status": "PROMOTING"})
    bucket, candidate_prefix = s3_uri(data["candidate_s3_uri"])
    s3 = boto3.client("s3")
    response = s3.list_objects_v2(Bucket=bucket, Prefix="models/safety/", Delimiter="/")
    versions = [int(match.group(1)) for item in response.get("CommonPrefixes", [])
                if (match := re.search(r"/v(\d+)/$", item["Prefix"]))]
    version = f"v{max(versions, default=0) + 1}"
    destination = f"models/safety/{version}"
    if s3.list_objects_v2(Bucket=bucket, Prefix=destination + "/", MaxKeys=1).get("KeyCount"):
        raise RuntimeError(f"immutable destination already exists: s3://{bucket}/{destination}/")
    metadata_object = s3.get_object(Bucket=bucket, Key=f"{candidate_prefix}/candidate_metadata.json")
    metadata = json.loads(metadata_object["Body"].read())
    classes = int(metadata.get("classes", 25))
    metadata.update({
        "model_version": version, "precision": "FP16", "engine_sha256": data["engine_sha256"],
        "dynamic_profile": {"min": [1, 3, 320, 320], "opt": [4, 3, 640, 640], "max": [8, 3, 640, 640]},
        "input_tensor": "images", "output_tensor": "output0",
    })
    config_text = f'''name: "yolov8"\nplatform: "tensorrt_plan"\nmax_batch_size: 8\ninput [{{ name: "images" data_type: TYPE_FP32 dims: [3, -1, -1] }}]\noutput [{{ name: "output0" data_type: TYPE_FP32 dims: [{classes + 4}, -1] }}]\ndynamic_batching {{ preferred_batch_size: [1, 4, 8] max_queue_delay_microseconds: 100 }}\n'''
    created = []
    try:
        for name in ("best.pt", "model.onnx", "model.plan", "evaluation_report.json"):
            key = f"{destination}/{name}"
            s3.copy_object(Bucket=bucket, Key=key,
                           CopySource={"Bucket": bucket, "Key": f"{candidate_prefix}/{name}"})
            created.append(key)
        for key, body, content_type in (
            (f"{destination}/config.pbtxt", config_text.encode(), "text/plain"),
            (f"{destination}/model_metadata.json", (json.dumps(metadata, indent=2) + "\n").encode(), "application/json"),
        ):
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=content_type)
            created.append(key)
        ready_key = f"{destination}/_READY"
        s3.put_object(Bucket=bucket, Key=ready_key, Body=data["engine_sha256"].encode())
        created.append(ready_key)
    except Exception:
        if created:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key} for key in created]})
        raise
    values = {
        "status": "PROMOTED_PENDING_GIT", "promoted_version": version,
        "promoted_model_uri": f"s3://{bucket}/{destination}/model.plan",
        "promoted_config_uri": f"s3://{bucket}/{destination}/config.pbtxt",
        "promoted_metadata_uri": f"s3://{bucket}/{destination}/model_metadata.json",
    }
    patch(api, cm, values)
    print(json.dumps(values, indent=2))


if __name__ == "__main__":
    action = os.environ["ACTION"]
    try:
        {"convert": convert, "promote": promote}[action]()
    except Exception as error:
        try:
            api, cm, _ = candidate()
            patch(api, cm, {"status": "CONVERSION_ERROR" if action == "convert" else "PROMOTION_ERROR",
                            "operator_error": str(error)[:500]})
        finally:
            raise
