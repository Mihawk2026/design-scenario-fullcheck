#!/usr/bin/env python3
"""Compile a CodeGraph business-impact export into an offline code fact snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE repositories (
    name TEXT PRIMARY KEY,
    path TEXT,
    branch TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    not_covered_json TEXT NOT NULL
);
CREATE TABLE entities (
    entity_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    service TEXT,
    repository TEXT NOT NULL,
    location_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE INDEX idx_entities_service ON entities(service);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE TABLE relations (
    relation_id INTEGER PRIMARY KEY,
    source_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    repository TEXT,
    evidence_json TEXT NOT NULL
);
CREATE INDEX idx_relations_source ON relations(source_id, relation_type);
CREATE INDEX idx_relations_target ON relations(target_id, relation_type);
CREATE TABLE business_mappings (
    mapping_id INTEGER PRIMARY KEY,
    business_object TEXT,
    state TEXT,
    action TEXT,
    asset_id TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE INDEX idx_business_mappings_object ON business_mappings(business_object);
CREATE INDEX idx_business_mappings_state ON business_mappings(state);
CREATE INDEX idx_business_mappings_action ON business_mappings(action);
"""


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def require_list(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def validate(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        repositories = require_list(payload, "repositories")
        entities = require_list(payload, "entities")
        relations = require_list(payload, "relations")
        mappings = require_list(payload, "business_mappings")
    except ValueError as exc:
        return [str(exc)]

    repository_names: set[str] = set()
    for index, repository in enumerate(repositories):
        if not isinstance(repository, dict):
            errors.append(f"repositories[{index}] must be an object")
            continue
        for field in ("name", "branch", "commit", "indexed_at"):
            if not repository.get(field):
                errors.append(f"repositories[{index}].{field} is required")
        if repository.get("name") in repository_names:
            errors.append(f"duplicate repository name: {repository.get('name')}")
        if repository.get("name"):
            repository_names.add(repository["name"])
        for field in ("coverage", "not_covered"):
            if not isinstance(repository.get(field, []), list):
                errors.append(f"repositories[{index}].{field} must be a list")

    entity_ids: set[str] = set()
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            errors.append(f"entities[{index}] must be an object")
            continue
        for field in ("id", "type", "name", "repository"):
            if not entity.get(field):
                errors.append(f"entities[{index}].{field} is required")
        if entity.get("id") in entity_ids:
            errors.append(f"duplicate entity id: {entity.get('id')}")
        if entity.get("id"):
            entity_ids.add(entity["id"])
        if entity.get("repository") not in repository_names:
            errors.append(
                f"entities[{index}].repository is not declared: {entity.get('repository')}"
            )

    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            errors.append(f"relations[{index}] must be an object")
            continue
        for field in ("source_id", "type", "target_id"):
            if not relation.get(field):
                errors.append(f"relations[{index}].{field} is required")
        repository = relation.get("repository")
        if repository and repository not in repository_names:
            errors.append(f"relations[{index}].repository is not declared: {repository}")

    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            errors.append(f"business_mappings[{index}] must be an object")
            continue
        if not mapping.get("asset_id"):
            errors.append(f"business_mappings[{index}].asset_id is required")
        if not any(mapping.get(field) for field in ("business_object", "state", "action")):
            errors.append(
                f"business_mappings[{index}] requires business_object, state, or action"
            )
        if mapping.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"business_mappings[{index}].confidence is invalid or missing")
    return errors


def insert_payload(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
    for repository in payload["repositories"]:
        connection.execute(
            """INSERT INTO repositories(
                name, path, branch, commit_sha, indexed_at, coverage_json, not_covered_json
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                repository["name"],
                repository.get("path"),
                repository["branch"],
                repository["commit"],
                repository["indexed_at"],
                json_text(repository.get("coverage", [])),
                json_text(repository.get("not_covered", [])),
            ),
        )
    for entity in payload["entities"]:
        connection.execute(
            """INSERT INTO entities(
                entity_id, entity_type, name, service, repository, location_json, evidence_json
            ) VALUES(?,?,?,?,?,?,?)""",
            (
                entity["id"],
                entity["type"],
                entity["name"],
                entity.get("service"),
                entity["repository"],
                json_text(entity.get("location", {})),
                json_text(entity.get("evidence", {})),
            ),
        )
    for relation in payload["relations"]:
        connection.execute(
            """INSERT INTO relations(
                source_id, relation_type, target_id, repository, evidence_json
            ) VALUES(?,?,?,?,?)""",
            (
                relation["source_id"],
                relation["type"],
                relation["target_id"],
                relation.get("repository"),
                json_text(relation.get("evidence", {})),
            ),
        )
    for mapping in payload["business_mappings"]:
        connection.execute(
            """INSERT INTO business_mappings(
                business_object, state, action, asset_id, confidence, evidence_json
            ) VALUES(?,?,?,?,?,?)""",
            (
                mapping.get("business_object"),
                mapping.get("state"),
                mapping.get("action"),
                mapping["asset_id"],
                mapping["confidence"],
                json_text(mapping.get("evidence", {})),
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile a CodeGraph export into an offline code fact snapshot."
    )
    parser.add_argument("--input", required=True, help="CodeGraph export JSON")
    parser.add_argument("--output", required=True, help="Output code-facts.db")
    parser.add_argument("--manifest", help="Output code-manifest.json")
    parser.add_argument("--coverage", help="Output code-coverage.json")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output = Path(args.output).resolve()
    manifest_path = (
        Path(args.manifest).resolve()
        if args.manifest
        else output.with_name("code-manifest.json")
    )
    coverage_path = (
        Path(args.coverage).resolve()
        if args.coverage
        else output.with_name("code-coverage.json")
    )
    try:
        payload = json.loads(input_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print("CodeGraph export must be a JSON object.", file=sys.stderr)
        return 2
    errors = validate(payload)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

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
            insert_payload(connection, payload)
            connection.commit()
        finally:
            connection.close()
        os.replace(temp_path, output)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    manifest = {
        "schema_version": 1,
        "generated_at": payload.get("generated_at"),
        "status": "fresh",
        "repositories": payload["repositories"],
        "code_facts_db": str(output),
    }
    coverage = {
        "schema_version": 1,
        "generated_at": payload.get("generated_at"),
        "repositories": [
            {
                "name": repository["name"],
                "branch": repository["branch"],
                "commit": repository["commit"],
                "coverage": repository.get("coverage", []),
                "not_covered": repository.get("not_covered", []),
            }
            for repository in payload["repositories"]
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    coverage_path.write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "repositories": len(payload["repositories"]),
                "entities": len(payload["entities"]),
                "relations": len(payload["relations"]),
                "business_mappings": len(payload["business_mappings"]),
                "output": str(output),
                "manifest": str(manifest_path),
                "coverage": str(coverage_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
