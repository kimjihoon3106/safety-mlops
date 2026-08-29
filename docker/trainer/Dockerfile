FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

WORKDIR /app
RUN pip install --no-cache-dir ultralytics==8.4.130 mlflow==3.5.1 boto3==1.43.82 onnx==1.22.0 kubernetes==34.1.0 pyyaml==6.0.3
COPY pipeline/train_candidate.py /app/train_candidate.py
CMD ["python", "/app/train_candidate.py"]

