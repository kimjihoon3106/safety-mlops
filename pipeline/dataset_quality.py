#!/usr/bin/env python3
"""CPU-only, policy-driven quality statistics for an already validated YOLO dataset."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from PIL import Image


SPLITS = ("train", "valid", "test")


def load_policy(path: Path) -> dict[str, Any]:
    policy = yaml.safe_load(path.read_text())
    if not isinstance(policy, dict):
        raise ValueError("dataset quality policy must be a YAML mapping")
    return policy


def class_schema(descriptor: dict[str, Any]) -> list[dict[str, Any]]:
    names = descriptor.get("names", [])
    if isinstance(names, dict):
        ordered = sorted(((int(key), str(value)) for key, value in names.items()))
    else:
        ordered = list(enumerate(str(value) for value in names))
    return [{"class_id": class_id, "class_name": name} for class_id, name in ordered]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def average(values: list[float]) -> float:
    return round(statistics.fmean(values), 6) if values else 0.0


def compare_schema(current: list[dict[str, Any]], previous: list[dict[str, Any]] | None) -> dict[str, Any]:
    if previous is None:
        return {"changed": False, "comparison_available": False, "current": current}
    changed = current != previous
    return {
        "changed": changed,
        "comparison_available": True,
        "previous": previous,
        "current": current,
    }


def add_issue(issues: list[dict[str, Any]], severity: str, code: str, message: str, **details: Any) -> None:
    issues.append({"severity": severity, "code": code, "message": message, "details": details})


def severity_for(policy_value: str) -> str:
    value = str(policy_value).upper()
    if value not in {"WARNING", "ERROR", "MANUAL_REVIEW"}:
        raise ValueError(f"unsupported policy severity: {policy_value}")
    return value


def analyze_dataset_quality(
    root: Path,
    dataset_version: int,
    policy: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    descriptor = yaml.safe_load((root / "data.yaml").read_text())
    schema = class_schema(descriptor)
    class_names = {item["class_id"]: item["class_name"] for item in schema}
    annotation_counts: Counter[int] = Counter()
    class_images: dict[int, set[str]] = defaultdict(set)
    split_images: dict[str, int] = {}
    image_hashes: dict[str, list[dict[str, str]]] = defaultdict(list)
    widths: list[float] = []
    heights: list[float] = []
    resolutions: Counter[str] = Counter()
    bbox_widths: list[float] = []
    bbox_heights: list[float] = []
    bbox_areas: list[float] = []
    bbox_aspects: list[float] = []
    empty_images = 0
    total_labels = 0

    for split in SPLITS:
        images = sorted(path for path in (root / split / "images").iterdir() if path.is_file())
        split_images[split] = len(images)
        for image_path in images:
            relative = image_path.relative_to(root).as_posix()
            with Image.open(image_path) as image:
                width, height = image.size
            widths.append(float(width))
            heights.append(float(height))
            resolutions[f"{width}x{height}"] += 1
            image_hashes[sha256_file(image_path)].append({"split": split, "path": relative})

            label_path = root / split / "labels" / f"{image_path.stem}.txt"
            lines = [] if not label_path.is_file() else [line for line in label_path.read_text().splitlines() if line.strip()]
            if not lines:
                empty_images += 1
                continue
            total_labels += 1
            for line in lines:
                class_id_text, _, _, width_text, height_text = line.split()
                class_id = int(class_id_text)
                box_width, box_height = float(width_text), float(height_text)
                area = box_width * box_height
                aspect = max(box_width / box_height, box_height / box_width) if box_width and box_height else float("inf")
                annotation_counts[class_id] += 1
                class_images[class_id].add(relative)
                bbox_widths.append(box_width)
                bbox_heights.append(box_height)
                bbox_areas.append(area)
                bbox_aspects.append(aspect)

    total_images = sum(split_images.values())
    total_annotations = sum(annotation_counts.values())
    empty_ratio = empty_images / total_images if total_images else 0.0
    distribution = {}
    for class_id, class_name in class_names.items():
        count = annotation_counts[class_id]
        distribution[class_name] = {
            "class_id": class_id,
            "class_name": class_name,
            "annotation_count": count,
            "image_count": len(class_images[class_id]),
            "percentage": round(count * 100 / total_annotations, 4) if total_annotations else 0.0,
        }

    duplicate_groups = [items for items in image_hashes.values() if len(items) > 1]
    leakage = {"train_valid_duplicates": 0, "train_test_duplicates": 0, "valid_test_duplicates": 0, "examples": []}
    pair_keys = {("train", "valid"): "train_valid_duplicates", ("train", "test"): "train_test_duplicates", ("valid", "test"): "valid_test_duplicates"}
    for group in duplicate_groups:
        by_split = Counter(item["split"] for item in group)
        for pair, key in pair_keys.items():
            leakage[key] += by_split[pair[0]] * by_split[pair[1]]
        if len(leakage["examples"]) < int(policy["duplicates"]["max_reported_examples"]):
            leakage["examples"].append(group)

    tiny_area = float(policy["bounding_boxes"]["tiny_area"])
    large_area = float(policy["bounding_boxes"]["large_area"])
    extreme_aspect = float(policy["bounding_boxes"]["extreme_aspect_ratio"])
    tiny_boxes = sum(area < tiny_area for area in bbox_areas)
    large_boxes = sum(area > large_area for area in bbox_areas)
    extreme_aspect_boxes = sum(aspect > extreme_aspect for aspect in bbox_aspects)
    small_cfg = policy["small_image"]
    small_images = sum(
        width < int(small_cfg["min_width"]) or height < int(small_cfg["min_height"])
        for width, height in zip(widths, heights)
    )
    previous_count = None if previous is None else previous.get("image_count")
    change = None if previous_count is None else total_images - int(previous_count)
    change_percent = None if not previous_count else round(change * 100 / int(previous_count), 4)
    schema_result = compare_schema(schema, None if previous is None else previous.get("class_schema"))

    issues: list[dict[str, Any]] = []
    empty_cfg = policy["empty_label_ratio"]
    if empty_ratio >= float(empty_cfg["error"]):
        add_issue(issues, "ERROR", "EMPTY_LABEL_RATIO_ERROR", "empty label ratio exceeds error threshold", ratio=empty_ratio)
    elif empty_ratio >= float(empty_cfg["warning"]):
        add_issue(issues, "WARNING", "EMPTY_LABEL_RATIO_WARNING", "empty label ratio exceeds warning threshold", ratio=empty_ratio)

    if change_percent is not None and abs(change_percent) >= float(policy["dataset_size_change"]["warning_percent"]):
        add_issue(issues, "WARNING", "DATASET_SIZE_CHANGED", "dataset image count changed abruptly", change_percent=change_percent)
    if schema_result["changed"]:
        add_issue(issues, severity_for(policy["class_schema"]["change"]), "CLASS_SCHEMA_CHANGED", "class id/name/order differs from previous dataset")

    class_cfg = policy["class_distribution"]
    for name, item in distribution.items():
        percentage = item["percentage"]
        if percentage < float(class_cfg["warning_min_percent"]):
            add_issue(issues, "WARNING", "CLASS_UNDERREPRESENTED", "class annotation share is below threshold", class_name=name, percentage=percentage)
        if percentage > float(class_cfg["warning_max_percent"]):
            add_issue(issues, "WARNING", "CLASS_DOMINANT", "class annotation share is above threshold", class_name=name, percentage=percentage)

    for key in ("train_valid_duplicates", "train_test_duplicates", "valid_test_duplicates"):
        if leakage[key]:
            policy_key = key.removesuffix("_duplicates")
            add_issue(issues, severity_for(policy["leakage"][policy_key]), "DATASET_LEAKAGE", f"exact duplicates detected for {policy_key}", duplicate_pairs=leakage[key])

    small_ratio = small_images / total_images if total_images else 0.0
    if small_ratio >= float(small_cfg["warning_ratio"]):
        add_issue(issues, "WARNING", "SMALL_IMAGE_RATIO_WARNING", "small image ratio exceeds threshold", ratio=small_ratio)
    bbox_cfg = policy["bounding_boxes"]
    for count, code, label in (
        (tiny_boxes, "TINY_BBOX_RATIO_WARNING", "tiny bounding box"),
        (large_boxes, "LARGE_BBOX_RATIO_WARNING", "large bounding box"),
        (extreme_aspect_boxes, "EXTREME_ASPECT_RATIO_WARNING", "extreme aspect ratio bounding box"),
    ):
        ratio = count / total_annotations if total_annotations else 0.0
        if ratio >= float(bbox_cfg["warning_ratio"]):
            add_issue(issues, "WARNING", code, f"{label} ratio exceeds threshold", count=count, ratio=ratio)

    duplicate_cfg = policy["duplicates"]
    duplicate_image_count = sum(len(group) for group in duplicate_groups)
    if duplicate_groups and str(duplicate_cfg["within_split"]).lower() != "ignore":
        within_split_groups = sum(1 for group in duplicate_groups if len({item["split"] for item in group}) == 1)
        if within_split_groups:
            add_issue(issues, severity_for(duplicate_cfg["within_split"]), "DUPLICATE_IMAGES", "exact duplicate images detected within a split", duplicate_groups=within_split_groups)

    counts = Counter(issue["severity"] for issue in issues)
    if counts["ERROR"]:
        status = "ERROR"
    elif counts["MANUAL_REVIEW"]:
        status = "MANUAL_REVIEW"
    elif counts["WARNING"]:
        status = "WARNING"
    else:
        status = "PASSED"

    return {
        "report_version": "1.0",
        "dataset_version": dataset_version,
        "previous_dataset_version": None if previous is None else previous.get("dataset_version"),
        "summary": {"status": status, "warnings": counts["WARNING"], "errors": counts["ERROR"], "manual_reviews": counts["MANUAL_REVIEW"]},
        "dataset": {"image_count": total_images, "label_count": total_labels, "annotation_count": total_annotations, "splits": split_images},
        "class_distribution": distribution,
        "empty_labels": {"image_count": empty_images, "ratio": round(empty_ratio, 6)},
        "dataset_size_change": {
            "previous_image_count": previous_count, "current_image_count": total_images,
            "image_count_change": change, "image_count_change_percent": change_percent,
        },
        "class_schema": schema_result,
        "leakage": leakage,
        "image_resolution": {
            "min_width": min(widths, default=0), "max_width": max(widths, default=0),
            "min_height": min(heights, default=0), "max_height": max(heights, default=0),
            "average_width": average(widths), "average_height": average(heights),
            "small_images": small_images, "small_image_ratio": round(small_ratio, 6),
            "resolution_distribution": dict(sorted(resolutions.items())),
        },
        "bounding_boxes": {
            "total": total_annotations,
            "width": {"min": min(bbox_widths, default=0), "max": max(bbox_widths, default=0), "average": average(bbox_widths)},
            "height": {"min": min(bbox_heights, default=0), "max": max(bbox_heights, default=0), "average": average(bbox_heights)},
            "area": {"min": min(bbox_areas, default=0), "max": max(bbox_areas, default=0), "average": average(bbox_areas)},
            "tiny_boxes": tiny_boxes, "large_boxes": large_boxes, "extreme_aspect_boxes": extreme_aspect_boxes,
        },
        "duplicates": {"duplicate_groups": len(duplicate_groups), "duplicate_images": duplicate_image_count},
        "issues": issues,
        "policy": policy,
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
