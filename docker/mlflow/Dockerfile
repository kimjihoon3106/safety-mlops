FROM golang:1.25-bookworm AS roles-anywhere-helper

RUN git clone --depth 1 --branch v1.8.5 https://github.com/aws/rolesanywhere-credential-helper.git /src
WORKDIR /src
RUN CGO_ENABLED=0 go build -ldflags "-X 'github.com/aws/rolesanywhere-credential-helper/cmd.Version=1.8.5' -w -s" -trimpath -o /aws_signing_helper main.go

FROM ghcr.io/mlflow/mlflow:v3.5.1

RUN pip install --no-cache-dir --timeout 120 --retries 3 boto3
COPY --from=roles-anywhere-helper /aws_signing_helper /usr/local/bin/aws_signing_helper
