# Code fact snapshot

Use CodeGraph only during full initialization or incremental update. Export a business-impact projection, compile it locally, and never query live CodeGraph during design review.

## Export shape

```json
{
  "schema_version": 1,
  "generated_at": "2026-07-22T10:00:00+08:00",
  "repositories": [
    {
      "name": "order-service",
      "path": "D:/repos/order-service",
      "branch": "main",
      "commit": "abc123",
      "indexed_at": "2026-07-22T09:55:00+08:00",
      "coverage": ["source-code", "sql", "configuration"],
      "not_covered": ["scheduler-platform", "data-warehouse"]
    }
  ],
  "entities": [
    {
      "id": "order-service:CloseExpiredOrderJob",
      "type": "scheduled-job",
      "name": "CloseExpiredOrderJob",
      "service": "order-service",
      "repository": "order-service",
      "location": {
        "file": "src/jobs/CloseExpiredOrderJob.java",
        "line": 42
      },
      "evidence": {
        "kind": "codegraph",
        "confidence": "high"
      }
    }
  ],
  "relations": [
    {
      "source_id": "order-service:CloseExpiredOrderJob",
      "type": "reads-state",
      "target_id": "order-service:Order.status",
      "repository": "order-service",
      "evidence": {
        "kind": "codegraph",
        "file": "src/jobs/CloseExpiredOrderJob.java",
        "line": 48
      }
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
        "kind": "codegraph"
      }
    }
  ]
}
```

Relations may reference entities omitted from the projection when the external node is still identified by a stable ID.

## Snapshot outputs

Compile the export with `scripts/compile_code_facts.py` to create:

- `code-facts.db`: local structured code facts used during review;
- `code-manifest.json`: repository, branch, commit, and index timestamp;
- `code-coverage.json`: covered and uncovered technical surfaces.

## Runtime boundary

During a design review:

- read only the compiled snapshot;
- never call live CodeGraph;
- never inspect live repository contents as a substitute for the snapshot;
- use positive code facts to confirm or add technical candidates;
- do not remove a business candidate because the snapshot has no matching code relation;
- label uncovered, stale, or unknown repositories explicitly.

If the snapshot is stale, switch to incremental-update mode, refresh it, and only then resume the design review.
