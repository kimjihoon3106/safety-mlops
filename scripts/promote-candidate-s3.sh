#!/usr/bin/env bash
set -euo pipefail
ns=mlops
status=$(kubectl get cm training-candidate -n "$ns" -o jsonpath='{.data.status}')
[[ "$status" == READY_FOR_PROMOTION ]] || { echo "expected READY_FOR_PROMOTION, got $status" >&2; exit 1; }
job="safety-promote-$(date +%s)"
kubectl create job "$job" -n "$ns" --from=cronjob/safety-s3-promotion-template
kubectl wait -n "$ns" --for=condition=complete "job/$job" --timeout=600s
kubectl logs -n "$ns" "job/$job"
