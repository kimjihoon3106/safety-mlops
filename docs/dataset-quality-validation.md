# Dataset Quality Validation

The Dataset Watcher keeps the existing structural validation as a hard gate and runs this CPU-only quality analysis immediately afterwards.

```text
Roboflow download
→ basic structural validation
→ dataset quality analysis
→ validation_report.json
→ S3 upload
→ training-candidate (AWAITING_APPROVAL)
```

The report contains class and image counts, empty-label ratio, comparison with the previous version, exact-hash leakage, resolution statistics, bounding-box statistics, within-dataset duplicates, and reproducible issue details. Thresholds are managed by the GitOps ConfigMap in `gitops/policies/dataset-quality-policy.yaml`.

## Decision behavior

| Quality status | Candidate status | Default training submission |
|---|---|---|
| `PASSED` | `AWAITING_APPROVAL` | Human approval allowed |
| `WARNING` | `AWAITING_APPROVAL` | Human approval allowed after reviewing warnings |
| `MANUAL_REVIEW` | `AWAITING_APPROVAL` | Blocked by default |
| `ERROR` | `AWAITING_APPROVAL` | Blocked by default |

The Candidate state machine is unchanged. An authorized reviewer may explicitly proceed after investigation with:

```bash
scripts/submit-hpo-workflow.sh --allow-quality-override
```

The report URI and summary are exposed in the `training-candidate` ConfigMap as `validation_report_uri`, `quality_status`, `quality_warnings`, `quality_errors`, and `quality_manual_reviews`.

## Report location

```text
s3://<bucket>/datasets/<project>/v<version>/validation_report.json
```

Quality findings never modify the currently served Production model. They only control whether a newly detected Dataset Candidate may proceed to the existing manually approved training workflow.
