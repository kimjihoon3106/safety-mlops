#!/usr/bin/env python3
import argparse
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests
import yaml


def api_key() -> str:
    value = os.getenv("ROBOFLOW_API_KEY")
    if value:
        return value.strip()
    key_file = Path.home() / ".config/mlops/roboflow_api_key"
    if key_file.is_file():
        return key_file.read_text().strip()
    raise RuntimeError("Set ROBOFLOW_API_KEY or create ~/.config/mlops/roboflow_api_key")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default="roboflow-universe-projects")
    parser.add_argument("--project", default="construction-site-safety")
    parser.add_argument("--version", type=int, default=30)
    parser.add_argument("--output", type=Path, default=Path("data/construction-site-safety-30"))
    args = parser.parse_args()

    url = (
        f"https://universe.roboflow.com/{args.workspace}/{args.project}"
        f"/dataset/{args.version}/download/yolov8"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".zip") as archive:
        with requests.get(url, params={"api_key": api_key()}, stream=True, timeout=120) as response:
            if not response.ok:
                raise RuntimeError(f"Roboflow download failed with HTTP {response.status_code}")
            with open(archive.name, "wb") as destination:
                shutil.copyfileobj(response.raw, destination)
        if not zipfile.is_zipfile(archive.name):
            raise RuntimeError("Roboflow response was not a ZIP archive")
        with zipfile.ZipFile(archive.name) as source:
            source.extractall(args.output)

    data_yaml = args.output / "data.yaml"
    if not data_yaml.is_file():
        raise RuntimeError(f"Missing dataset descriptor: {data_yaml}")
    descriptor = yaml.safe_load(data_yaml.read_text())
    descriptor.update({"train": "train/images", "val": "valid/images", "test": "test/images"})
    data_yaml.write_text(yaml.safe_dump(descriptor, sort_keys=False))
    print(data_yaml.resolve())


if __name__ == "__main__":
    main()
