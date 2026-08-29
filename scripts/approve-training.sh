#!/usr/bin/env bash
set -euo pipefail

namespace=mlops
candidate=training-candidate
status=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.status}')
if [[ "$status" != "AWAITING_APPROVAL" ]]; then
  echo "candidate status must be AWAITING_APPROVAL, found: $status" >&2
  exit 1
fi

version=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.dataset_version}')
job="safety-training-v${version}-$(date +%s)"
kubectl create job "$job" -n "$namespace" --from=cronjob/safety-training-template
kubectl patch configmap "$candidate" -n "$namespace" --type merge -p '{"data":{"status":"TRAINING"}}'
echo "created $job for dataset version $version"

