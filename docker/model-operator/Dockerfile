FROM nvcr.io/nvidia/tritonserver:25.10-py3
WORKDIR /app
RUN pip install --no-cache-dir boto3==1.43.82 kubernetes==34.1.0
COPY pipeline/model_operator.py /app/model_operator.py
CMD ["python", "/app/model_operator.py"]
