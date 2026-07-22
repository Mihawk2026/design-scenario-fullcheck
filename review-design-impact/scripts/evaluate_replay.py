#!/usr/bin/env python3
"""Replay historical changes and measure scenario/service/evidence retrieval quality."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).strip().casefold().split())


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def resolve_path(base: Path, value: Any, required: bool = True) -> Path | None:
    if not value:
        if required:
            raise ValueError("Required replay path is missing")
        return None
    path = Path(str(value))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def expected_names(values: Any, field: str) -> set[str]:
    result: set[str] = set()
    if not isinstance(values, list):
        return result
    for value in values:
        if isinstance(value, str):
            name = value
        elif isinstance(value, dict):
            name = value.get(field, "")
        else:
            continue
        if isinstance(name, str) and name.strip():
            result.add(normalize(name))
    return result


def counts(predicted: set[str], expected: set[str]) -> dict[str, int]:
    return {
        "true_positive": len(predicted & expected),
        "false_positive": len(predicted - expected),
        "false_negative": len(expected - predicted),
    }


def metrics(values: dict[str, int]) -> dict[str, float | int]:
    true_positive = values["true_positive"]
    predicted = true_positive + values["false_positive"]
    expected = true_positive + values["false_negative"]
    precision = true_positive / predicted if predicted else (1.0 if not expected else 0.0)
    recall = true_positive / expected if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        **values,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def expected_evidence(values: Any) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("scenario"), str):
            continue
        case_ids = value.get("case_ids", [])
        if isinstance(case_ids, list):
            result[normalize(value["scenario"])] = {
                normalize(case_id) for case_id in case_ids if isinstance(case_id, str)
            }
    return result


def replay_case(
    analyzer: Path,
    dataset_dir: Path,
    item: dict[str, Any],
    output: Path,
    defaults: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, int]]]:
    history_db = resolve_path(dataset_dir, item.get("history_db", defaults.get("history_db")))
    change_spec = resolve_path(dataset_dir, item.get("change_spec"))
    code_db = resolve_path(
        dataset_dir, item.get("code_db", defaults.get("code_db")), required=False
    )
    aliases = resolve_path(
        dataset_dir, item.get("aliases", defaults.get("aliases")), required=False
    )
    command = [
        sys.executable,
        str(analyzer),
        "--db",
        str(history_db),
        "--change-spec",
        str(change_spec),
        "--output",
        str(output),
    ]
    if code_db:
        command.extend(["--code-db", str(code_db)])
    if aliases:
        command.extend(["--aliases", str(aliases)])
    for option in ("min_match_score", "graph_depth"):
        value = item.get(option, defaults.get(option))
        if value is not None:
            command.extend([f"--{option.replace('_', '-')}", str(value)])
    completed = subprocess.run(
        command, check=False, capture_output=True, text=True, encoding="utf-8"
    )
    if completed.returncode:
        raise RuntimeError(
            f"Replay {item.get('id', '<unknown>')} failed: "
            f"{completed.stderr or completed.stdout}"
        )
    impact = load_object(output)
    predicted_scenarios = expected_names(impact.get("historical_scenarios"), "scenario")
    predicted_services = expected_names(impact.get("candidate_services"), "service")
    wanted_scenarios = expected_names(item.get("expected_scenarios"), "scenario")
    wanted_services = expected_names(item.get("expected_services"), "service")
    result_counts = {
        "scenarios": counts(predicted_scenarios, wanted_scenarios),
        "services": counts(predicted_services, wanted_services),
    }

    wanted_evidence = expected_evidence(item.get("expected_evidence"))
    predicted_evidence: set[str] = set()
    expected_evidence_ids: set[str] = set()
    for scenario in impact.get("historical_scenarios", []):
        if not isinstance(scenario, dict):
            continue
        scenario_key = normalize(str(scenario.get("scenario", "")))
        if scenario_key not in wanted_evidence:
            continue
        expected_evidence_ids.update(wanted_evidence[scenario_key])
        for case_id in scenario.get("case_ids", []):
            if isinstance(case_id, str):
                predicted_evidence.add(normalize(case_id))
    result_counts["evidence"] = counts(predicted_evidence, expected_evidence_ids)
    case_result = {
        "id": item.get("id"),
        "metrics": {key: metrics(value) for key, value in result_counts.items()},
        "missing_scenarios": sorted(wanted_scenarios - predicted_scenarios),
        "unexpected_scenarios": sorted(predicted_scenarios - wanted_scenarios),
        "missing_services": sorted(wanted_services - predicted_services),
        "unexpected_services": sorted(predicted_services - wanted_services),
    }
    return case_result, result_counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate impact discovery by historical replay.")
    parser.add_argument("--dataset", required=True, help="Replay dataset JSON")
    parser.add_argument("--output", required=True, help="Evaluation result JSON")
    args = parser.parse_args()

    dataset_path = Path(args.dataset).resolve()
    output_path = Path(args.output).resolve()
    analyzer = Path(__file__).with_name("analyze_impact.py")
    try:
        dataset = load_object(dataset_path)
        cases = dataset.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("Replay dataset requires a non-empty cases list")
        defaults = dataset.get("defaults", {})
        if not isinstance(defaults, dict):
            raise ValueError("defaults must be an object")
        totals = {
            name: {"true_positive": 0, "false_positive": 0, "false_negative": 0}
            for name in ("scenarios", "services", "evidence")
        }
        results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="design-impact-replay-") as raw_temp:
            temp = Path(raw_temp)
            for index, item in enumerate(cases):
                if not isinstance(item, dict) or not item.get("id"):
                    raise ValueError(f"cases[{index}] requires an id")
                case_result, case_counts = replay_case(
                    analyzer,
                    dataset_path.parent,
                    item,
                    temp / f"impact-{index}.json",
                    defaults,
                )
                results.append(case_result)
                for metric_name, values in case_counts.items():
                    for field, value in values.items():
                        totals[metric_name][field] += value
        overall = {name: metrics(values) for name, values in totals.items()}
        thresholds = dataset.get("thresholds", {})
        if not isinstance(thresholds, dict):
            raise ValueError("thresholds must be an object")
        checks = {
            "scenario_recall": overall["scenarios"]["recall"],
            "scenario_precision": overall["scenarios"]["precision"],
            "service_recall": overall["services"]["recall"],
            "service_precision": overall["services"]["precision"],
            "evidence_precision": overall["evidence"]["precision"],
        }
        failures = [
            {
                "metric": name,
                "actual": value,
                "required": float(thresholds[name]),
            }
            for name, value in checks.items()
            if name in thresholds and value < float(thresholds[name])
        ]
        report = {
            "schema_version": 1,
            "passed": not failures,
            "case_count": len(results),
            "overall": overall,
            "thresholds": thresholds,
            "failures": failures,
            "cases": results,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if failures else 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
