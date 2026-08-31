FROM ubuntu:20.04 AS roles-anywhere-helper

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates curl gcc git libc6-dev \
    && curl -fsSLo /tmp/go.tar.gz https://go.dev/dl/go1.26.6.linux-amd64.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && git clone --depth 1 --branch v1.8.5 https://github.com/aws/rolesanywhere-credential-helper.git /src
WORKDIR /src
RUN CGO_ENABLED=1 /usr/local/go/bin/go build -ldflags "-X 'github.com/aws/rolesanywhere-credential-helper/cmd.Version=1.8.5' -w -s" -trimpath -o /aws_signing_helper main.go

FROM ghcr.io/mlflow/mlflow:v3.5.1

RUN pip install --no-cache-dir --timeout 120 --retries 3 boto3
COPY --from=roles-anywhere-helper /aws_signing_helper /usr/local/bin/aws_signing_helper
