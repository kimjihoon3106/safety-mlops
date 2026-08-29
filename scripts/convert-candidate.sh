#!/usr/bin/env bash
set -euo pipefail
ns=mlops
status=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.status}')
[[ "$status" == EVALUATION_PASSED ]] || { echo "expected EVALUATION_PASSED, got $status" >&2; exit 1; }
job="safety-convert-$(date +%s)"
kubectl create job "$job" -n "$ns" --from=cronjob/safety-model-operator-template
kubectl wait -n "$ns" --for=condition=complete "job/$job" --timeout=1800s
kubectl logs -n "$ns" "job/$job"
