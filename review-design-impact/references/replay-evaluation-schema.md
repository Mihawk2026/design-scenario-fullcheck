# Historical replay evaluation

Use replay evaluation to measure whether a knowledge-base or analyzer change still finds the scenarios and services that were actually required by past changes. The expected labels must come from accepted final designs, defect reviews, incidents, or an SE-reviewed answer key; do not generate the answer key from the analyzer output being evaluated.

```json
{
  "defaults": {
    "history_db": ".design-impact/history.db",
    "code_db": ".design-impact/code-facts.db",
    "aliases": ".design-impact/aliases.json",
    "graph_depth": 2,
    "min_match_score": 1.0
  },
  "thresholds": {
    "scenario_recall": 0.9,
    "scenario_precision": 0.6,
    "service_recall": 0.9,
    "service_precision": 0.6,
    "evidence_precision": 0.9
  },
  "cases": [
    {
      "id": "order-freeze-2025-04",
      "change_spec": "replay/order-freeze/change-spec.json",
      "expected_scenarios": [
        "冻结与支付并发",
        "冻结期间自动关单"
      ],
      "expected_services": [
        "order-service",
        "payment-service",
        "scheduler-service"
      ],
      "expected_evidence": [
        {
          "scenario": "冻结与支付并发",
          "case_ids": ["order-freeze-v2"]
        }
      ]
    }
  ]
}
```

Paths are relative to the dataset file unless absolute. Per-case `history_db`, `code_db`, `aliases`, `graph_depth`, and `min_match_score` override defaults.

Run `scripts/evaluate_replay.py --dataset <dataset> --output <result>` internally. Exit code `1` means a configured quality threshold was missed; exit code `2` means the evaluation could not execute. Inspect case-level missing and unexpected scenario/service lists before changing matching thresholds.

Recall is the primary omission-control gate. Precision is also required so that weak service co-occurrence and broad lexical matches do not turn every review into an unusable list. Evidence precision is measured only for scenarios with `expected_evidence`; include enough curated examples to make it meaningful.

Never use the same analyzer result to create and score expected labels. A useful initial set contains representative changes, known historical omissions, cross-service changes, term-renaming cases, and cases where code structure does not expose the business relationship.
