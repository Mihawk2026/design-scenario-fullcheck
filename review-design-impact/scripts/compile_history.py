#!/usr/bin/env python3
"""Compile extracted local ChangeCase JSON into a reproducible SQLite database."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TERM_FIELDS = {
    "business_objects": "business_object",
    "capabilities": "capability",
    "change_types": "change_type",
    "actions": "action",
    "states": "state",
    "actors": "actor",
    "triggers": "trigger",
    "business_invariants": "invariant",
    "changed_rules": "changed_rule",
}

VALIDATION_TO_TIER = {
    "validated": "trusted",
    "partial": "candidate",
    "unverified": "candidate",
    "conflict": "conflict",
    "rejected": "rejected",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}
EVIDENCE_KINDS = {
    "explicit",
    "inferred",
    "version-diff",
    "review-comment",
    "defect",
    "incident",
}
EVIDENCE_LOCATION_KEYS = {"section", "page", "paragraph", "line", "location"}


def normalized(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def require_list(case: dict[str, Any], field: str) -> list[Any]:
    value = case.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def evidence_errors(evidence: Any, field: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, dict):
        return [f"{field}.evidence must be an object"]
    if evidence.get("kind") not in EVIDENCE_KINDS:
        errors.append(f"{field}.evidence.kind is invalid or missing")
    if not any(evidence.get(key) not in (None, "") for key in EVIDENCE_LOCATION_KEYS):
        errors.append(f"{field}.evidence requires a stable source location")
    return errors


def knowledge_tier(case: dict[str, Any]) -> str:
    validation = case.get("validation", {})
    return VALIDATION_TO_TIER.get(validation.get("status"), "candidate")


def item_tier(case_tier: str, evidence: Any) -> str:
    if case_tier == "trusted" and isinstance(evidence, dict):
        if evidence.get("kind") == "inferred":
            return "candidate"
    return case_tier


def load_case_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".jsonl":
        result: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        ):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"line {line_number} is not an object")
                result.append(value)
        return result

    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise ValueError("JSON array must contain only objects")
        return value
    if isinstance(value, dict):
        return [value]
    raise ValueError("JSON must be an object or an array of objects")


def validate_case(case: dict[str, Any], origin: Path) -> list[str]:
    errors: list[str] = []
    case_id = case.get("case_id")
    source = case.get("source")
    if not isinstance(case_id, str) or not case_id.strip():
        errors.append("case_id must be a non-empty string")
    if not isinstance(case.get("title"), str) or not case.get("title", "").strip():
        errors.append("title must be a non-empty string")
    if not isinstance(source, dict) or not isinstance(source.get("path"), str):
        errors.append("source.path must be a string")
    elif not isinstance(source.get("sha256"), str) or not source.get("sha256", "").strip():
        errors.append("source.sha256 must be a non-empty string")
    for field in TERM_FIELDS:
        try:
            require_list(case, field)
        except ValueError as exc:
            errors.append(str(exc))
    if not require_list(case, "business_objects"):
        errors.append("business_objects must not be empty")
    if not require_list(case, "change_types"):
        errors.append("change_types must not be empty")
    validation = case.get("validation")
    validation_status = None
    if not isinstance(validation, dict):
        errors.append("validation must be an object")
    else:
        validation_status = validation.get("status")
        if validation_status not in VALIDATION_TO_TIER:
            errors.append("validation.status is invalid or missing")
        if validation.get("confidence") not in CONFIDENCE_VALUES:
            errors.append("validation.confidence is invalid or missing")
        if validation_status == "validated" and not validation.get("method"):
            errors.append("validated cases require validation.method")
        if validation_status == "validated" and require_list(case, "uncertain_fields"):
            errors.append("validated cases cannot contain uncertain_fields; use partial")
    scenarios = case.get("scenarios", [])
    service_changes = case.get("service_changes", [])
    if not isinstance(scenarios, list):
        errors.append("scenarios must be a list")
    if not isinstance(service_changes, list):
        errors.append("service_changes must be a list")
    if isinstance(scenarios, list) and isinstance(service_changes, list):
        if validation_status != "rejected" and not scenarios and not service_changes:
            errors.append("at least one scenario or service change is required")
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict) or not scenario.get("name"):
                errors.append(f"scenarios[{index}].name is required")
            elif validation_status != "rejected":
                errors.extend(evidence_errors(scenario.get("evidence"), f"scenarios[{index}]"))
        for index, change in enumerate(service_changes):
            if not isinstance(change, dict) or not change.get("service"):
                errors.append(f"service_changes[{index}].service is required")
            elif validation_status != "rejected":
                errors.extend(evidence_errors(change.get("evidence"), f"service_changes[{index}]"))
    for field in ("relations", "historical_omissions"):
        for index, item in enumerate(require_list(case, field)):
            if isinstance(item, dict) and validation_status != "rejected":
                errors.extend(evidence_errors(item.get("evidence"), f"{field}[{index}]"))
    unresolved_conflicts = 0
    for index, conflict in enumerate(require_list(case, "conflicts")):
        if not isinstance(conflict, dict) or not conflict.get("topic"):
            errors.append(f"conflicts[{index}].topic is required")
            continue
        claims = conflict.get("claims", [])
        if not isinstance(claims, list) or len(claims) < 2:
            errors.append(f"conflicts[{index}].claims requires at least two claims")
        else:
            for claim_index, claim in enumerate(claims):
                if not isinstance(claim, dict) or "value" not in claim:
                    errors.append(f"conflicts[{index}].claims[{claim_index}].value is required")
                else:
                    errors.extend(
                        evidence_errors(
                            claim.get("evidence"),
                            f"conflicts[{index}].claims[{claim_index}]",
                        )
                    )
        if conflict.get("resolution_status") != "resolved":
            unresolved_conflicts += 1
    if unresolved_conflicts and validation_status not in {"conflict", "partial"}:
        errors.append("unresolved conflicts require validation.status conflict or partial")
    return [f"{origin}: {case_id or '<unknown>'}: {error}" for error in errors]


def collect_cases(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    cases: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_ids: dict[str, Path] = {}
    paths = sorted(
        [*root.rglob("*.json"), *root.rglob("*.jsonl")],
        key=lambda item: str(item).casefold(),
    )
    for path in paths:
        try:
            loaded = load_case_file(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        for case in loaded:
            case_errors = validate_case(case, path)
            errors.extend(case_errors)
            case_id = case.get("case_id")
            if isinstance(case_id, str) and case_id:
                if case_id in seen_ids:
                    errors.append(
                        f"{path}: duplicate case_id {case_id}; first seen in {seen_ids[case_id]}"
                    )
                else:
                    seen_ids[case_id] = path
            if not case_errors:
                cases.append(case)
    return cases, errors


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE documents (
    doc_id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    version TEXT,
    title TEXT NOT NULL,
    source_json TEXT NOT NULL
);
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    doc_path TEXT NOT NULL,
    domain TEXT,
    validation_status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    knowledge_tier TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    full_json TEXT NOT NULL
);
CREATE TABLE case_terms (
    case_id TEXT NOT NULL,
    term_type TEXT NOT NULL,
    term TEXT NOT NULL,
    normalized TEXT NOT NULL,
    PRIMARY KEY (case_id, term_type, normalized),
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE INDEX idx_case_terms_lookup ON case_terms(term_type, normalized);
CREATE TABLE scenarios (
    scenario_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    name TEXT NOT NULL,
    precondition TEXT,
    trigger_text TEXT,
    expected_behavior TEXT,
    knowledge_tier TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE TABLE service_changes (
    service_change_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    service TEXT NOT NULL,
    normalized_service TEXT NOT NULL,
    responsibility TEXT,
    asset_types_json TEXT NOT NULL,
    modification TEXT NOT NULL,
    knowledge_tier TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE INDEX idx_service_changes_service ON service_changes(normalized_service);
CREATE TABLE relations (
    relation_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    source TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target TEXT NOT NULL,
    knowledge_tier TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE TABLE historical_omissions (
    omission_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT,
    source TEXT,
    knowledge_tier TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE TABLE service_cochange (
    service_a TEXT NOT NULL,
    service_b TEXT NOT NULL,
    case_count INTEGER NOT NULL,
    case_ids_json TEXT NOT NULL,
    PRIMARY KEY (service_a, service_b)
);
CREATE TABLE conflicts (
    conflict_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    resolution_status TEXT,
    conflict_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
"""


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def insert_cases(connection: sqlite3.Connection, cases: Iterable[dict[str, Any]]) -> None:
    documents: dict[str, tuple[str | None, str, dict[str, Any]]] = {}
    service_cases: dict[tuple[str, str], set[str]] = defaultdict(set)

    for case in cases:
        case_id = case["case_id"].strip()
        source = case["source"]
        doc_path = source["path"]
        case_tier = knowledge_tier(case)
        validation = case["validation"]
        documents.setdefault(
            doc_path, (source.get("version"), case["title"], source)
        )
        connection.execute(
            """INSERT INTO cases(
                case_id, title, doc_path, domain, validation_status, confidence,
                knowledge_tier, validation_json, full_json
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                case_id,
                case["title"],
                doc_path,
                case.get("domain"),
                validation["status"],
                validation["confidence"],
                case_tier,
                json_text(validation),
                json_text(case),
            ),
        )

        if case_tier == "rejected":
            continue

        for field, term_type in TERM_FIELDS.items():
            for term in require_list(case, field):
                if isinstance(term, str) and term.strip():
                    connection.execute(
                        "INSERT OR IGNORE INTO case_terms(case_id, term_type, term, normalized) VALUES(?,?,?,?)",
                        (case_id, term_type, term, normalized(term)),
                    )

        for scenario in require_list(case, "scenarios"):
            if not isinstance(scenario, dict) or not scenario.get("name"):
                continue
            connection.execute(
                """INSERT INTO scenarios(
                    case_id, name, precondition, trigger_text, expected_behavior,
                    knowledge_tier, evidence_json
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    case_id,
                    scenario["name"],
                    scenario.get("precondition"),
                    scenario.get("trigger"),
                    scenario.get("expected_behavior"),
                    item_tier(case_tier, scenario.get("evidence")),
                    json_text(scenario.get("evidence", {})),
                ),
            )

        services_in_case: set[str] = set()
        for change in require_list(case, "service_changes"):
            if not isinstance(change, dict) or not change.get("service"):
                continue
            service = str(change["service"]).strip()
            normalized_service = normalized(service)
            services_in_case.add(normalized_service)
            modifications = change.get("modifications", [])
            if isinstance(modifications, str):
                modifications = [modifications]
            if not modifications:
                modifications = [""]
            for modification in modifications:
                connection.execute(
                    """INSERT INTO service_changes(
                        case_id, service, normalized_service, responsibility,
                        asset_types_json, modification, knowledge_tier, evidence_json
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        case_id,
                        service,
                        normalized_service,
                        change.get("responsibility"),
                        json_text(change.get("asset_types", [])),
                        str(modification),
                        item_tier(case_tier, change.get("evidence")),
                        json_text(change.get("evidence", {})),
                    ),
                )
        if case_tier in {"trusted", "candidate"}:
            for service_a, service_b in itertools.combinations(sorted(services_in_case), 2):
                service_cases[(service_a, service_b)].add(case_id)

        for relation in require_list(case, "relations"):
            if not isinstance(relation, dict):
                continue
            if all(relation.get(key) for key in ("source", "type", "target")):
                connection.execute(
                    """INSERT INTO relations(
                        case_id, source, relation_type, target, knowledge_tier, evidence_json
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        case_id,
                        relation["source"],
                        relation["type"],
                        relation["target"],
                        item_tier(case_tier, relation.get("evidence")),
                        json_text(relation.get("evidence", {})),
                    ),
                )

        for omission in require_list(case, "historical_omissions"):
            if isinstance(omission, str):
                omission = {"description": omission}
            if isinstance(omission, dict) and omission.get("description"):
                connection.execute(
                    """INSERT INTO historical_omissions(
                        case_id, description, severity, source, knowledge_tier, evidence_json
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        case_id,
                        omission["description"],
                        omission.get("severity"),
                        omission.get("source"),
                        item_tier(case_tier, omission.get("evidence")),
                        json_text(omission.get("evidence", {})),
                    ),
                )

        for conflict in require_list(case, "conflicts"):
            if isinstance(conflict, dict) and conflict.get("topic"):
                connection.execute(
                    """INSERT INTO conflicts(
                        case_id, topic, resolution_status, conflict_json
                    ) VALUES(?,?,?,?)""",
                    (
                        case_id,
                        conflict["topic"],
                        conflict.get("resolution_status"),
                        json_text(conflict),
                    ),
                )

    for path, (version, title, source) in documents.items():
        connection.execute(
            "INSERT INTO documents(path, version, title, source_json) VALUES(?,?,?,?)",
            (path, version, title, json_text(source)),
        )

    for (service_a, service_b), case_ids in sorted(service_cases.items()):
        connection.execute(
            "INSERT INTO service_cochange(service_a, service_b, case_count, case_ids_json) VALUES(?,?,?,?)",
            (service_a, service_b, len(case_ids), json_text(sorted(case_ids))),
        )


def build_quality_report(cases: list[dict[str, Any]], database: Path) -> dict[str, Any]:
    statuses = Counter(case["validation"]["status"] for case in cases)
    tiers = Counter(knowledge_tier(case) for case in cases)
    active_cases = [case for case in cases if knowledge_tier(case) != "rejected"]
    service_names = {
        str(change.get("service")).strip()
        for case in active_cases
        for change in require_list(case, "service_changes")
        if isinstance(change, dict) and change.get("service")
    }
    review_queue = [
        {
            "case_id": case["case_id"],
            "title": case["title"],
            "validation_status": case["validation"]["status"],
            "confidence": case["validation"]["confidence"],
            "issues": case["validation"].get("issues", []),
            "conflict_count": len(require_list(case, "conflicts")),
        }
        for case in cases
        if knowledge_tier(case) in {"candidate", "conflict"}
    ]
    return {
        "schema_version": 1,
        "database": str(database),
        "case_count": len(cases),
        "validation_status_counts": dict(sorted(statuses.items())),
        "knowledge_tier_counts": dict(sorted(tiers.items())),
        "scenario_count": sum(
            len(require_list(case, "scenarios")) for case in active_cases
        ),
        "service_count": len(service_names),
        "conflict_count": sum(
            len(require_list(case, "conflicts")) for case in active_cases
        ),
        "human_review_queue": review_queue,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile all local ChangeCase JSON into SQLite."
    )
    parser.add_argument("--cases", required=True, help="Directory containing JSON/JSONL cases")
    parser.add_argument("--output", required=True, help="Output SQLite path")
    args = parser.parse_args()

    case_root = Path(args.cases).resolve()
    if not case_root.is_dir():
        print(f"Case directory not found: {case_root}", file=sys.stderr)
        return 2
    cases, errors = collect_cases(case_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"Compilation stopped with {len(errors)} error(s).", file=sys.stderr)
        return 1
    if not cases:
        print("No valid ChangeCase JSON files found.", file=sys.stderr)
        return 1

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f"{output.stem}-", suffix=".tmp.db", dir=output.parent
    )
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        connection = sqlite3.connect(temp_path)
        try:
            connection.executescript(SCHEMA)
            insert_cases(connection, cases)
            connection.commit()
        finally:
            connection.close()
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    quality_report = build_quality_report(cases, output)
    quality_path = output.with_name(f"{output.stem}.quality.json")
    quality_path.write_text(
        json.dumps(quality_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        json.dumps(
            {
                "cases": len(cases),
                "knowledge_tiers": quality_report["knowledge_tier_counts"],
                "human_review_queue": len(quality_report["human_review_queue"]),
                "output": str(output),
                "quality_report": str(quality_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
