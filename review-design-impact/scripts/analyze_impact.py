#!/usr/bin/env python3
"""Scan every compiled ChangeCase and aggregate historically supported impacts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SPEC_TO_CASE_FIELDS = {
    "business_objects": "business_objects",
    "capabilities": "capabilities",
    "change_types": "change_types",
    "actions": "actions",
    "states": "states",
    "actors": "actors",
    "triggers": "triggers",
    "invariants": "business_invariants",
    "changed_rules": "changed_rules",
}


def normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def load_aliases(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    raw = load_object(path)
    aliases: dict[str, str] = {}
    for alias, canonical in raw.items():
        if not isinstance(alias, str) or not isinstance(canonical, str):
            raise ValueError("Alias keys and values must be strings")
        aliases[normalize(alias)] = normalize(canonical)
    return aliases


def canonical(value: str, aliases: dict[str, str]) -> str:
    current = normalize(value)
    visited: set[str] = set()
    while current in aliases and current not in visited:
        visited.add(current)
        current = aliases[current]
    return current


def string_set(
    value: Any, aliases: dict[str, str]
) -> tuple[set[str], dict[str, str]]:
    if not isinstance(value, list):
        return set(), {}
    result: set[str] = set()
    display: dict[str, str] = {}
    for item in value:
        if isinstance(item, str) and item.strip():
            key = canonical(item, aliases)
            result.add(key)
            display.setdefault(key, item.strip())
    return result, display


def evidence_case_id(case_id: str) -> dict[str, str]:
    return {"type": "historical", "case_id": case_id}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Full-scan a compiled local design history database."
    )
    parser.add_argument("--db", required=True, help="Compiled history SQLite")
    parser.add_argument("--change-spec", required=True, help="ChangeSpec JSON")
    parser.add_argument("--aliases", help="Optional JSON alias-to-canonical mapping")
    parser.add_argument("--output", required=True, help="Impact JSON output")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    spec_path = Path(args.change_spec).resolve()
    alias_path = Path(args.aliases).resolve() if args.aliases else None
    if not db_path.is_file():
        print(f"History database not found: {db_path}", file=sys.stderr)
        return 2

    try:
        spec = load_object(spec_path)
        aliases = load_aliases(alias_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    spec_terms: dict[str, set[str]] = {}
    for spec_field in SPEC_TO_CASE_FIELDS:
        spec_terms[spec_field], _ = string_set(spec.get(spec_field, []), aliases)

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT case_id, title, doc_path, domain, full_json FROM cases ORDER BY case_id"
        ).fetchall()
        matched_cases: list[dict[str, Any]] = []
        matched_case_ids: set[str] = set()

        for row in rows:
            case = json.loads(row["full_json"])
            reasons: list[dict[str, Any]] = []
            for spec_field, case_field in SPEC_TO_CASE_FIELDS.items():
                wanted = spec_terms[spec_field]
                actual, actual_display = string_set(case.get(case_field, []), aliases)
                overlap = sorted(wanted & actual)
                if overlap:
                    reasons.append(
                        {
                            "field": spec_field,
                            "values": [actual_display.get(item, item) for item in overlap],
                        }
                    )
            if reasons:
                matched_case_ids.add(row["case_id"])
                matched_cases.append(
                    {
                        "case_id": row["case_id"],
                        "title": row["title"],
                        "source": row["doc_path"],
                        "domain": row["domain"],
                        "match_reasons": reasons,
                    }
                )

        scenarios: dict[str, dict[str, Any]] = {}
        services: dict[str, dict[str, Any]] = {}
        omissions: list[dict[str, Any]] = []

        if matched_case_ids:
            placeholders = ",".join("?" for _ in matched_case_ids)
            case_id_args = sorted(matched_case_ids)
            scenario_rows = connection.execute(
                f"""SELECT case_id, name, precondition, trigger_text,
                           expected_behavior, evidence_json
                    FROM scenarios WHERE case_id IN ({placeholders})
                    ORDER BY name, case_id""",
                case_id_args,
            ).fetchall()
            for row in scenario_rows:
                key = canonical(row["name"], aliases)
                item = scenarios.setdefault(
                    key,
                    {
                        "scenario": row["name"],
                        "preconditions": [],
                        "triggers": [],
                        "expected_behaviors": [],
                        "case_ids": [],
                        "evidence": [],
                    },
                )
                for target, value in (
                    ("preconditions", row["precondition"]),
                    ("triggers", row["trigger_text"]),
                    ("expected_behaviors", row["expected_behavior"]),
                ):
                    if value and value not in item[target]:
                        item[target].append(value)
                if row["case_id"] not in item["case_ids"]:
                    item["case_ids"].append(row["case_id"])
                item["evidence"].append(
                    {
                        **evidence_case_id(row["case_id"]),
                        "detail": json.loads(row["evidence_json"]),
                    }
                )

            service_rows = connection.execute(
                f"""SELECT case_id, service, normalized_service, responsibility,
                           asset_types_json, modification, evidence_json
                    FROM service_changes WHERE case_id IN ({placeholders})
                    ORDER BY normalized_service, case_id, service_change_id""",
                case_id_args,
            ).fetchall()
            for row in service_rows:
                key = canonical(row["service"], aliases)
                item = services.setdefault(
                    key,
                    {
                        "service": row["service"],
                        "responsibilities": [],
                        "asset_types": [],
                        "modifications": [],
                        "case_ids": [],
                        "evidence": [],
                        "source": "matched-history",
                    },
                )
                responsibility = row["responsibility"]
                if responsibility and responsibility not in item["responsibilities"]:
                    item["responsibilities"].append(responsibility)
                for asset_type in json.loads(row["asset_types_json"]):
                    if asset_type not in item["asset_types"]:
                        item["asset_types"].append(asset_type)
                modification = row["modification"]
                if modification and modification not in item["modifications"]:
                    item["modifications"].append(modification)
                if row["case_id"] not in item["case_ids"]:
                    item["case_ids"].append(row["case_id"])
                item["evidence"].append(
                    {
                        **evidence_case_id(row["case_id"]),
                        "detail": json.loads(row["evidence_json"]),
                    }
                )

            omission_rows = connection.execute(
                f"""SELECT case_id, description, severity, source, evidence_json
                    FROM historical_omissions WHERE case_id IN ({placeholders})
                    ORDER BY case_id, omission_id""",
                case_id_args,
            ).fetchall()
            for row in omission_rows:
                omissions.append(
                    {
                        "case_id": row["case_id"],
                        "description": row["description"],
                        "severity": row["severity"],
                        "source": row["source"],
                        "evidence": json.loads(row["evidence_json"]),
                    }
                )

        direct_services = set(services)
        cochange_rows = connection.execute(
            "SELECT service_a, service_b, case_count, case_ids_json FROM service_cochange ORDER BY service_a, service_b"
        ).fetchall()
        cochanges: list[dict[str, Any]] = []
        for row in cochange_rows:
            left = canonical(row["service_a"], aliases)
            right = canonical(row["service_b"], aliases)
            if left not in direct_services and right not in direct_services:
                continue
            cochanges.append(
                {
                    "service_a": row["service_a"],
                    "service_b": row["service_b"],
                    "case_count": row["case_count"],
                    "case_ids": json.loads(row["case_ids_json"]),
                }
            )
            for key, display in ((left, row["service_a"]), (right, row["service_b"])):
                if key not in services:
                    services[key] = {
                        "service": display,
                        "responsibilities": [],
                        "asset_types": [],
                        "modifications": [],
                        "case_ids": json.loads(row["case_ids_json"]),
                        "evidence": [
                            {
                                "type": "historical-cochange",
                                "case_count": row["case_count"],
                                "paired_services": [row["service_a"], row["service_b"]],
                            }
                        ],
                        "source": "cochange-candidate",
                    }

        issues: list[str] = []
        if not spec.get("before_behaviors"):
            issues.append("ChangeSpec has no before_behaviors")
        if not spec.get("after_behaviors"):
            issues.append("ChangeSpec has no after_behaviors")
        if not spec.get("business_objects"):
            issues.append("ChangeSpec has no business_objects")
        if not spec.get("change_types"):
            issues.append("ChangeSpec has no change_types")

        result = {
            "schema_version": 1,
            "change_spec": spec,
            "scan": {
                "mode": "full-corpus",
                "uses_embeddings": False,
                "uses_top_k": False,
                "total_cases": len(rows),
                "matched_cases": len(matched_cases),
            },
            "matched_cases": matched_cases,
            "historical_scenarios": sorted(
                scenarios.values(), key=lambda item: item["scenario"].casefold()
            ),
            "candidate_services": sorted(
                services.values(), key=lambda item: item["service"].casefold()
            ),
            "service_cochanges": cochanges,
            "historical_omissions": omissions,
            "change_spec_issues": issues,
        }
    finally:
        connection.close()

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "total_cases": result["scan"]["total_cases"],
                "matched_cases": result["scan"]["matched_cases"],
                "scenarios": len(result["historical_scenarios"]),
                "candidate_services": len(result["candidate_services"]),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
