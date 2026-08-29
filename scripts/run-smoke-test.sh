#!/usr/bin/env bash
set -euo pipefail
ns=mlops
job="safety-smoke-test-$(date +%s)"
kubectl create job "$job" -n "$ns" --from=cronjob/safety-smoke-test-template
if kubectl wait -n "$ns" --for=condition=complete "job/$job" --timeout=320s; then
  kubectl logs -n "$ns" "job/$job"
else
  kubectl logs -n "$ns" "job/$job" --all-containers=true || true
  exit 1
fi
