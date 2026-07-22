#!/usr/bin/env python3
"""Analyze design impact across history and an offline CodeGraph fact snapshot."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
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
    "before_behaviors": "before_behaviors",
    "after_behaviors": "after_behaviors",
}

FIELD_WEIGHTS = {
    "business_objects": 2.0,
    "capabilities": 1.8,
    "actions": 1.5,
    "states": 1.5,
    "invariants": 1.3,
    "changed_rules": 1.3,
    "change_types": 1.0,
    "before_behaviors": 1.0,
    "after_behaviors": 1.0,
    "actors": 0.7,
    "triggers": 0.7,
}


def normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return " ".join(normalized.split())


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
    for alias, canonical_name in raw.items():
        if not isinstance(alias, str) or not isinstance(canonical_name, str):
            raise ValueError("Alias keys and values must be strings")
        aliases[normalize(alias)] = normalize(canonical_name)
    return aliases


def canonical(value: str, aliases: dict[str, str]) -> str:
    current = normalize(value)
    visited: set[str] = set()
    while current in aliases and current not in visited:
        visited.add(current)
        current = aliases[current]
    return current


def terms(value: Any, aliases: dict[str, str]) -> list[tuple[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            key = canonical(item, aliases)
            if key not in seen:
                result.append((key, item.strip()))
                seen.add(key)
    return result


def lexical_tokens(value: str) -> set[str]:
    result: set[str] = set()
    for part in re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", normalize(value)):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            result.add(part)
            if len(part) > 1:
                result.update(part[index : index + 2] for index in range(len(part) - 1))
        else:
            result.add(part)
    return result


def similarity(left: str, right: str) -> tuple[float, str]:
    if not left or not right:
        return 0.0, "none"
    if left == right:
        return 1.0, "exact"
    if min(len(left), len(right)) >= 2 and (left in right or right in left):
        return 0.9, "contains"
    left_tokens = lexical_tokens(left)
    right_tokens = lexical_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0, "none"
    overlap = len(left_tokens & right_tokens)
    if not overlap:
        return 0.0, "none"
    containment = overlap / min(len(left_tokens), len(right_tokens))
    jaccard = overlap / len(left_tokens | right_tokens)
    return max(containment * 0.75, jaccard), "lexical"


def best_term_matches(
    wanted: list[tuple[str, str]], actual: list[tuple[str, str]], threshold: float = 0.5
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for wanted_key, wanted_display in wanted:
        best: tuple[float, str, str] = (0.0, "none", "")
        for actual_key, actual_display in actual:
            score, match_type = similarity(wanted_key, actual_key)
            if score > best[0]:
                best = (score, match_type, actual_display)
        if best[0] >= threshold:
            matches.append(
                {
                    "wanted": wanted_display,
                    "actual": best[2],
                    "score": round(best[0], 4),
                    "match_type": best[1],
                }
            )
    return matches


def score_case(
    case: dict[str, Any],
    spec_terms: dict[str, list[tuple[str, str]]],
    aliases: dict[str, str],
) -> tuple[float, list[dict[str, Any]]]:
    total = 0.0
    reasons: list[dict[str, Any]] = []
    for spec_field, case_field in SPEC_TO_CASE_FIELDS.items():
        matches = best_term_matches(
            spec_terms.get(spec_field, []), terms(case.get(case_field, []), aliases)
        )
        if not matches:
            continue
        contribution = FIELD_WEIGHTS.get(spec_field, 1.0) * max(
            match["score"] for match in matches
        )
        total += contribution
        reasons.append(
            {
                "field": spec_field,
                "score": round(contribution, 4),
                "matches": matches,
            }
        )
    return round(total, 4), reasons


def max_case_field_similarity(
    case_ids: list[str],
    row_by_id: dict[str, sqlite3.Row],
    spec_terms: dict[str, list[tuple[str, str]]],
    field: str,
    aliases: dict[str, str],
) -> float:
    result = 0.0
    for case_id in case_ids:
        if case_id not in row_by_id:
            continue
        case = json.loads(row_by_id[case_id]["full_json"])
        matches = best_term_matches(
            spec_terms.get(field, []), terms(case.get(field, []), aliases)
        )
        result = max(result, max((match["score"] for match in matches), default=0.0))
    return result


def aggregate_tier(tiers: list[str]) -> str:
    values = set(tiers)
    if "conflict" in values:
        return "conflict"
    if "trusted" in values:
        return "trusted"
    if "candidate" in values:
        return "candidate"
    return "rejected"


def evidence_case_id(case_id: str) -> dict[str, str]:
    return {"type": "historical", "case_id": case_id}


def propagate_history_relations(
    connection: sqlite3.Connection,
    seed_terms: set[str],
    direct_case_ids: set[str],
    max_depth: int,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    rows = connection.execute(
        """SELECT stable_id, case_id, source_id, source, relation_type,
                  target_id, target, direction, propagation, conditions_json,
                  version_scope_json, knowledge_tier, evidence_json
           FROM relations WHERE knowledge_tier != 'rejected' ORDER BY relation_id"""
    ).fetchall()
    frontier = set(seed_terms)
    seen = set(seed_terms)
    case_hits: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paths: list[dict[str, Any]] = []
    for depth in range(1, max_depth + 1):
        next_frontier: set[str] = set()
        for row in rows:
            source = normalize(row["source"])
            target = normalize(row["target"])
            propagation = row["propagation"]
            best_source = (
                max((similarity(seed, source)[0] for seed in frontier), default=0.0)
                if propagation in {"forward", "bidirectional"}
                else 0.0
            )
            best_target = (
                max((similarity(seed, target)[0] for seed in frontier), default=0.0)
                if propagation in {"reverse", "bidirectional"}
                else 0.0
            )
            if max(best_source, best_target) < 0.7:
                continue
            matched_endpoint = source if best_source >= best_target else target
            reached_endpoint = target if matched_endpoint == source else source
            hit = {
                "relation_id": row["stable_id"],
                "depth": depth,
                "source_id": row["source_id"],
                "source": row["source"],
                "relation_type": row["relation_type"],
                "target_id": row["target_id"],
                "target": row["target"],
                "direction": row["direction"],
                "propagation": propagation,
                "conditions": json.loads(row["conditions_json"]),
                "version_scope": json.loads(row["version_scope_json"]),
                "knowledge_tier": row["knowledge_tier"],
                "evidence": json.loads(row["evidence_json"]),
            }
            case_hits[row["case_id"]].append(hit)
            paths.append({"case_id": row["case_id"], **hit})
            if reached_endpoint not in seen:
                seen.add(reached_endpoint)
                next_frontier.add(reached_endpoint)
        frontier = next_frontier
        if not frontier:
            break
    return (
        {case_id: hits for case_id, hits in case_hits.items() if case_id not in direct_case_ids},
        paths,
    )


def load_code_impacts(
    code_db: Path | None,
    spec_terms: dict[str, list[tuple[str, str]]],
    aliases: dict[str, str],
    max_depth: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, set[str]]]:
    if code_db is None or not code_db.is_file():
        return [], [], {}
    connection = sqlite3.connect(code_db)
    connection.row_factory = sqlite3.Row
    try:
        entity_rows = connection.execute(
            """SELECT entity_id, entity_type, name, service, repository,
                      location_json, evidence_json FROM entities"""
        ).fetchall()
        entities = {row["entity_id"]: row for row in entity_rows}
        mapping_rows = connection.execute(
            """SELECT mapping_id, business_object, state, action, asset_id,
                      confidence, evidence_json FROM business_mappings"""
        ).fetchall()
        impacts: dict[str, dict[str, Any]] = {}
        frontier: set[str] = set()
        for row in mapping_rows:
            field_values = {
                "business_objects": row["business_object"],
                "states": row["state"],
                "actions": row["action"],
            }
            matches: list[dict[str, Any]] = []
            score = 0.0
            for field, value in field_values.items():
                if not value:
                    continue
                actual = [(canonical(value, aliases), value)]
                field_matches = best_term_matches(spec_terms.get(field, []), actual, 0.5)
                if field_matches:
                    contribution = FIELD_WEIGHTS[field] * max(
                        match["score"] for match in field_matches
                    )
                    score += contribution
                    matches.append({"field": field, "matches": field_matches})
            if score < 0.8 or row["asset_id"] not in entities:
                continue
            entity = entities[row["asset_id"]]
            impacts[row["asset_id"]] = {
                "entity_id": row["asset_id"],
                "entity_type": entity["entity_type"],
                "name": entity["name"],
                "service": entity["service"],
                "repository": entity["repository"],
                "location": json.loads(entity["location_json"]),
                "match_score": round(score, 4),
                "match_reasons": matches,
                "confidence": row["confidence"],
                "evidence": [json.loads(row["evidence_json"])],
                "source": "offline-code-mapping",
            }
            frontier.add(row["asset_id"])

        relation_rows = connection.execute(
            """SELECT source_id, relation_type, target_id, evidence_json
               FROM relations ORDER BY relation_id"""
        ).fetchall()
        adjacency: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)
        for row in relation_rows:
            evidence = json.loads(row["evidence_json"])
            adjacency[row["source_id"]].append((row["target_id"], row["relation_type"], evidence))
            adjacency[row["target_id"]].append((row["source_id"], row["relation_type"], evidence))
        paths: list[dict[str, Any]] = []
        visited = set(frontier)
        for depth in range(1, max_depth + 1):
            next_frontier: set[str] = set()
            for source_id in sorted(frontier):
                for target_id, relation_type, evidence in adjacency.get(source_id, []):
                    paths.append(
                        {
                            "depth": depth,
                            "source_id": source_id,
                            "relation_type": relation_type,
                            "target_id": target_id,
                            "evidence": evidence,
                        }
                    )
                    if target_id in visited or target_id not in entities:
                        continue
                    visited.add(target_id)
                    next_frontier.add(target_id)
                    entity = entities[target_id]
                    impacts[target_id] = {
                        "entity_id": target_id,
                        "entity_type": entity["entity_type"],
                        "name": entity["name"],
                        "service": entity["service"],
                        "repository": entity["repository"],
                        "location": json.loads(entity["location_json"]),
                        "match_score": round(0.7**depth, 4),
                        "match_reasons": [{"field": "code-relation", "depth": depth}],
                        "confidence": "medium",
                        "evidence": [evidence],
                        "source": "offline-code-propagation",
                    }
            frontier = next_frontier
            if not frontier:
                break

        services: dict[str, set[str]] = defaultdict(set)
        for impact in impacts.values():
            if impact.get("service"):
                services[normalize(impact["service"])].add(impact["entity_id"])
        return (
            sorted(impacts.values(), key=lambda item: item["entity_id"].casefold()),
            paths,
            services,
        )
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze all historical cases and an offline CodeGraph snapshot."
    )
    parser.add_argument("--db", required=True, help="Compiled history SQLite")
    parser.add_argument("--code-db", help="Optional offline code-facts.db")
    parser.add_argument("--change-spec", required=True, help="ChangeSpec JSON")
    parser.add_argument("--aliases", help="Optional alias-to-canonical JSON")
    parser.add_argument("--output", required=True, help="Impact JSON output")
    parser.add_argument("--min-match-score", type=float, default=1.0)
    parser.add_argument("--graph-depth", type=int, default=2)
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    code_db_path = Path(args.code_db).resolve() if args.code_db else None
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

    spec_terms = {
        field: terms(spec.get(field, []), aliases) for field in SPEC_TO_CASE_FIELDS
    }
    seed_terms = {
        key for values in spec_terms.values() for key, _display in values if key
    }

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT case_id, title, doc_path, domain, validation_status,
                      confidence, knowledge_tier, full_json
               FROM cases ORDER BY case_id"""
        ).fetchall()
        row_by_id = {row["case_id"]: row for row in rows}
        case_scores: dict[str, float] = {}
        case_reasons: dict[str, list[dict[str, Any]]] = {}
        direct_case_ids: set[str] = set()
        for row in rows:
            if row["knowledge_tier"] == "rejected":
                continue
            case = json.loads(row["full_json"])
            score, reasons = score_case(case, spec_terms, aliases)
            case_scores[row["case_id"]] = score
            case_reasons[row["case_id"]] = reasons
            if score >= args.min_match_score:
                direct_case_ids.add(row["case_id"])

        relation_hits, history_paths = propagate_history_relations(
            connection, seed_terms, direct_case_ids, max(0, args.graph_depth)
        )
        propagated_case_ids = set(relation_hits) - direct_case_ids
        matched_case_ids = direct_case_ids | propagated_case_ids
        matched_cases: list[dict[str, Any]] = []
        for case_id in sorted(matched_case_ids):
            row = row_by_id[case_id]
            propagated = case_id in propagated_case_ids
            matched_cases.append(
                {
                    "case_id": case_id,
                    "title": row["title"],
                    "source": row["doc_path"],
                    "domain": row["domain"],
                    "validation_status": row["validation_status"],
                    "confidence": row["confidence"],
                    "knowledge_tier": "candidate" if propagated else row["knowledge_tier"],
                    "match_source": "relation-propagated" if propagated else "direct",
                    "match_score": case_scores.get(case_id, 0.0),
                    "match_reasons": (
                        [{"field": "historical-relation", "paths": relation_hits[case_id]}]
                        if propagated
                        else case_reasons.get(case_id, [])
                    ),
                }
            )

        scenarios: dict[str, dict[str, Any]] = {}
        services: dict[str, dict[str, Any]] = {}
        omissions: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        if matched_case_ids:
            placeholders = ",".join("?" for _ in matched_case_ids)
            case_args = sorted(matched_case_ids)
            scenario_rows = connection.execute(
                f"""SELECT case_id, name, precondition, trigger_text,
                            expected_behavior, knowledge_tier, evidence_json
                     FROM scenarios WHERE case_id IN ({placeholders})
                     ORDER BY name, case_id""",
                case_args,
            ).fetchall()
            for row in scenario_rows:
                key = canonical(row["name"], aliases)
                effective_tier = (
                    "candidate" if row["case_id"] in propagated_case_ids else row["knowledge_tier"]
                )
                item = scenarios.setdefault(
                    key,
                    {
                        "scenario": row["name"],
                        "preconditions": [],
                        "triggers": [],
                        "expected_behaviors": [],
                        "case_ids": [],
                        "knowledge_tiers": [],
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
                item["knowledge_tiers"].append(effective_tier)
                item["evidence"].append(
                    {
                        **evidence_case_id(row["case_id"]),
                        "knowledge_tier": effective_tier,
                        "detail": json.loads(row["evidence_json"]),
                    }
                )

            service_rows = connection.execute(
                f"""SELECT case_id, service, responsibility, asset_types_json,
                            modification, knowledge_tier, evidence_json
                     FROM service_changes WHERE case_id IN ({placeholders})
                     ORDER BY normalized_service, case_id, service_change_id""",
                case_args,
            ).fetchall()
            for row in service_rows:
                key = canonical(row["service"], aliases)
                effective_tier = (
                    "candidate" if row["case_id"] in propagated_case_ids else row["knowledge_tier"]
                )
                item = services.setdefault(
                    key,
                    {
                        "service": row["service"],
                        "responsibilities": [],
                        "asset_types": [],
                        "modifications": [],
                        "case_ids": [],
                        "knowledge_tiers": [],
                        "evidence": [],
                        "source": "matched-history",
                    },
                )
                if row["responsibility"] and row["responsibility"] not in item["responsibilities"]:
                    item["responsibilities"].append(row["responsibility"])
                for asset_type in json.loads(row["asset_types_json"]):
                    if asset_type not in item["asset_types"]:
                        item["asset_types"].append(asset_type)
                if row["modification"] and row["modification"] not in item["modifications"]:
                    item["modifications"].append(row["modification"])
                if row["case_id"] not in item["case_ids"]:
                    item["case_ids"].append(row["case_id"])
                item["knowledge_tiers"].append(effective_tier)
                item["evidence"].append(
                    {
                        **evidence_case_id(row["case_id"]),
                        "knowledge_tier": effective_tier,
                        "detail": json.loads(row["evidence_json"]),
                    }
                )

            omission_rows = connection.execute(
                f"""SELECT case_id, description, severity, source,
                            knowledge_tier, evidence_json
                     FROM historical_omissions WHERE case_id IN ({placeholders})""",
                case_args,
            ).fetchall()
            for row in omission_rows:
                omissions.append(
                    {
                        "case_id": row["case_id"],
                        "description": row["description"],
                        "severity": row["severity"],
                        "source": row["source"],
                        "knowledge_tier": (
                            "candidate"
                            if row["case_id"] in propagated_case_ids
                            else row["knowledge_tier"]
                        ),
                        "evidence": json.loads(row["evidence_json"]),
                    }
                )
            conflict_rows = connection.execute(
                f"""SELECT case_id, topic, resolution_status, conflict_json
                     FROM conflicts WHERE case_id IN ({placeholders})""",
                case_args,
            ).fetchall()
            conflicts = [
                {
                    "case_id": row["case_id"],
                    "topic": row["topic"],
                    "resolution_status": row["resolution_status"],
                    "detail": json.loads(row["conflict_json"]),
                }
                for row in conflict_rows
            ]

        for item in scenarios.values():
            item["knowledge_tier"] = aggregate_tier(item["knowledge_tiers"])
            item["requires_confirmation"] = item["knowledge_tier"] != "trusted"
        for item in services.values():
            item["knowledge_tier"] = aggregate_tier(item["knowledge_tiers"])
            item["requires_confirmation"] = item["knowledge_tier"] != "trusted"

        code_impacts, code_paths, code_services = load_code_impacts(
            code_db_path, spec_terms, aliases, max(0, args.graph_depth)
        )
        for service_key, entity_ids in code_services.items():
            if service_key in services:
                services[service_key]["evidence"].append(
                    {"type": "offline-code", "entity_ids": sorted(entity_ids)}
                )
                continue
            display = next(
                impact["service"]
                for impact in code_impacts
                if impact.get("service") and normalize(impact["service"]) == service_key
            )
            services[service_key] = {
                "service": display,
                "responsibilities": [],
                "asset_types": sorted(
                    {
                        impact["entity_type"]
                        for impact in code_impacts
                        if impact.get("service") and normalize(impact["service"]) == service_key
                    }
                ),
                "modifications": [],
                "case_ids": [],
                "knowledge_tiers": ["candidate"],
                "knowledge_tier": "candidate",
                "requires_confirmation": True,
                "evidence": [{"type": "offline-code", "entity_ids": sorted(entity_ids)}],
                "source": "offline-code-candidate",
            }

        direct_services = {
            key
            for key, item in services.items()
            if item["source"] == "matched-history"
            and any(case_id in direct_case_ids for case_id in item["case_ids"])
        }
        tier_by_case = {row["case_id"]: row["knowledge_tier"] for row in rows}
        cochanges: list[dict[str, Any]] = []
        for row in connection.execute(
            "SELECT service_a, service_b, case_count, case_ids_json FROM service_cochange"
        ).fetchall():
            left = canonical(row["service_a"], aliases)
            right = canonical(row["service_b"], aliases)
            if left not in direct_services and right not in direct_services:
                continue
            case_ids = json.loads(row["case_ids_json"])
            context = min(max((case_scores.get(case_id, 0.0) for case_id in case_ids), default=0.0) / 3, 1.0)
            frequency = min(row["case_count"] / 3, 1.0)
            trusted = sum(tier_by_case.get(case_id) == "trusted" for case_id in case_ids) / max(
                len(case_ids), 1
            )
            business_similarity = max_case_field_similarity(
                case_ids, row_by_id, spec_terms, "business_objects", aliases
            )
            change_similarity = max_case_field_similarity(
                case_ids, row_by_id, spec_terms, "change_types", aliases
            )
            score = round(
                context * 0.35
                + business_similarity * 0.15
                + change_similarity * 0.15
                + frequency * 0.2
                + trusted * 0.15,
                4,
            )
            expanded = (
                row["case_count"] >= 2
                and score >= 0.6
                and max(business_similarity, change_similarity) >= 0.5
            )
            cochange = {
                "service_a": row["service_a"],
                "service_b": row["service_b"],
                "case_count": row["case_count"],
                "case_ids": case_ids,
                "score": score,
                "expanded": expanded,
                "signals": {
                    "context_similarity": round(context, 4),
                    "business_object_similarity": round(business_similarity, 4),
                    "change_type_similarity": round(change_similarity, 4),
                    "frequency": round(frequency, 4),
                    "trusted_ratio": round(trusted, 4),
                },
            }
            cochanges.append(cochange)
            if not expanded:
                continue
            for key, display in ((left, row["service_a"]), (right, row["service_b"])):
                if key not in services:
                    services[key] = {
                        "service": display,
                        "responsibilities": [],
                        "asset_types": [],
                        "modifications": [],
                        "case_ids": case_ids,
                        "knowledge_tiers": ["candidate"],
                        "knowledge_tier": "candidate",
                        "requires_confirmation": True,
                        "evidence": [{"type": "historical-cochange", "score": score}],
                        "source": "scored-cochange-candidate",
                    }

        issues: list[str] = []
        for field in ("before_behaviors", "after_behaviors", "business_objects", "change_types"):
            if not spec.get(field):
                issues.append(f"ChangeSpec has no {field}")
        tier_counts: dict[str, int] = defaultdict(int)
        for row in rows:
            tier_counts[row["knowledge_tier"]] += 1

        result = {
            "schema_version": 3,
            "change_spec": spec,
            "scan": {
                "mode": "full-corpus-weighted-graph",
                "uses_embeddings": False,
                "uses_top_k": False,
                "total_cases": len(rows),
                "knowledge_tier_counts": dict(sorted(tier_counts.items())),
                "rejected_cases_excluded": tier_counts.get("rejected", 0),
                "matched_cases": len(matched_cases),
                "direct_matches": len(direct_case_ids),
                "relation_propagated_matches": len(propagated_case_ids),
                "min_match_score": args.min_match_score,
                "graph_depth": args.graph_depth,
                "code_snapshot_used": bool(code_db_path and code_db_path.is_file()),
            },
            "matched_cases": matched_cases,
            "historical_scenarios": sorted(
                scenarios.values(), key=lambda item: item["scenario"].casefold()
            ),
            "candidate_services": sorted(
                services.values(), key=lambda item: item["service"].casefold()
            ),
            "service_cochanges": sorted(
                cochanges, key=lambda item: (-item["score"], item["service_a"], item["service_b"])
            ),
            "historical_omissions": omissions,
            "historical_conflicts": conflicts,
            "code_impacts": code_impacts,
            "graph_propagation": {
                "historical_paths": history_paths,
                "code_paths": code_paths,
            },
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
                "code_impacts": len(result["code_impacts"]),
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
