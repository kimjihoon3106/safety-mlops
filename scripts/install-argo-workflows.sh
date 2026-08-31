#!/usr/bin/env bash
set -euo pipefail

version=v4.1.0
namespace=argo
manifest="https://github.com/argoproj/argo-workflows/releases/download/${version}/install.yaml"

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side -n "$namespace" -f "$manifest"
kubectl rollout status deployment/workflow-controller -n "$namespace" --timeout=180s
kubectl rollout status deployment/argo-server -n "$namespace" --timeout=180s
kubectl get pods,service -n "$namespace"
