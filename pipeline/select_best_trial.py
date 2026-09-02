#!/usr/bin/env python3
import json
import os
from pathlib import Path

import boto3
import optuna


def main() -> None:
    storage = optuna.storages.RDBStorage(
        url=os.environ["OPTUNA_STORAGE"],
        heartbeat_interval=int(os.getenv("OPTUNA_HEARTBEAT_INTERVAL", "60")),
        grace_period=int(os.getenv("OPTUNA_HEARTBEAT_GRACE_PERIOD", "180")),
    )
    study = optuna.load_study(
        study_name=os.environ["OPTUNA_STUDY_NAME"],
        storage=storage,
    )
    optuna.storages.fail_stale_trials(study)
    completed = [
        trial for trial in study.trials
        if trial.state == optuna.trial.TrialState.COMPLETE
    ]
    if not completed:
        states = {state.name: 0 for state in optuna.trial.TrialState}
        for trial in study.trials:
            states[trial.state.name] += 1
        raise RuntimeError(
            f"study {study.study_name!r} has no COMPLETE trials; states={states}"
        )
    best = study.best_trial
    result = {
        "study_name": study.study_name,
        "candidate_id": os.environ["CANDIDATE_ID"],
        "dataset_version": os.environ["DATASET_VERSION"],
        "storage_backend": "postgresql",
        "study_user_attrs": study.user_attrs,
        "best_trial": best.number,
        "objective_map50_95": best.value,
        "parameters": best.params,
        "completed_trials": len(completed),
        "trials": [
            {
                "number": trial.number,
                "state": trial.state.name,
                "value": trial.value,
                "parameters": trial.params,
            }
            for trial in study.trials
        ],
    }
    destination = Path("/work/best_parameters.json")
    destination.write_text(json.dumps(best.params, separators=(",", ":")))
    report = Path("/work/best_trial.json")
    report.write_text(json.dumps(result, indent=2) + "\n")
    s3 = boto3.client("s3")
    prefix = f"artifacts/hpo/{os.environ['CANDIDATE_ID']}"
    s3.upload_file(str(report), os.environ["S3_BUCKET"], f"{prefix}/best_trial.json")
    s3.upload_file(str(report), os.environ["S3_BUCKET"], f"{prefix}/study_summary.json")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
