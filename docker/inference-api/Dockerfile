FROM python:3.10-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.116.1 uvicorn==0.35.0 numpy==2.2.6 pillow==11.3.0 python-multipart==0.0.20
COPY pipeline/infer_image.py /app/infer_image.py
COPY api/app.py /app/app.py

USER 65532:65532
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
