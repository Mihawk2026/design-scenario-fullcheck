#!/usr/bin/env python3
"""Validate the machine-readable design impact review and enforce completion gates."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


ALLOWED_STATUSES = {
    "covered",
    "partial",
    "missing",
    "conflict",
    "unverified",
    "not-applicable",
}
HIGH_SEVERITY = {"critical", "high"}
ALLOWED_KNOWLEDGE_TIERS = {"trusted", "candidate", "conflict", "heuristic"}
ALLOWED_SERVICE_STATUSES = {
    "confirmed",
    "historical-candidate",
    "code-candidate",
    "partial",
    "missing",
    "conflict",
    "unverified",
    "not-applicable",
}


def nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def normalize_label(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def validate_evidence_list(
    value: Any, prefix: str, trace_ids: set[str], errors: list[str]
) -> None:
    if not isinstance(value, list):
        errors.append(f"{prefix} must be a list")
        return
    for index, evidence in enumerate(value):
        evidence_prefix = f"{prefix}[{index}]"
        if not isinstance(evidence, dict):
            errors.append(f"{evidence_prefix} must be an object")
            continue
        evidence_type = evidence.get("type")
        if not evidence_type:
            errors.append(f"{evidence_prefix}.type is required")
        if evidence_type == "historical" and not evidence.get("case_id"):
            errors.append(f"{evidence_prefix}.case_id is required for historical evidence")
        if evidence_type == "offline-code" and not (
            evidence.get("entity_id") or nonempty_list(evidence.get("entity_ids"))
        ):
            errors.append(
                f"{evidence_prefix} requires entity_id or entity_ids for offline-code evidence"
            )
        trace_id = evidence.get("trace_id")
        if trace_id and trace_id not in trace_ids:
            errors.append(f"{evidence_prefix}.trace_id does not exist: {trace_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a design impact review JSON.")
    parser.add_argument("--input", required=True, help="Review JSON")
    args = parser.parse_args()

    path = Path(args.input).resolve()
    try:
        report = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("Review must be a JSON object.", file=sys.stderr)
        return 2

    errors: list[str] = []
    warnings: list[str] = []
    required_top_level = {
        "change_spec",
        "knowledge_quality",
        "scenario_coverage",
        "service_modifications",
        "cross_service_review",
        "findings",
        "knowledge_conflicts",
        "open_questions",
        "evidence_trace",
    }
    for field in sorted(required_top_level):
        if field not in report:
            errors.append(f"Missing top-level field: {field}")

    evidence_trace = report.get("evidence_trace", [])
    trace_ids: set[str] = set()
    if not isinstance(evidence_trace, list):
        errors.append("evidence_trace must be a list")
        evidence_trace = []
    for index, trace in enumerate(evidence_trace):
        prefix = f"evidence_trace[{index}]"
        if not isinstance(trace, dict):
            errors.append(f"{prefix} must be an object")
            continue
        trace_id = trace.get("id")
        if not isinstance(trace_id, str) or not trace_id.strip():
            errors.append(f"{prefix}.id is required")
        elif trace_id in trace_ids:
            errors.append(f"Duplicate evidence trace id: {trace_id}")
        else:
            trace_ids.add(trace_id)
        if not trace.get("type"):
            errors.append(f"{prefix}.type is required")
        if not trace.get("source"):
            errors.append(f"{prefix}.source is required")

    change_spec = report.get("change_spec", {})
    if not isinstance(change_spec, dict):
        errors.append("change_spec must be an object")
    else:
        for field in ("before_behaviors", "after_behaviors", "business_objects", "change_types"):
            if not nonempty_list(change_spec.get(field)):
                errors.append(f"change_spec.{field} must be a non-empty list")

    scenarios = report.get("scenario_coverage", [])
    if not isinstance(scenarios, list):
        errors.append("scenario_coverage must be a list")
        scenarios = []
    status_counts: Counter[str] = Counter()
    scenario_names: dict[str, tuple[int, Any]] = {}
    for index, scenario in enumerate(scenarios):
        prefix = f"scenario_coverage[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not scenario.get("scenario"):
            errors.append(f"{prefix}.scenario is required")
        scenario_key = normalize_label(scenario.get("scenario"))
        if scenario_key in scenario_names:
            prior_index, prior_status = scenario_names[scenario_key]
            errors.append(
                f"{prefix}.scenario duplicates scenario_coverage[{prior_index}]"
            )
            if prior_status != scenario.get("status"):
                errors.append(f"{prefix} conflicts with the duplicate scenario status")
        elif scenario_key:
            scenario_names[scenario_key] = (index, scenario.get("status"))
        status = scenario.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status is invalid: {status}")
        else:
            status_counts[status] += 1
        if status != "not-applicable" and not nonempty_list(
            scenario.get("responsible_services")
        ):
            errors.append(f"{prefix} has no responsible_services")
        if status == "not-applicable" and not scenario.get("not_applicable_reason"):
            errors.append(f"{prefix} requires not_applicable_reason")
        if status in {"covered", "partial", "conflict"} and not nonempty_list(
            scenario.get("evidence")
        ):
            errors.append(f"{prefix} requires evidence for status {status}")
        validate_evidence_list(
            scenario.get("evidence", []), f"{prefix}.evidence", trace_ids, errors
        )
        tier = scenario.get("knowledge_tier")
        if tier not in ALLOWED_KNOWLEDGE_TIERS:
            errors.append(f"{prefix}.knowledge_tier is invalid or missing")
        if tier != "trusted" and scenario.get("requires_confirmation") is not True:
            errors.append(f"{prefix} is non-trusted and requires_confirmation must be true")
        if scenario.get("severity") in HIGH_SEVERITY and status in {"missing", "conflict"}:
            if not nonempty_list(scenario.get("evidence")):
                errors.append(f"{prefix} is high risk but has no evidence")
            if tier in {"candidate", "heuristic"} and scenario.get("confidence") == "high":
                errors.append(f"{prefix} cannot be high confidence from {tier} knowledge alone")
        if status == "unverified" and nonempty_list(scenario.get("evidence")):
            warnings.append(f"{prefix} is unverified but includes evidence; confirm the status")

    services = report.get("service_modifications", [])
    if not isinstance(services, list):
        errors.append("service_modifications must be a list")
        services = []
    service_names: dict[str, int] = {}
    for index, service in enumerate(services):
        prefix = f"service_modifications[{index}]"
        if not isinstance(service, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in ("service", "reason"):
            if not service.get(field):
                errors.append(f"{prefix}.{field} is required")
        if service.get("status") not in ALLOWED_SERVICE_STATUSES:
            errors.append(f"{prefix}.status is invalid or missing")
        service_key = normalize_label(service.get("service"))
        if service_key in service_names:
            errors.append(
                f"{prefix}.service duplicates service_modifications[{service_names[service_key]}]"
            )
        elif service_key:
            service_names[service_key] = index
        for field in ("modifications", "tests", "evidence"):
            if not isinstance(service.get(field), list):
                errors.append(f"{prefix}.{field} must be a list")
        if not nonempty_list(service.get("modifications")) and service.get("status") != "not-applicable":
            errors.append(f"{prefix} has no modifications and is not marked not-applicable")
        if service.get("status") != "not-applicable" and not nonempty_list(
            service.get("evidence")
        ):
            errors.append(f"{prefix} has no evidence and is not marked not-applicable")
        validate_evidence_list(
            service.get("evidence", []), f"{prefix}.evidence", trace_ids, errors
        )
        service_tier = service.get("knowledge_tier")
        if service_tier not in ALLOWED_KNOWLEDGE_TIERS:
            errors.append(f"{prefix}.knowledge_tier is invalid or missing")
        if service_tier != "trusted" and service.get("requires_confirmation") is not True:
            errors.append(f"{prefix} is non-trusted and requires_confirmation must be true")

    cross_service = report.get("cross_service_review", {})
    if not isinstance(cross_service, dict):
        errors.append("cross_service_review must be an object")
    else:
        for field in ("failure_matrix", "publish_order", "rollback", "open_items"):
            if not isinstance(cross_service.get(field), list):
                errors.append(f"cross_service_review.{field} must be a list")
        if len(services) > 1 and not cross_service.get("source_of_truth"):
            errors.append("cross_service_review.source_of_truth is required for multiple services")
        source_of_truth = normalize_label(cross_service.get("source_of_truth"))
        if source_of_truth and source_of_truth not in service_names:
            errors.append("cross_service_review.source_of_truth must name a reviewed service")

    findings = report.get("findings", [])
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []
    for index, finding in enumerate(findings):
        prefix = f"findings[{index}]"
        if not isinstance(finding, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if finding.get("status") not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status is invalid")
        if finding.get("severity") in HIGH_SEVERITY and finding.get("status") in {"missing", "conflict"}:
            if not nonempty_list(finding.get("evidence")):
                errors.append(f"{prefix} is high risk but has no evidence")
        validate_evidence_list(
            finding.get("evidence", []), f"{prefix}.evidence", trace_ids, errors
        )

    knowledge_quality = report.get("knowledge_quality")
    if not isinstance(knowledge_quality, dict):
        errors.append("knowledge_quality must be an object")

    conflicts = report.get("knowledge_conflicts", [])
    if not isinstance(conflicts, list):
        errors.append("knowledge_conflicts must be a list")
        conflicts = []
    for index, conflict in enumerate(conflicts):
        prefix = f"knowledge_conflicts[{index}]"
        if not isinstance(conflict, dict) or not conflict.get("topic"):
            errors.append(f"{prefix}.topic is required")
            continue
        claims = conflict.get("claims")
        if not isinstance(claims, list) or len(claims) < 2:
            errors.append(f"{prefix}.claims requires at least two claims")

    summary = report.get("summary")
    if isinstance(summary, dict):
        expected_total = len(scenarios)
        if summary.get("total_obligations") != expected_total:
            warnings.append(
                f"summary.total_obligations is {summary.get('total_obligations')}, expected {expected_total}"
            )
        for status, count in status_counts.items():
            summary_key = "not_applicable" if status == "not-applicable" else status
            if summary.get(summary_key) != count:
                warnings.append(
                    f"summary.{summary_key} is {summary.get(summary_key)}, expected {count}"
                )
    else:
        warnings.append("summary is missing or is not an object")

    result = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "scenarios": len(scenarios),
            "services": len(services),
            "findings": len(findings),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
