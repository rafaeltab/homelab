#!/usr/bin/env python3
"""Transform exported Grafana dashboards and deterministically validate copies.

Export inputs are Grafana GET /api/dashboards/uid/:uid response objects. This
script deliberately changes only top-level dashboard id/version and datasource
UID fields. It never reads credentials or contacts Grafana.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

UID_MAP = {
    "grafanacloud-prom": "mimir",
    "grafanacloud-logs": "loki",
    "grafanacloud-traces": "tempo",
}
EXPECTED = {
    "hermes-usage": {
        "title": "Hermes Usage",
        "panels": 21,
        "folder_uid": "hermes",
        "portable_sha256": "a4b424f4b38e06f04e43f024c785822410c0d13d0ea36ad15fb7630ab861c642",
    },
    "hermes-reliability": {
        "title": "Hermes Reliability",
        "panels": 12,
        "folder_uid": "hermes",
        "portable_sha256": "21472ca5bb9528d95849018fbb1c05197e325a960061f919d152fcd44a6524b0",
    },
    "adrk6nd": {
        "title": "Host Metrics (opentelemetry)",
        "panels": 25,
        "folder_uid": "",
        "portable_sha256": "b1cca9296745362a08de88d141a0374523c17f4a61057a7919573cb3b301fcc4",
    },
}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def portable(dashboard: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dashboard)
    result.pop("id", None)
    result.pop("version", None)
    return result


def rewrite_datasource_uids(value: Any, mapping: dict[str, str]) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"uid", "datasourceUid"} and isinstance(child, str) and child in mapping:
                value[key] = mapping[child]
                changed += 1
            else:
                changed += rewrite_datasource_uids(child, mapping)
    elif isinstance(value, list):
        for child in value:
            changed += rewrite_datasource_uids(child, mapping)
    return changed


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source-dir", type=Path, help="directory containing <uid>.response.json exports")
    mode.add_argument("--check", action="store_true", help="validate committed outputs without source exports")
    parser.add_argument("--output-dir", type=Path, default=Path("cluster/workloads/grafana/dashboards"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    for uid, expected in EXPECTED.items():
        output_path = args.output_dir / f"{uid}.json"
        if args.source_dir:
            response = json.loads((args.source_dir / f"{uid}.response.json").read_text())
            source = response["dashboard"]
            source_portable = portable(source)
            source_hash = sha(source_portable)
            if expected["portable_sha256"] != "TO_BE_GENERATED" and source_hash != expected["portable_sha256"]:
                raise SystemExit(f"{uid}: source portable hash changed: {source_hash}")
            folder_uid = response.get("meta", {}).get("folderUid", "")
            if folder_uid != expected["folder_uid"]:
                raise SystemExit(f"{uid}: unexpected source folder UID {folder_uid!r}")
            transformed = copy.deepcopy(source_portable)
            replacements = rewrite_datasource_uids(transformed, UID_MAP)
            output_path.write_bytes(json.dumps(transformed, sort_keys=True, indent=2).encode() + b"\n")
            print(f"{uid}: source_portable_sha256={source_hash} replacements={replacements}")

        dashboard = json.loads(output_path.read_text())
        if dashboard.get("uid") != uid:
            raise SystemExit(f"{uid}: output UID is {dashboard.get('uid')!r}")
        if dashboard.get("title") != expected["title"] or len(dashboard.get("panels", [])) != expected["panels"]:
            raise SystemExit(f"{uid}: output title/panel count mismatch")
        if "id" in dashboard or "version" in dashboard:
            raise SystemExit(f"{uid}: volatile id/version was retained")
        serialized = canonical(dashboard).decode()
        stale = [old for old in UID_MAP if old in serialized]
        if stale:
            raise SystemExit(f"{uid}: stale datasource UIDs: {stale}")

        # Reverse only datasource UID fields; equality proves no other JSON changed.
        reversed_dashboard = copy.deepcopy(dashboard)
        rewrite_datasource_uids(reversed_dashboard, {v: k for k, v in UID_MAP.items()})
        portable_hash = sha(reversed_dashboard)
        expected_hash = expected["portable_sha256"]
        if expected_hash != "TO_BE_GENERATED" and portable_hash != expected_hash:
            raise SystemExit(f"{uid}: transformed portable hash mismatch: {portable_hash}")
        print(f"{uid}: uid/title/panels OK, no stale source UIDs, portable_sha256={portable_hash}")

    actual_outputs = {path.stem for path in args.output_dir.glob("*.json")}
    if actual_outputs != set(EXPECTED):
        raise SystemExit(f"unexpected dashboard output set: {sorted(actual_outputs)}")


if __name__ == "__main__":
    main()
