#!/usr/bin/env bash
set -euo pipefail

namespace=mlops
candidate=training-candidate
status=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.status}')
if [[ "$status" != "EVALUATING" ]]; then
  echo "candidate status must be EVALUATING, found: $status" >&2
  exit 1
fi

run_id=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.training_run_id}')
job="safety-evaluation-${run_id:0:8}-$(date +%s)"
kubectl create job "$job" -n "$namespace" --from=cronjob/safety-evaluation-template
echo "created $job; inspect with: kubectl logs -n $namespace job/$job -f"
