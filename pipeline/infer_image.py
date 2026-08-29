#!/usr/bin/env python3
import argparse
import json
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


CLASS_NAMES = [
    "Excavator", "Gloves", "Hardhat", "Ladder", "Mask", "NO-Hardhat",
    "NO-Mask", "NO-Safety Vest", "Person", "SUV", "Safety Cone",
    "Safety Vest", "bus", "dump truck", "fire hydrant", "machinery",
    "mini-van", "sedan", "semi", "trailer", "truck and trailer", "truck",
    "van", "vehicle", "wheel loader",
]


def letterbox(image: Image.Image, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    image = image.convert("RGB")
    width, height = image.size
    scale = min(size / width, size / height)
    resized = (round(width * scale), round(height * scale))
    pad_x = (size - resized[0]) // 2
    pad_y = (size - resized[1]) // 2
    canvas = Image.new("RGB", (size, size), (114, 114, 114))
    canvas.paste(image.resize(resized, Image.Resampling.BILINEAR), (pad_x, pad_y))
    array = np.asarray(canvas, dtype=np.float32) / 255.0
    return np.transpose(array, (2, 0, 1))[None, ...], scale, (pad_x, pad_y)


def iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    x1 = np.maximum(box[0], boxes[:, 0])
    y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2])
    y2 = np.minimum(box[3], boxes[:, 3])
    intersection = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    box_area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
    areas = np.maximum(0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0, boxes[:, 3] - boxes[:, 1])
    return intersection / np.maximum(box_area + areas - intersection, 1e-7)


def nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, threshold: float) -> list[int]:
    keep = []
    for class_id in np.unique(classes):
        indices = np.where(classes == class_id)[0]
        order = indices[np.argsort(scores[indices])[::-1]]
        while order.size:
            current = int(order[0])
            keep.append(current)
            order = order[1:]
            if order.size:
                order = order[iou(boxes[current], boxes[order]) <= threshold]
    return sorted(keep, key=lambda index: float(scores[index]), reverse=True)


def infer(url: str, tensor: np.ndarray) -> np.ndarray:
    payload = {
        "inputs": [{
            "name": "images", "shape": list(tensor.shape), "datatype": "FP32",
            "data": tensor.reshape(-1).tolist(),
        }],
        "outputs": [{"name": "output0", "parameters": {"binary_data": False}}],
    }
    request = urllib.request.Request(
        f"{url.rstrip('/')}/v2/models/yolov8/infer",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.load(response)
    output = result["outputs"][0]
    return np.asarray(output["data"], dtype=np.float32).reshape(output["shape"])


def postprocess(output: np.ndarray, confidence: float, nms_iou: float,
                scale: float, padding: tuple[int, int], original_size: tuple[int, int]) -> list[dict]:
    predictions = output[0].T
    class_scores = predictions[:, 4:]
    class_ids = np.argmax(class_scores, axis=1)
    scores = class_scores[np.arange(len(predictions)), class_ids]
    predictions, class_ids, scores = predictions[scores >= confidence], class_ids[scores >= confidence], scores[scores >= confidence]
    if not len(predictions):
        return []
    xywh = predictions[:, :4]
    boxes = np.column_stack((xywh[:, 0] - xywh[:, 2] / 2, xywh[:, 1] - xywh[:, 3] / 2,
                             xywh[:, 0] + xywh[:, 2] / 2, xywh[:, 1] + xywh[:, 3] / 2))
    selected = nms(boxes, scores, class_ids, nms_iou)
    width, height = original_size
    detections = []
    for index in selected:
        box = boxes[index].copy()
        box[[0, 2]] = (box[[0, 2]] - padding[0]) / scale
        box[[1, 3]] = (box[[1, 3]] - padding[1]) / scale
        box[[0, 2]] = np.clip(box[[0, 2]], 0, width)
        box[[1, 3]] = np.clip(box[[1, 3]], 0, height)
        detections.append({
            "class_id": int(class_ids[index]), "class_name": CLASS_NAMES[int(class_ids[index])],
            "confidence": round(float(scores[index]), 4), "box_xyxy": [round(float(value), 1) for value in box],
        })
    return detections


def draw_result(image: Image.Image, detections: list[dict]) -> Image.Image:
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)
    font = ImageFont.load_default()
    for detection in detections:
        box = detection["box_xyxy"]
        label = f'{detection["class_name"]} {detection["confidence"]:.2f}'
        draw.rectangle(box, outline=(255, 40, 40), width=3)
        left, top, right, bottom = draw.textbbox((box[0], box[1]), label, font=font)
        draw.rectangle((left - 2, top - 2, right + 2, bottom + 2), fill=(255, 40, 40))
        draw.text((box[0], box[1]), label, fill="white", font=font)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, default=Path("result.jpg"))
    parser.add_argument("--imgsz", type=int, choices=(320, 640), default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.45)
    args = parser.parse_args()

    image = Image.open(args.image)
    tensor, scale, padding = letterbox(image, args.imgsz)
    output = infer(args.url, tensor)
    detections = postprocess(output, args.conf, args.iou, scale, padding, image.size)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    draw_result(image, detections).save(args.output, quality=95)
    json_path = args.output.with_suffix(".json")
    json_path.write_text(json.dumps({"image": str(args.image), "detections": detections}, indent=2) + "\n")
    print(json.dumps({"output_image": str(args.output), "output_json": str(json_path),
                      "detection_count": len(detections), "detections": detections}, indent=2))


if __name__ == "__main__":
    main()
