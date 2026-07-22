# ChangeCase schema

Use this schema to extract historical design facts. Preserve evidence for every nontrivial fact. Save either one JSON object per `.json` file or newline-delimited objects in `.jsonl`.

## Required fields

```json
{
  "case_id": "order-freeze-v2",
  "title": "订单增加人工冻结能力",
  "source": {
    "path": "D:/designs/order-freeze-v2.docx",
    "version": "V2",
    "sections": ["4.3 服务改造"]
  },
  "supersedes": ["order-freeze-v1"],
  "domain": "交易",
  "business_objects": ["订单"],
  "capabilities": ["订单生命周期管理"],
  "change_types": ["新增状态", "新增操作"],
  "before_behaviors": ["待支付订单可以支付或自动关闭"],
  "after_behaviors": ["待支付订单可以被冻结"],
  "actions": ["冻结", "解冻"],
  "states": ["WAIT_PAY", "FROZEN"],
  "actors": ["客服", "风控系统"],
  "triggers": ["人工操作", "风控事件"],
  "business_invariants": ["冻结期间不得支付或履约"],
  "scenarios": [
    {
      "name": "冻结与支付并发",
      "precondition": "订单处于待支付状态",
      "trigger": "支付和冻结同时发生",
      "expected_behavior": "只能有一个操作成功",
      "evidence": {
        "section": "异常场景/并发处理",
        "kind": "explicit"
      }
    }
  ],
  "service_changes": [
    {
      "service": "order-service",
      "responsibility": "订单状态真相源",
      "asset_types": ["domain-logic", "api", "database"],
      "modifications": ["增加冻结状态", "增加冻结和解冻接口"],
      "evidence": {
        "section": "4.3 服务改造",
        "kind": "explicit"
      }
    }
  ],
  "relations": [
    {
      "source": "订单状态",
      "type": "controls",
      "target": "支付许可",
      "evidence": {
        "section": "业务规则",
        "kind": "explicit"
      }
    }
  ],
  "historical_omissions": [
    {
      "description": "首版遗漏自动关单任务",
      "severity": "high",
      "source": "V1到V2差异",
      "evidence": {
        "kind": "version-diff"
      }
    }
  ],
  "uncertain_fields": []
}
```

## Extraction rules

- Use stable, canonical names. Preserve original aliases in evidence when useful.
- Split a document into multiple cases when it describes independent changes.
- Keep `before_behaviors` and `after_behaviors` separate.
- Record business invariants, not only implementation decisions.
- Include operations, scheduled jobs, data consumers, admin tools, analytics, audit, migration, rollback, observability, and tests when present.
- Set evidence `kind` to `explicit`, `inferred`, `version-diff`, `review-comment`, `defect`, or `incident`.
- Do not label a version difference as an omission unless a source explicitly supports that conclusion.
- Put unresolved or contradictory extraction results in `uncertain_fields`.

## Validation minimum

Every case must contain non-empty `case_id`, `title`, `source.path`, `business_objects`, `change_types`, and at least one of `scenarios` or `service_changes`.
