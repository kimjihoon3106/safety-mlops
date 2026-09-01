#!/usr/bin/env python3
"""Existing hard-fail structural validation for downloaded YOLO datasets."""

from pathlib import Path

import yaml
from PIL import Image


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
