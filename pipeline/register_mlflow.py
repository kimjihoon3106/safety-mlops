#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import mlflow


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tracking-uri", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()

    metadata = json.loads((args.model_dir / "model_metadata.json").read_text())
    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("yolov8-safety")
    with mlflow.start_run(run_name="yolov8n-safety-final-export") as run:
        mlflow.log_params({
            "model": "yolov8n",
            "epochs": 5,
            "image_size": 640,
            "tensorrt_precision": metadata["tensorrt"]["precision"],
            "onnx_input": metadata["onnx"]["inputs"][0]["name"],
            "onnx_output": metadata["onnx"]["outputs"][0]["name"],
        })
        mlflow.log_metrics({
            "map50_overall": 0.220,
            "map50_95_overall": 0.144,
            "map50_hardhat": 0.576,
            "map50_safety_vest": 0.459,
        })
        for filename in ("best.pt", "best.onnx", "best.engine", "model_metadata.json"):
            mlflow.log_artifact(str(args.model_dir / filename), artifact_path="models")
        print(run.info.run_id)


if __name__ == "__main__":
    main()
