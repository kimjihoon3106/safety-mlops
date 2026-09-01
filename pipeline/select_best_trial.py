#!/usr/bin/env python3
import json
import os
import sqlite3
from pathlib import Path

import boto3
import optuna


def main() -> None:
    study = optuna.load_study(
        study_name=os.environ["OPTUNA_STUDY_NAME"],
        storage=os.getenv("OPTUNA_STORAGE", "sqlite:////work/optuna/study.db"),
    )
    best = study.best_trial
    result = {
        "study_name": study.study_name,
        "candidate_id": os.environ["CANDIDATE_ID"],
        "dataset_version": os.environ["DATASET_VERSION"],
        "best_trial": best.number,
        "objective_map50_95": best.value,
        "parameters": best.params,
        "completed_trials": len([
            t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE
        ]),
    }
    destination = Path("/work/best_parameters.json")
    destination.write_text(json.dumps(best.params, separators=(",", ":")))
    report = Path("/work/best_trial.json")
    report.write_text(json.dumps(result, indent=2) + "\n")
    snapshot = Path("/work/optuna/study-snapshot.db")
    with sqlite3.connect("/work/optuna/study.db") as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    s3 = boto3.client("s3")
    prefix = f"artifacts/hpo/{os.environ['CANDIDATE_ID']}"
    s3.upload_file(str(report), os.environ["S3_BUCKET"], f"{prefix}/best_trial.json")
    s3.upload_file(str(snapshot), os.environ["S3_BUCKET"], f"{prefix}/study.db")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
