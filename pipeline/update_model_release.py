#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

import yaml


parser = argparse.ArgumentParser()
parser.add_argument("--version", required=True)
parser.add_argument("--model-uri", required=True)
parser.add_argument("--config-uri", required=True)
parser.add_argument("--metadata-uri", required=True)
parser.add_argument("--sha256", required=True)
args = parser.parse_args()
path = Path("gitops/production/model-release.yaml")
document = yaml.safe_load(path.read_text())
data = document["data"]
data.update({"MODEL_VERSION": args.version, "MODEL_URI": args.model_uri,
             "CONFIG_URI": args.config_uri, "METADATA_URI": args.metadata_uri,
             "MODEL_SHA256": args.sha256})
data["model.yaml"] = yaml.safe_dump({"model": {"name": "safety", "version": args.version,
    "artifact": args.model_uri, "configArtifact": args.config_uri, "sha256": args.sha256}}, sort_keys=False)
path.write_text(yaml.safe_dump(document, sort_keys=False))
for deployment_path in (Path("k8s/triton.yaml"), Path("k8s/inference-api.yaml")):
    source = deployment_path.read_text()
    updated, count = re.subn(r"(safety\.mlops/model-version:\s*)\S+", rf"\g<1>{args.version}", source, count=1)
    if count != 1:
        raise RuntimeError(f"model version annotation missing: {deployment_path}")
    deployment_path.write_text(updated)
release = Path(f"model-releases/safety/{args.version}.json")
if not release.exists():
    release.write_text(json.dumps({"model_version": args.version, "engine_sha256": args.sha256,
        "model_uri": args.model_uri, "config_uri": args.config_uri,
        "metadata_uri": args.metadata_uri}, indent=2) + "\n")
