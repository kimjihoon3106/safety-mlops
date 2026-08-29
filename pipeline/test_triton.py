#!/usr/bin/env python3
import argparse
import json
import urllib.request


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    metadata = get_json(f"{args.url}/v2/models/yolov8")
    payload = {
        "inputs": [{
            "name": "images",
            "shape": [1, 3, 320, 320],
            "datatype": "FP32",
            "data": [0.0] * (3 * 320 * 320),
        }],
        "outputs": [{"name": "output0", "parameters": {"binary_data": False}}],
    }
    request = urllib.request.Request(
        f"{args.url}/v2/models/yolov8/infer",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    summary = {
        "model": result["model_name"],
        "model_version": result["model_version"],
        "input": metadata["inputs"],
        "output": metadata["outputs"],
        "inference_output": [
            {"name": output["name"], "datatype": output["datatype"], "shape": output["shape"]}
            for output in result["outputs"]
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
