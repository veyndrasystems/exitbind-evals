#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_common import capture_environment, write_json

parser = argparse.ArgumentParser()
parser.add_argument("--binary", type=Path)
parser.add_argument("--out", required=True, type=Path)
args = parser.parse_args()
write_json(args.out, {"schema_version": "environment-v1", **capture_environment(args.binary)})
print(json.dumps({"output": str(args.out), "binary_sha256": capture_environment(args.binary).get("binary_sha256")}))
