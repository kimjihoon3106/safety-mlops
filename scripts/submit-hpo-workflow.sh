#!/usr/bin/env bash
set -euo pipefail

namespace=mlops
candidate=training-candidate
status=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.status}')
if [[ "$status" != "AWAITING_APPROVAL" ]]; then
  echo "candidate status must be AWAITING_APPROVAL, found: $status" >&2
  exit 1
fi

dataset_version=$(kubectl get configmap "$candidate" -n "$namespace" -o jsonpath='{.data.dataset_version}')
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
      - {name: model_family, value: yolov8}
      - {name: base_model, value: yolov8s.pt}
      - {name: epochs, value: "50"}
      - {name: hpo_epochs, value: "10"}
      - {name: hpo_trial_count, value: "6"}
      - {name: hpo_parallelism, value: "1"}
      - {name: evaluation_policy_version, value: v1}
      - {name: git_commit, value: e78067fdc67b507f791aab30cfb0119961a03e04}
EOF
)
kubectl patch configmap "$candidate" -n "$namespace" --type merge \
  -p "{\"data\":{\"status\":\"HPO_RUNNING\",\"workflow_name\":\"${workflow#*/}\",\"candidate_id\":\"${candidate_id}\"}}"
echo "created ${workflow}; production remains unchanged"
