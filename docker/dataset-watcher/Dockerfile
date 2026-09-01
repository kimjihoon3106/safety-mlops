FROM python:3.10-slim

WORKDIR /app
RUN pip install --no-cache-dir requests==2.32.5 pyyaml==6.0.3 boto3==1.43.82 kubernetes==34.1.0 pillow==12.1.1
COPY pipeline/dataset_watcher.py /app/dataset_watcher.py
COPY pipeline/dataset_basic.py /app/dataset_basic.py
COPY pipeline/dataset_quality.py /app/dataset_quality.py
COPY pipeline/ecr_secret_refresh.py /app/ecr_secret_refresh.py
USER 65532:65532
CMD ["python", "/app/dataset_watcher.py"]
