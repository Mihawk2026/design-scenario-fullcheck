from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "review-design-impact" / "scripts"


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class PipelineTest(unittest.TestCase):
    def test_local_history_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            docs = root / "docs"
            state = root / ".design-impact"
            cases = state / "cases"
            docs.mkdir()
            (docs / "order-freeze-v1.md").write_text("# 订单冻结设计", encoding="utf-8")

            workspace_result = run_script(
                "workspace_state.py",
                "--workspace",
                str(root),
            )
            self.assertEqual(workspace_result.returncode, 0, workspace_result.stderr)
            session = json.loads((state / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["document_count"], 1)
            self.assertEqual(len(session["pending_extraction"]), 1)
            self.assertEqual(session["discovery_mode"], "design-name-or-directory")

            manifest = root / "manifest.json"
            inventory = run_script(
                "inventory_documents.py",
                "--root",
                str(docs),
                "--output",
                str(manifest),
            )
            self.assertEqual(inventory.returncode, 0, inventory.stderr)
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest_data["documents"]), 1)
            source_hash = manifest_data["documents"][0]["sha256"]

            change_case = {
                "case_id": "order-freeze-v1",
                "title": "订单冻结",
                "source": {
                    "path": str(docs / "order-freeze-v1.md"),
                    "sha256": source_hash,
                    "version": "V1",
                },
                "domain": "交易",
                "business_objects": ["订单"],
                "capabilities": ["订单生命周期"],
                "change_types": ["新增状态"],
                "actions": ["冻结"],
                "states": ["FROZEN"],
                "business_invariants": ["冻结期间禁止支付"],
                "scenarios": [
                    {
                        "name": "冻结与支付并发",
                        "expected_behavior": "只能一个操作成功",
                        "evidence": {"kind": "explicit"},
                    }
                ],
                "service_changes": [
                    {
                        "service": "order-service",
                        "responsibility": "订单状态真相源",
                        "asset_types": ["domain-logic"],
                        "modifications": ["增加冻结状态"],
                        "evidence": {"kind": "explicit"},
                    },
                    {
                        "service": "payment-service",
                        "responsibility": "支付许可校验",
                        "asset_types": ["domain-logic"],
                        "modifications": ["拒绝冻结订单支付"],
                        "evidence": {"kind": "explicit"},
                    },
                ],
                "relations": [],
                "historical_omissions": [],
            }
            (cases / "order-freeze-v1.json").write_text(
                json.dumps(change_case, ensure_ascii=False), encoding="utf-8"
            )

            refreshed_state = run_script(
                "workspace_state.py",
                "--workspace",
                str(root),
            )
            self.assertEqual(refreshed_state.returncode, 0, refreshed_state.stderr)
            session = json.loads((state / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["pending_extraction"], [])

            database = root / "history.db"
            compile_result = run_script(
                "compile_history.py",
                "--cases",
                str(cases),
                "--output",
                str(database),
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)

            spec = {
                "title": "订单增加冻结能力",
                "before_behaviors": ["订单可以支付"],
                "after_behaviors": ["订单可以被冻结"],
                "business_objects": ["订单"],
                "capabilities": [],
                "change_types": ["新增状态"],
                "actions": ["冻结"],
                "states": ["FROZEN"],
                "actors": [],
                "triggers": [],
                "changed_rules": [],
                "invariants": ["冻结期间禁止支付"],
            }
            spec_path = root / "change-spec.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            impact_path = root / "impact.json"
            impact_result = run_script(
                "analyze_impact.py",
                "--db",
                str(database),
                "--change-spec",
                str(spec_path),
                "--output",
                str(impact_path),
            )
            self.assertEqual(impact_result.returncode, 0, impact_result.stderr)
            impact = json.loads(impact_path.read_text(encoding="utf-8"))
            self.assertEqual(impact["scan"]["total_cases"], 1)
            self.assertEqual(impact["scan"]["matched_cases"], 1)
            self.assertEqual(len(impact["historical_scenarios"]), 1)
            self.assertEqual(len(impact["candidate_services"]), 2)

            review = {
                "change_spec": spec,
                "summary": {
                    "total_obligations": 1,
                    "covered": 1,
                    "partial": 0,
                    "missing": 0,
                    "conflict": 0,
                    "unverified": 0,
                    "not_applicable": 0,
                },
                "scenario_coverage": [
                    {
                        "scenario": "冻结与支付并发",
                        "status": "covered",
                        "responsible_services": ["order-service", "payment-service"],
                        "severity": "high",
                        "confidence": "high",
                        "evidence": [{"type": "historical", "case_id": "order-freeze-v1"}],
                    }
                ],
                "service_modifications": [
                    {
                        "service": "order-service",
                        "reason": "状态真相源",
                        "status": "confirmed",
                        "modifications": ["增加冻结状态"],
                        "tests": ["状态迁移"],
                        "evidence": [{"type": "historical"}],
                    },
                    {
                        "service": "payment-service",
                        "reason": "支付许可校验",
                        "status": "confirmed",
                        "modifications": ["拒绝冻结订单支付"],
                        "tests": ["冻结后支付"],
                        "evidence": [{"type": "historical"}],
                    },
                ],
                "cross_service_review": {
                    "source_of_truth": "order-service",
                    "failure_matrix": [],
                    "publish_order": [],
                    "rollback": [],
                    "open_items": [],
                },
                "findings": [],
                "open_questions": [],
                "evidence_trace": [],
            }
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            validation = run_script("validate_review.py", "--input", str(review_path))
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)


if __name__ == "__main__":
    unittest.main()
