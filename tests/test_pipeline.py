from __future__ import annotations

import hashlib
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
                        "evidence": {"kind": "explicit", "section": "异常场景"},
                    }
                ],
                "service_changes": [
                    {
                        "service": "order-service",
                        "responsibility": "订单状态真相源",
                        "asset_types": ["domain-logic"],
                        "modifications": ["增加冻结状态"],
                        "evidence": {"kind": "explicit", "section": "服务改造"},
                    },
                    {
                        "service": "payment-service",
                        "responsibility": "支付许可校验",
                        "asset_types": ["domain-logic"],
                        "modifications": ["拒绝冻结订单支付"],
                        "evidence": {"kind": "explicit", "section": "服务改造"},
                    },
                ],
                "relations": [],
                "historical_omissions": [],
                "conflicts": [],
                "uncertain_fields": [],
                "validation": {
                    "status": "validated",
                    "confidence": "high",
                    "method": "independent-source-reread",
                    "issues": [],
                },
            }
            (cases / "order-freeze-v1.json").write_text(
                json.dumps(change_case, ensure_ascii=False), encoding="utf-8"
            )
            conflict_case = {
                **change_case,
                "case_id": "order-freeze-cancel-conflict",
                "title": "冻结期间取消规则冲突",
                "change_types": ["修改业务规则"],
                "scenarios": [
                    {
                        "name": "冻结期间取消",
                        "expected_behavior": "需要区分冻结类型",
                        "evidence": {"kind": "explicit", "section": "取消规则"},
                    }
                ],
                "service_changes": [
                    {
                        "service": "customer-admin",
                        "responsibility": "客服取消入口",
                        "asset_types": ["admin-ui"],
                        "modifications": ["按冻结类型限制取消"],
                        "evidence": {"kind": "explicit", "section": "客服操作"},
                    }
                ],
                "conflicts": [
                    {
                        "topic": "冻结期间是否允许取消",
                        "claims": [
                            {
                                "value": "允许",
                                "evidence": {"kind": "explicit", "section": "人工冻结"},
                            },
                            {
                                "value": "禁止",
                                "evidence": {"kind": "explicit", "section": "风控冻结"},
                            },
                        ],
                        "resolution_status": "unresolved",
                    }
                ],
                "validation": {
                    "status": "conflict",
                    "confidence": "high",
                    "method": "independent-source-reread",
                    "issues": ["冻结类型适用范围不同"],
                },
            }
            (cases / "order-freeze-conflict.json").write_text(
                json.dumps(conflict_case, ensure_ascii=False), encoding="utf-8"
            )

            refreshed_state = run_script(
                "workspace_state.py",
                "--workspace",
                str(root),
            )
            self.assertEqual(refreshed_state.returncode, 0, refreshed_state.stderr)
            session = json.loads((state / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["pending_extraction"], [])
            self.assertEqual(session["pending_validation"], [])

            database = root / "history.db"
            compile_result = run_script(
                "compile_history.py",
                "--cases",
                str(cases),
                "--output",
                str(database),
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            quality = json.loads(
                (root / "history.quality.json").read_text(encoding="utf-8")
            )
            self.assertEqual(quality["knowledge_tier_counts"]["trusted"], 1)
            self.assertEqual(quality["knowledge_tier_counts"]["conflict"], 1)
            self.assertEqual(len(quality["human_review_queue"]), 1)

            capture_dir = state / "codegraph-mcp"
            raw_dir = capture_dir / "raw"
            raw_dir.mkdir(parents=True)
            raw_response = raw_dir / "cg-0001.txt"
            raw_response.write_text(
                "Order status readers, writers, call paths, and blast radius.",
                encoding="utf-8",
            )
            response_digest = hashlib.sha256(raw_response.read_bytes()).hexdigest()
            code_export = {
                "schema_version": 2,
                "generated_at": "2026-07-22T10:00:00+00:00",
                "mcp": {
                    "server": "codegraph",
                    "implementation": "colbymchenry/codegraph",
                    "transport": "mcp",
                    "tools_observed": ["codegraph_explore"],
                },
                "repositories": [
                    {
                        "name": "order-service",
                        "branch": "main",
                        "commit": "abc123",
                        "indexed_at": "2026-07-22T09:55:00+00:00",
                        "coverage": ["source-code", "sql"],
                        "not_covered": ["scheduler-platform"],
                    }
                ],
                "mcp_calls": [
                    {
                        "id": "cg-0001",
                        "repository": "order-service",
                        "tool": "codegraph_explore",
                        "arguments": {
                            "query": "Trace order status readers and writers."
                        },
                        "response_path": "raw/cg-0001.txt",
                        "response_sha256": response_digest,
                        "observed_at": "2026-07-22T09:56:00+00:00",
                        "status": "ok",
                        "staleness": "fresh",
                    }
                ],
                "query_seeds": [
                    {
                        "repository": "order-service",
                        "category": "state",
                        "seed": "Order.status / WAIT_PAY",
                        "status": "matched",
                        "mcp_call_ids": ["cg-0001"],
                        "notes": "Reader and writer paths were returned.",
                    }
                ],
                "entities": [
                    {
                        "id": "order-service:CloseExpiredOrderJob",
                        "type": "scheduled-job",
                        "name": "CloseExpiredOrderJob",
                        "service": "order-service",
                        "repository": "order-service",
                        "location": {"file": "src/jobs/CloseExpiredOrderJob.java", "line": 42},
                        "evidence": {
                            "kind": "codegraph-mcp",
                            "mcp_call_id": "cg-0001",
                        },
                    }
                ],
                "relations": [
                    {
                        "source_id": "order-service:CloseExpiredOrderJob",
                        "type": "reads-state",
                        "target_id": "order-service:Order.status",
                        "repository": "order-service",
                        "evidence": {
                            "kind": "codegraph-mcp",
                            "mcp_call_id": "cg-0001",
                        },
                    }
                ],
                "business_mappings": [
                    {
                        "business_object": "订单",
                        "state": "WAIT_PAY",
                        "action": "自动关单",
                        "asset_id": "order-service:CloseExpiredOrderJob",
                        "confidence": "high",
                        "evidence": {
                            "kind": "codegraph-mcp",
                            "mcp_call_id": "cg-0001",
                        },
                    }
                ],
            }
            code_export_path = capture_dir / "capture.json"
            code_export_path.write_text(
                json.dumps(code_export, ensure_ascii=False), encoding="utf-8"
            )
            code_database = state / "code-facts.db"
            code_compile = run_script(
                "compile_code_facts.py",
                "--input",
                str(code_export_path),
                "--output",
                str(code_database),
            )
            self.assertEqual(code_compile.returncode, 0, code_compile.stderr)
            self.assertTrue(code_database.is_file())
            self.assertTrue((state / "code-manifest.json").is_file())
            self.assertTrue((state / "code-coverage.json").is_file())
            code_manifest = json.loads(
                (state / "code-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(code_manifest["repositories"][0]["commit"], "abc123")
            self.assertEqual(code_manifest["source"]["transport"], "mcp")
            self.assertEqual(code_manifest["mcp_call_count"], 1)
            self.assertEqual(code_manifest["query_seed_count"], 1)

            snapshot_state = run_script(
                "workspace_state.py",
                "--workspace",
                str(root),
            )
            self.assertEqual(snapshot_state.returncode, 0, snapshot_state.stderr)
            session = json.loads((state / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["code_snapshot_status"], "unknown")
            self.assertFalse(session["code_update_required"])
            self.assertNotIn("refresh_code_snapshot", session["next_actions"])

            code_export["mcp_calls"][0]["status"] = "truncated"
            code_export["query_seeds"][0]["status"] = "truncated"
            code_export_path.write_text(
                json.dumps(code_export, ensure_ascii=False), encoding="utf-8"
            )
            rejected_stale_confidence = run_script(
                "compile_code_facts.py",
                "--input",
                str(code_export_path),
                "--output",
                str(code_database),
            )
            self.assertEqual(rejected_stale_confidence.returncode, 1)
            self.assertIn(
                "cannot be high confidence",
                rejected_stale_confidence.stderr,
            )

            code_export["business_mappings"][0]["confidence"] = "medium"
            code_export_path.write_text(
                json.dumps(code_export, ensure_ascii=False), encoding="utf-8"
            )
            stale_compile = run_script(
                "compile_code_facts.py",
                "--input",
                str(code_export_path),
                "--output",
                str(code_database),
            )
            self.assertEqual(stale_compile.returncode, 0, stale_compile.stderr)
            stale_state = run_script(
                "workspace_state.py",
                "--workspace",
                str(root),
            )
            self.assertEqual(stale_state.returncode, 0, stale_state.stderr)
            session = json.loads((state / "session.json").read_text(encoding="utf-8"))
            self.assertEqual(session["code_snapshot_status"], "stale")
            self.assertTrue(session["code_update_required"])
            self.assertIn("refresh_code_snapshot", session["next_actions"])

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
            self.assertEqual(impact["scan"]["total_cases"], 2)
            self.assertEqual(impact["scan"]["matched_cases"], 2)
            self.assertEqual(len(impact["historical_scenarios"]), 2)
            self.assertEqual(len(impact["candidate_services"]), 3)
            self.assertEqual(len(impact["historical_conflicts"]), 1)
            trusted_scenario = next(
                item
                for item in impact["historical_scenarios"]
                if item["scenario"] == "冻结与支付并发"
            )
            self.assertEqual(trusted_scenario["knowledge_tier"], "trusted")

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
                "knowledge_quality": {
                    "trusted_cases": 1,
                    "candidate_cases": 0,
                    "conflict_cases": 1,
                    "rejected_cases": 0,
                    "human_review_queue": 1,
                },
                "scenario_coverage": [
                    {
                        "scenario": "冻结与支付并发",
                        "status": "covered",
                        "responsible_services": ["order-service", "payment-service"],
                        "severity": "high",
                        "confidence": "high",
                        "knowledge_tier": "trusted",
                        "requires_confirmation": False,
                        "evidence": [{"type": "historical", "case_id": "order-freeze-v1"}],
                    }
                ],
                "service_modifications": [
                    {
                        "service": "order-service",
                        "reason": "状态真相源",
                        "status": "confirmed",
                        "knowledge_tier": "trusted",
                        "requires_confirmation": False,
                        "modifications": ["增加冻结状态"],
                        "tests": ["状态迁移"],
                        "evidence": [{"type": "historical"}],
                    },
                    {
                        "service": "payment-service",
                        "reason": "支付许可校验",
                        "status": "confirmed",
                        "knowledge_tier": "trusted",
                        "requires_confirmation": False,
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
                "knowledge_conflicts": [
                    {
                        "topic": "冻结期间是否允许取消",
                        "claims": [
                            {"value": "允许", "source": "人工冻结"},
                            {"value": "禁止", "source": "风控冻结"},
                        ],
                        "resolution_status": "unresolved",
                    }
                ],
                "open_questions": [],
                "evidence_trace": [],
            }
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            validation = run_script("validate_review.py", "--input", str(review_path))
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)


if __name__ == "__main__":
    unittest.main()
