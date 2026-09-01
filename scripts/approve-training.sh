#!/usr/bin/env bash
set -euo pipefail

namespace=mlops
candidate=training-candidate
allow_quality_override=false
if [[ "${1:-}" == "--allow-quality-override" ]]; then
  allow_quality_override=true
fi
status=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.status}')
if [[ "$status" != "AWAITING_APPROVAL" ]]; then
  echo "candidate status must be AWAITING_APPROVAL, found: $status" >&2
  exit 1
fi
quality_status=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.quality_status}')
if [[ "$quality_status" == "ERROR" || "$quality_status" == "MANUAL_REVIEW" ]]; then
  if [[ "$allow_quality_override" != true ]]; then
    echo "dataset quality status is $quality_status; inspect validation_report_uri and use --allow-quality-override only after manual review" >&2
    exit 1
  fi
  echo "manual quality override accepted for status=$quality_status" >&2
fi

version=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.dataset_version}')
job="safety-training-v${version}-$(date +%s)"
kubectl create job "$job" -n "$namespace" --from=cronjob/safety-training-template
kubectl patch configmap "$candidate" -n "$namespace" --type merge -p '{"data":{"status":"TRAINING"}}'
echo "created $job for dataset version $version"
