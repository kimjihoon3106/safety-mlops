#!/usr/bin/env bash
set -euo pipefail
ns=mlops
repo=kimjihoon3106/safety-mlops
status=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.status}')
[[ "$status" == PROMOTED_PENDING_GIT ]] || { echo "expected PROMOTED_PENDING_GIT, got $status" >&2; exit 1; }
previous=$(kubectl get cm safety-model-release -n "$ns" -o jsonpath='{.data.MODEL_VERSION}')
previous_model_uri=$(kubectl get cm safety-model-release -n "$ns" -o jsonpath='{.data.MODEL_URI}')
previous_config_uri=$(kubectl get cm safety-model-release -n "$ns" -o jsonpath='{.data.CONFIG_URI}')
previous_metadata_uri=$(kubectl get cm safety-model-release -n "$ns" -o jsonpath='{.data.METADATA_URI}')
previous_sha=$(kubectl get cm safety-model-release -n "$ns" -o jsonpath='{.data.MODEL_SHA256}')
version=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.promoted_version}')
model_uri=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.promoted_model_uri}')
config_uri=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.promoted_config_uri}')
metadata_uri=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.promoted_metadata_uri}')
sha=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.engine_sha256}')
gh workflow run promote-model.yaml --repo "$repo" -f version="$version" -f previous_version="$previous" \
  -f model_uri="$model_uri" -f config_uri="$config_uri" -f metadata_uri="$metadata_uri" -f sha256="$sha"
kubectl patch cm training-candidate -n "$ns" --type merge -p "{\"data\":{\"status\":\"DEPLOYING\",\"previous_version\":\"$previous\",\"previous_model_uri\":\"$previous_model_uri\",\"previous_config_uri\":\"$previous_config_uri\",\"previous_metadata_uri\":\"$previous_metadata_uri\",\"previous_sha256\":\"$previous_sha\"}}"
echo "promotion dispatched: $previous -> $version"
