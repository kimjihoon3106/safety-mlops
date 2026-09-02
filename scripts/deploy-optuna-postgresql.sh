#!/usr/bin/env bash
set -euo pipefail

namespace=mlops
secret=optuna-postgresql
manifest=k8s/optuna-postgresql.yaml

kubectl get namespace "$namespace" >/dev/null
if ! kubectl get secret "$secret" -n "$namespace" >/dev/null 2>&1; then
  password=$(openssl rand -hex 24)
  database_url="postgresql+psycopg2://optuna:${password}@optuna-postgresql.mlops.svc.cluster.local:5432/optuna"
  kubectl create secret generic "$secret" -n "$namespace" \
    --from-literal=password="$password" \
    --from-literal=database-url="$database_url"
  unset password database_url
fi

kubectl apply -f "$manifest"
kubectl rollout status statefulset/optuna-postgresql -n "$namespace" --timeout=180s
