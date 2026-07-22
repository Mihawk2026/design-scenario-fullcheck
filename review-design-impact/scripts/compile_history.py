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
from collections import defaultdict
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


def normalized(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def require_list(case: dict[str, Any], field: str) -> list[Any]:
    value = case.get(field, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


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
    scenarios = case.get("scenarios", [])
    service_changes = case.get("service_changes", [])
    if not isinstance(scenarios, list):
        errors.append("scenarios must be a list")
    if not isinstance(service_changes, list):
        errors.append("service_changes must be a list")
    if isinstance(scenarios, list) and isinstance(service_changes, list):
        if not scenarios and not service_changes:
            errors.append("at least one scenario or service change is required")
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
    evidence_json TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE
);
CREATE TABLE historical_omissions (
    omission_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT,
    source TEXT,
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
        documents.setdefault(
            doc_path, (source.get("version"), case["title"], source)
        )
        connection.execute(
            "INSERT INTO cases(case_id, title, doc_path, domain, full_json) VALUES(?,?,?,?,?)",
            (
                case_id,
                case["title"],
                doc_path,
                case.get("domain"),
                json_text(case),
            ),
        )

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
                    case_id, name, precondition, trigger_text, expected_behavior, evidence_json
                ) VALUES(?,?,?,?,?,?)""",
                (
                    case_id,
                    scenario["name"],
                    scenario.get("precondition"),
                    scenario.get("trigger"),
                    scenario.get("expected_behavior"),
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
                        asset_types_json, modification, evidence_json
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        case_id,
                        service,
                        normalized_service,
                        change.get("responsibility"),
                        json_text(change.get("asset_types", [])),
                        str(modification),
                        json_text(change.get("evidence", {})),
                    ),
                )
        for service_a, service_b in itertools.combinations(sorted(services_in_case), 2):
            service_cases[(service_a, service_b)].add(case_id)

        for relation in require_list(case, "relations"):
            if not isinstance(relation, dict):
                continue
            if all(relation.get(key) for key in ("source", "type", "target")):
                connection.execute(
                    "INSERT INTO relations(case_id, source, relation_type, target, evidence_json) VALUES(?,?,?,?,?)",
                    (
                        case_id,
                        relation["source"],
                        relation["type"],
                        relation["target"],
                        json_text(relation.get("evidence", {})),
                    ),
                )

        for omission in require_list(case, "historical_omissions"):
            if isinstance(omission, str):
                omission = {"description": omission}
            if isinstance(omission, dict) and omission.get("description"):
                connection.execute(
                    """INSERT INTO historical_omissions(
                        case_id, description, severity, source, evidence_json
                    ) VALUES(?,?,?,?,?)""",
                    (
                        case_id,
                        omission["description"],
                        omission.get("severity"),
                        omission.get("source"),
                        json_text(omission.get("evidence", {})),
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

    print(
        json.dumps(
            {"cases": len(cases), "output": str(output)}, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
