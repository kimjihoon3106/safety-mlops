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

dataset_version=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.dataset_version}')
dataset_s3_uri=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.s3_uri}')
candidate_id="candidate-${dataset_version}"
workflow=$(kubectl create -n "$namespace" -o name -f - <<EOF
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: safety-hpo-${dataset_version}-
  labels:
    mlops.safety/candidate-id: ${candidate_id}
    mlops.safety/dataset-version: ${dataset_version}
spec:
  workflowTemplateRef:
    name: safety-ml-lifecycle
  arguments:
    parameters:
      - {name: candidate_id, value: "${candidate_id}"}
      - {name: dataset_version, value: "${dataset_version}"}
      - {name: dataset_s3_uri, value: "${dataset_s3_uri}"}
      - {name: run_mode, value: FULL}
      - {name: model_family, value: yolov8}
      - {name: base_model, value: yolov8s.pt}
      - {name: epochs, value: "50"}
      - {name: hpo_epochs, value: "10"}
      - {name: hpo_trial_count, value: "6"}
      - {name: hpo_parallelism, value: "1"}
      - {name: hpo_startup_trials, value: "1"}
      - {name: evaluation_policy_version, value: v1}
      - {name: git_commit, value: 6a90dcf5d156d8186c199bcb149ec81490ec7818}
EOF
)
kubectl patch configmap "$candidate" -n "$namespace" --type merge \
  -p "{\"data\":{\"status\":\"HPO_RUNNING\",\"workflow_name\":\"${workflow#*/}\",\"candidate_id\":\"${candidate_id}\"}}"
echo "created ${workflow}; production remains unchanged"
