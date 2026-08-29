#!/usr/bin/env python3
import json
import os
import time
import urllib.request
from pathlib import Path


def get(url):
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=20) as response:
        body = response.read()
        status = response.status
    return status, body, (time.perf_counter() - started) * 1000


def multipart(image):
    boundary = "safety-mlops-smoke-boundary"
    payload = image.read_bytes()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"smoke.jpg\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def main():
    triton = os.getenv("TRITON_URL", "http://triton:8000")
    api = os.getenv("API_URL", "http://inference-api:8080")
    expected = os.environ["EXPECTED_MODEL_VERSION"]
    maximum = float(os.getenv("MAX_LATENCY_MS", "5000"))
    status, _, _ = get(f"{triton}/v2/health/ready")
    if status != 200:
        raise RuntimeError(f"Triton readiness HTTP {status}")
    status, health, _ = get(f"{api}/health")
    if status != 200 or json.loads(health).get("status") != "ok":
        raise RuntimeError("API health failed")
    body, content_type = multipart(Path("/test/smoke.jpg"))
    request = urllib.request.Request(f"{api}/detect", data=body, headers={"Content-Type": content_type})
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
        http_status = response.status
    latency = (time.perf_counter() - started) * 1000
    required = {"model", "model_version", "detection_count", "detections"}
    if http_status != 200 or not required.issubset(result) or not isinstance(result["detections"], list):
        raise RuntimeError(f"invalid detection response: {result}")
    if result["model_version"] != expected:
        raise RuntimeError(f"model version {result['model_version']} != {expected}")
    if latency > maximum:
        raise RuntimeError(f"latency {latency:.1f}ms > {maximum}ms")
    print(json.dumps({"status": "PASS", "http_status": http_status, "model_version": expected,
                      "latency_ms": round(latency, 2), "detection_count": result["detection_count"]}))


if __name__ == "__main__":
    main()
