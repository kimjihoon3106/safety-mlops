import io
import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from infer_image import infer, letterbox, postprocess


app = FastAPI(title="Safety Detection API", version="1.0.0")
TRITON_URL = os.getenv("TRITON_URL", "http://triton.mlops.svc.cluster.local:8000")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "triton_url": TRITON_URL}


@app.post("/detect")
async def detect(image: UploadFile = File(...), confidence: float = 0.25,
                 iou: float = 0.45, image_size: int = 640) -> dict:
    if image_size not in (320, 640):
        raise HTTPException(400, "image_size must be 320 or 640")
    if not 0 <= confidence <= 1 or not 0 <= iou <= 1:
        raise HTTPException(400, "confidence and iou must be between 0 and 1")
    try:
        source = Image.open(io.BytesIO(await image.read())).convert("RGB")
    except UnidentifiedImageError as exc:
        raise HTTPException(400, "uploaded file is not a supported image") from exc
    tensor, scale, padding = letterbox(source, image_size)
    output = infer(TRITON_URL, tensor)
    detections = postprocess(output, confidence, iou, scale, padding, source.size)
    return {
        "filename": image.filename,
        "image_size": {"width": source.width, "height": source.height},
        "model": "yolov8",
        "detection_count": len(detections),
        "detections": detections,
    }
