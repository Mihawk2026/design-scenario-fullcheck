#!/usr/bin/env python3
"""Validate the machine-readable design impact review and enforce completion gates."""

from __future__ import annotations

import argparse
import json
import sys
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


def nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


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
        "scenario_coverage",
        "service_modifications",
        "cross_service_review",
        "findings",
        "open_questions",
        "evidence_trace",
    }
    for field in sorted(required_top_level):
        if field not in report:
            errors.append(f"Missing top-level field: {field}")

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
    for index, scenario in enumerate(scenarios):
        prefix = f"scenario_coverage[{index}]"
        if not isinstance(scenario, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not scenario.get("scenario"):
            errors.append(f"{prefix}.scenario is required")
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
        if scenario.get("severity") in HIGH_SEVERITY and status in {"missing", "conflict"}:
            if not nonempty_list(scenario.get("evidence")):
                errors.append(f"{prefix} is high risk but has no evidence")
        if status == "unverified" and nonempty_list(scenario.get("evidence")):
            warnings.append(f"{prefix} is unverified but includes evidence; confirm the status")

    services = report.get("service_modifications", [])
    if not isinstance(services, list):
        errors.append("service_modifications must be a list")
        services = []
    for index, service in enumerate(services):
        prefix = f"service_modifications[{index}]"
        if not isinstance(service, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for field in ("service", "reason"):
            if not service.get(field):
                errors.append(f"{prefix}.{field} is required")
        for field in ("modifications", "tests", "evidence"):
            if not isinstance(service.get(field), list):
                errors.append(f"{prefix}.{field} must be a list")
        if not nonempty_list(service.get("modifications")) and service.get("status") != "not-applicable":
            errors.append(f"{prefix} has no modifications and is not marked not-applicable")

    cross_service = report.get("cross_service_review", {})
    if not isinstance(cross_service, dict):
        errors.append("cross_service_review must be an object")
    else:
        for field in ("failure_matrix", "publish_order", "rollback", "open_items"):
            if not isinstance(cross_service.get(field), list):
                errors.append(f"cross_service_review.{field} must be a list")
        if len(services) > 1 and not cross_service.get("source_of_truth"):
            errors.append("cross_service_review.source_of_truth is required for multiple services")

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
