#!/usr/bin/env python3
import argparse
import json
import numbers
import os
import re
import shutil
from pathlib import Path

import boto3
import mlflow
import onnx
from ultralytics import YOLO


def inspect_onnx(path: Path) -> dict:
    model = onnx.load(str(path))
    onnx.checker.check_model(model)

    def tensor_info(value):
        dims = []
        for dim in value.type.tensor_type.shape.dim:
            dims.append(dim.dim_param or dim.dim_value or "dynamic")
        return {"name": value.name, "shape": dims}

    return {
        "inputs": [tensor_info(value) for value in model.graph.input],
        "outputs": [tensor_info(value) for value in model.graph.output],
    }


def inspect_engine(path: Path) -> dict:
    import tensorrt as trt

    logger = trt.Logger(trt.Logger.WARNING)
    with open(path, "rb") as source, trt.Runtime(logger) as runtime:
        engine = runtime.deserialize_cuda_engine(source.read())
    if engine is None:
        raise RuntimeError("TensorRT could not deserialize the generated engine")
    tensors = []
    for index in range(engine.num_io_tensors):
        name = engine.get_tensor_name(index)
        tensors.append(
            {
                "name": name,
                "mode": str(engine.get_tensor_mode(name)),
                "shape": list(engine.get_tensor_shape(name)),
                "dtype": str(engine.get_tensor_dtype(name)),
            }
        )
    return {"inputs_outputs": tensors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path("outputs/safety-yolov8n"))
    parser.add_argument("--resume-from", type=Path)
    args = parser.parse_args()

    bucket = os.environ["S3_BUCKET"]
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000")
    args.output.mkdir(parents=True, exist_ok=True)
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("yolov8-safety")

    with mlflow.start_run() as run:
        mlflow.log_params(
            {"model": "yolov8n.pt", "epochs": args.epochs, "imgsz": args.imgsz, "batch": args.batch}
        )
        if args.resume_from:
            best_pt = args.resume_from.resolve()
        else:
            model = YOLO("yolov8n.pt")
            result = model.train(
                data=str(args.data.resolve()),
                epochs=args.epochs,
                imgsz=args.imgsz,
                batch=args.batch,
                device=0,
                workers=0,
                project=str(args.output.parent.resolve()),
                name=args.output.name,
                exist_ok=True,
            )
            metrics = {
                re.sub(r"[^A-Za-z0-9_. /:-]", "_", key): float(value)
                for key, value in result.results_dict.items()
                if isinstance(value, numbers.Number)
            }
            mlflow.log_metrics(metrics)
            best_pt = Path(result.save_dir) / "weights/best.pt"
        final_pt = args.output / "best.pt"
        if best_pt != final_pt.resolve():
            shutil.copy2(best_pt, final_pt)
        mlflow.log_artifact(str(final_pt), artifact_path="models")

        exported = YOLO(str(final_pt)).export(
            format="onnx", dynamic=True, simplify=True, imgsz=args.imgsz, opset=17
        )
        best_onnx = Path(exported)
        if best_onnx != args.output / "best.onnx":
            shutil.move(str(best_onnx), args.output / "best.onnx")
        best_onnx = args.output / "best.onnx"
        onnx_info = inspect_onnx(best_onnx)

        engine_export = YOLO(str(final_pt)).export(
            format="engine", dynamic=True, half=True, imgsz=args.imgsz, batch=args.batch, workspace=2
        )
        best_engine = Path(engine_export)
        if best_engine != args.output / "best.engine":
            shutil.move(str(best_engine), args.output / "best.engine")
        best_engine = args.output / "best.engine"
        engine_info = inspect_engine(best_engine)

        metadata = {
            "mlflow_run_id": run.info.run_id,
            "onnx": onnx_info,
            "tensorrt": engine_info,
        }
        metadata_path = args.output / "model_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2))
        mlflow.log_artifacts(str(args.output), artifact_path="exports")

        s3 = boto3.client("s3")
        for path in (final_pt, best_onnx, best_engine, metadata_path):
            s3.upload_file(str(path), bucket, f"models/{run.info.run_id}/{path.name}")
        print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
