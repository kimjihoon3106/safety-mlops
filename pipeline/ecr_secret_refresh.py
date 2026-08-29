#!/usr/bin/env python3
import base64
import json
import os

import boto3
from kubernetes import client, config
from kubernetes.client.rest import ApiException


def main() -> None:
    namespace = os.getenv("NAMESPACE", "mlops")
    secret_name = os.getenv("ECR_SECRET_NAME", "ecr-registry")
    response = boto3.client("ecr").get_authorization_token()
    authorization = response["authorizationData"][0]
    registry = authorization["proxyEndpoint"].removeprefix("https://")
    docker_config = {"auths": {registry: {"auth": authorization["authorizationToken"]}}}
    encoded = base64.b64encode(json.dumps(docker_config).encode()).decode()

    config.load_incluster_config()
    api = client.CoreV1Api()
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=secret_name),
        type="kubernetes.io/dockerconfigjson",
        data={".dockerconfigjson": encoded},
    )
    try:
        api.replace_namespaced_secret(secret_name, namespace, body)
        action = "replaced"
    except ApiException as exc:
        if exc.status != 404:
            raise
        api.create_namespaced_secret(namespace, body)
        action = "created"
    print(json.dumps({"status": action, "secret": secret_name, "registry": registry}))


if __name__ == "__main__":
    main()
