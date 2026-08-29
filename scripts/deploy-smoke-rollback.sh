#!/usr/bin/env bash
set -euo pipefail
ns=mlops
repo=kimjihoon3106/safety-mlops
candidate=training-candidate
version=$(kubectl get cm "$candidate" -n "$ns" -o jsonpath='{.data.promoted_version}')
for _ in {1..60}; do
  kubectl annotate application safety-mlops-platform -n argocd argocd.argoproj.io/refresh=hard --overwrite >/dev/null
  revision=$(kubectl get application safety-mlops-platform -n argocd -o jsonpath='{.status.sync.revision}')
  remote=$(git ls-remote https://github.com/$repo.git refs/heads/main | cut -f1)
  [[ "$revision" == "$remote" ]] && break
  sleep 5
done
kubectl patch application safety-mlops-platform -n argocd --type merge -p "{\"operation\":{\"sync\":{\"revision\":\"$remote\",\"prune\":false}}}" >/dev/null
kubectl wait -n argocd --for=jsonpath='{.status.operationState.phase}'=Succeeded application/safety-mlops-platform --timeout=180s
kubectl rollout status deployment/triton -n "$ns" --timeout=300s
kubectl rollout status deployment/inference-api -n "$ns" --timeout=180s
kubectl patch cm "$candidate" -n "$ns" --type merge -p '{"data":{"status":"SMOKE_TESTING"}}'
if "$(dirname "$0")/run-smoke-test.sh"; then
  kubectl patch cm "$candidate" -n "$ns" --type merge -p '{"data":{"status":"PRODUCTION"}}'
  exit 0
fi
kubectl patch cm "$candidate" -n "$ns" --type merge -p '{"data":{"status":"SMOKE_TEST_FAILED"}}'
previous=$(kubectl get cm "$candidate" -n "$ns" -o jsonpath='{.data.previous_version}')
gh workflow run promote-model.yaml --repo "$repo" -f version="$previous" -f previous_version="$version" \
  -f model_uri="$(kubectl get cm "$candidate" -n "$ns" -o jsonpath='{.data.previous_model_uri}')" \
  -f config_uri="$(kubectl get cm "$candidate" -n "$ns" -o jsonpath='{.data.previous_config_uri}')" \
  -f metadata_uri="$(kubectl get cm "$candidate" -n "$ns" -o jsonpath='{.data.previous_metadata_uri}')" \
  -f sha256="$(kubectl get cm "$candidate" -n "$ns" -o jsonpath='{.data.previous_sha256}')"
kubectl patch cm "$candidate" -n "$ns" --type merge -p '{"data":{"status":"ROLLBACK"}}'
echo "smoke test failed; Git rollback dispatched to $previous" >&2
exit 1
