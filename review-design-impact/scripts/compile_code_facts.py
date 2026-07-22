#!/usr/bin/env python3
"""Compile normalized colbymchenry/codegraph MCP observations into a snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE mcp_metadata (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE mcp_calls (
    call_id TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    tool TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    response_path TEXT NOT NULL,
    response_sha256 TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    staleness TEXT NOT NULL
);
CREATE INDEX idx_mcp_calls_repository ON mcp_calls(repository, status, staleness);
CREATE TABLE query_seeds (
    seed_id INTEGER PRIMARY KEY,
    repository TEXT NOT NULL,
    category TEXT NOT NULL,
    seed TEXT NOT NULL,
    status TEXT NOT NULL,
    mcp_call_ids_json TEXT NOT NULL,
    notes TEXT
);
CREATE INDEX idx_query_seeds_repository ON query_seeds(repository, category, status);
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
    source_external INTEGER NOT NULL,
    target_external INTEGER NOT NULL,
    source_repository TEXT,
    target_repository TEXT,
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_list(payload: dict[str, Any], field: str) -> list[Any]:
    value = payload.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return value


def validate_mcp_evidence(
    value: Any, location: str, call_ids: set[str], errors: list[str]
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{location}.evidence must be an object")
        return
    if value.get("kind") != "codegraph-mcp":
        errors.append(f"{location}.evidence.kind must be codegraph-mcp")
    call_id = value.get("mcp_call_id")
    if call_id not in call_ids:
        errors.append(f"{location}.evidence.mcp_call_id is not declared: {call_id}")


def validate(payload: dict[str, Any], capture_root: Path) -> list[str]:
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version != 2:
        errors.append("schema_version must be 2")
    try:
        repositories = require_list(payload, "repositories")
        mcp_calls = require_list(payload, "mcp_calls")
        query_seeds = require_list(payload, "query_seeds")
        entities = require_list(payload, "entities")
        relations = require_list(payload, "relations")
        mappings = require_list(payload, "business_mappings")
    except ValueError as exc:
        return [str(exc)]

    mcp = payload.get("mcp", {})
    if not isinstance(mcp, dict):
        errors.append("mcp must be an object")
    else:
        for field in ("server", "implementation", "transport"):
            if not mcp.get(field):
                errors.append(f"mcp.{field} is required")
        if mcp.get("transport") != "mcp":
            errors.append("mcp.transport must be mcp")
        if not isinstance(mcp.get("tools_observed"), list):
            errors.append("mcp.tools_observed must be a list")

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

    call_ids: set[str] = set()
    call_quality: dict[str, tuple[str | None, str | None]] = {}
    for index, call in enumerate(mcp_calls):
        if not isinstance(call, dict):
            errors.append(f"mcp_calls[{index}] must be an object")
            continue
        for field in (
            "id",
            "repository",
            "tool",
            "response_path",
            "response_sha256",
            "observed_at",
        ):
            if not call.get(field):
                errors.append(f"mcp_calls[{index}].{field} is required")
        call_id = call.get("id")
        if call_id in call_ids:
            errors.append(f"duplicate MCP call id: {call_id}")
        if call_id:
            call_ids.add(call_id)
            call_quality[call_id] = (call.get("status"), call.get("staleness"))
        if call.get("repository") not in repository_names:
            errors.append(
                f"mcp_calls[{index}].repository is not declared: "
                f"{call.get('repository')}"
            )
        if not isinstance(call.get("arguments"), dict):
            errors.append(f"mcp_calls[{index}].arguments must be an object")
        digest = call.get("response_sha256")
        if isinstance(digest, str) and (
            len(digest) != 64
            or any(
                character not in "0123456789abcdefABCDEF" for character in digest
            )
        ):
            errors.append(f"mcp_calls[{index}].response_sha256 must be 64 hex characters")
        response_value = call.get("response_path")
        if isinstance(response_value, str) and response_value:
            response_path = (capture_root / response_value).resolve()
            try:
                response_path.relative_to(capture_root.resolve())
            except ValueError:
                errors.append(
                    f"mcp_calls[{index}].response_path must stay inside the capture directory"
                )
            else:
                if not response_path.is_file():
                    errors.append(
                        f"mcp_calls[{index}].response_path does not exist: {response_value}"
                    )
                elif isinstance(digest, str) and len(digest) == 64:
                    if file_sha256(response_path) != digest.casefold():
                        errors.append(
                            f"mcp_calls[{index}].response_sha256 does not match the raw response"
                        )
        if call.get("status") not in {"ok", "error", "truncated"}:
            errors.append(f"mcp_calls[{index}].status is invalid or missing")
        if call.get("staleness") not in {"fresh", "stale", "unknown"}:
            errors.append(f"mcp_calls[{index}].staleness is invalid or missing")

    if not mcp_calls:
        errors.append("mcp_calls must contain at least one observed MCP call")

    for index, query_seed in enumerate(query_seeds):
        if not isinstance(query_seed, dict):
            errors.append(f"query_seeds[{index}] must be an object")
            continue
        for field in ("repository", "category", "seed"):
            if not query_seed.get(field):
                errors.append(f"query_seeds[{index}].{field} is required")
        if query_seed.get("repository") not in repository_names:
            errors.append(
                f"query_seeds[{index}].repository is not declared: "
                f"{query_seed.get('repository')}"
            )
        if query_seed.get("status") not in {
            "matched",
            "not-found",
            "ambiguous",
            "truncated",
            "not-queried",
        }:
            errors.append(f"query_seeds[{index}].status is invalid or missing")
        seed_call_ids = query_seed.get("mcp_call_ids")
        if not isinstance(seed_call_ids, list):
            errors.append(f"query_seeds[{index}].mcp_call_ids must be a list")
        else:
            for call_id in seed_call_ids:
                if call_id not in call_ids:
                    errors.append(
                        f"query_seeds[{index}].mcp_call_ids contains undeclared id: "
                        f"{call_id}"
                    )
            if query_seed.get("status") != "not-queried" and not seed_call_ids:
                errors.append(
                    f"query_seeds[{index}].mcp_call_ids is required for queried status"
                )

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
        validate_mcp_evidence(
            entity.get("evidence"), f"entities[{index}]", call_ids, errors
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
        validate_mcp_evidence(
            relation.get("evidence"), f"relations[{index}]", call_ids, errors
        )
        source_id = relation.get("source_id")
        target_id = relation.get("target_id")
        source_external = relation.get("source_external") is True
        target_external = relation.get("target_external") is True
        if source_id not in entity_ids and not source_external:
            errors.append(
                f"relations[{index}].source_id is undeclared; mark source_external=true"
            )
        if target_id not in entity_ids and not target_external:
            errors.append(
                f"relations[{index}].target_id is undeclared; mark target_external=true"
            )
        if source_external and not relation.get("source_repository"):
            errors.append(f"relations[{index}].source_repository is required")
        if target_external and not relation.get("target_repository"):
            errors.append(f"relations[{index}].target_repository is required")

    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            errors.append(f"business_mappings[{index}] must be an object")
            continue
        if not mapping.get("asset_id"):
            errors.append(f"business_mappings[{index}].asset_id is required")
        elif mapping.get("asset_id") not in entity_ids:
            errors.append(
                f"business_mappings[{index}].asset_id is not a declared entity"
            )
        if not any(mapping.get(field) for field in ("business_object", "state", "action")):
            errors.append(
                f"business_mappings[{index}] requires business_object, state, or action"
            )
        if mapping.get("confidence") not in {"high", "medium", "low"}:
            errors.append(f"business_mappings[{index}].confidence is invalid or missing")
        validate_mcp_evidence(
            mapping.get("evidence"),
            f"business_mappings[{index}]",
            call_ids,
            errors,
        )
        evidence = mapping.get("evidence", {})
        call_id = evidence.get("mcp_call_id") if isinstance(evidence, dict) else None
        if (
            mapping.get("confidence") == "high"
            and call_quality.get(call_id) != ("ok", "fresh")
        ):
            errors.append(
                f"business_mappings[{index}] cannot be high confidence without "
                "a fresh successful MCP call"
            )
    return errors


def insert_payload(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
    mcp = payload["mcp"]
    for key, value in mcp.items():
        connection.execute(
            "INSERT INTO mcp_metadata(key, value_json) VALUES(?,?)",
            (key, json_text(value)),
        )
    for call in payload.get("mcp_calls", []):
        connection.execute(
            """INSERT INTO mcp_calls(
                call_id, repository, tool, arguments_json, response_path,
                response_sha256, observed_at, status, staleness
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                call["id"],
                call["repository"],
                call["tool"],
                json_text(call["arguments"]),
                call["response_path"],
                call["response_sha256"],
                call["observed_at"],
                call["status"],
                call["staleness"],
            ),
        )
    for query_seed in payload.get("query_seeds", []):
        connection.execute(
            """INSERT INTO query_seeds(
                repository, category, seed, status, mcp_call_ids_json, notes
            ) VALUES(?,?,?,?,?,?)""",
            (
                query_seed["repository"],
                query_seed["category"],
                query_seed["seed"],
                query_seed["status"],
                json_text(query_seed.get("mcp_call_ids", [])),
                query_seed.get("notes"),
            ),
        )
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
                source_id, relation_type, target_id, repository, source_external,
                target_external, source_repository, target_repository, evidence_json
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                relation["source_id"],
                relation["type"],
                relation["target_id"],
                relation.get("repository"),
                int(relation.get("source_external") is True),
                int(relation.get("target_external") is True),
                relation.get("source_repository"),
                relation.get("target_repository"),
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
        description="Compile normalized CodeGraph MCP observations into a snapshot."
    )
    parser.add_argument("--input", required=True, help="Normalized CodeGraph MCP capture JSON")
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
        print("CodeGraph MCP capture must be a JSON object.", file=sys.stderr)
        return 2
    errors = validate(payload, input_path.parent)
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

    mcp_calls = payload.get("mcp_calls", [])
    snapshot_status = (
        "stale"
        if any(
            call.get("status") != "ok" or call.get("staleness") == "stale"
            for call in mcp_calls
        )
        else "unknown"
        if any(call.get("staleness") == "unknown" for call in mcp_calls)
        else "fresh"
    )
    manifest = {
        "schema_version": 2,
        "generated_at": payload.get("generated_at"),
        "status": snapshot_status,
        "source": {
            "kind": "codegraph-mcp",
            **payload["mcp"],
        },
        "mcp_call_count": len(mcp_calls),
        "query_seed_count": len(payload.get("query_seeds", [])),
        "repositories": payload["repositories"],
        "code_facts_db": str(output),
    }
    coverage = {
        "schema_version": 2,
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
        "mcp_calls": [
            {
                "id": call["id"],
                "repository": call["repository"],
                "tool": call["tool"],
                "status": call["status"],
                "staleness": call["staleness"],
            }
            for call in mcp_calls
        ],
        "query_seeds": payload.get("query_seeds", []),
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
                "mcp_calls": len(payload.get("mcp_calls", [])),
                "query_seeds": len(payload.get("query_seeds", [])),
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
