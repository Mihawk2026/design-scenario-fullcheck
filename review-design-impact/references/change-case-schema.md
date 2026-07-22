# ChangeCase schema

Use this schema to extract historical design facts. Preserve evidence for every nontrivial fact. Save either one JSON object per `.json` file or newline-delimited objects in `.jsonl`.

## Required fields

```json
{
  "case_id": "order-freeze-v2",
  "title": "订单增加人工冻结能力",
  "source": {
    "path": "D:/designs/order-freeze-v2.docx",
    "sha256": "document-sha256",
    "version": "V2",
    "sections": ["4.3 服务改造"]
  },
  "supersedes": ["order-freeze-v1"],
  "domain": "交易",
  "original_terms": {
    "business_objects": ["订单"],
    "states": ["挂起"],
    "services": ["订单中心"]
  },
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
  "behavior_signatures": [
    {
      "business_object": "订单",
      "precondition": "WAIT_PAY",
      "trigger": "客服冻结",
      "action": "冻结",
      "target_state": "FROZEN",
      "allowed_behaviors": ["解冻"],
      "forbidden_behaviors": ["支付", "履约"],
      "recovery": "恢复到冻结前状态",
      "downstream_effects": ["自动关单跳过"],
      "applicability": ["人工冻结"]
    }
  ],
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
      "direction": "forward",
      "propagation": "bidirectional",
      "conditions": ["订单处于可支付状态"],
      "version_scope": {
        "from": "V2",
        "to": null
      },
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
  "conflicts": [],
  "uncertain_fields": [],
  "extraction": {
    "run_id": "extract-20260722-order-freeze-v2",
    "executor": "codex-review-design-impact",
    "completed_at": "2026-07-22T09:00:00+08:00"
  },
  "validation": {
    "status": "validated",
    "confidence": "high",
    "method": "independent-source-reread",
    "run_id": "review-20260722-order-freeze-v2",
    "reviewer": "codex-review-design-impact-pass-2",
    "reviewed_at": "2026-07-22T09:15:00+08:00",
    "source_sha256": "document-sha256",
    "independent_context": true,
    "issues": [],
    "verified_fields": [
      "business_objects",
      "scenarios",
      "service_changes"
    ]
  }
}
```

## Extraction rules

- Use stable, canonical names. Preserve original aliases in evidence when useful.
- Perform document-local extraction before normalization. Preserve source terms in `original_terms`.
- Compare behavior signatures before merging differently named concepts.
- Split a document into multiple cases when it describes independent changes.
- Keep `before_behaviors` and `after_behaviors` separate.
- Record business invariants, not only implementation decisions.
- Include operations, scheduled jobs, data consumers, admin tools, analytics, audit, migration, rollback, observability, and tests when present.
- Set evidence `kind` to `explicit`, `inferred`, `version-diff`, `review-comment`, `defect`, or `incident`.
- Do not label a version difference as an omission unless a source explicitly supports that conclusion.
- Put unresolved or contradictory extraction results in `uncertain_fields`.
- Put competing conclusions in `conflicts`; never discard one side by majority count.
- Independently reread the source and populate `validation` after the first extraction pass.
- For every relation, record evidence direction separately from impact propagation. `direction` is `forward` or `bidirectional`; `propagation` is `forward`, `reverse`, `bidirectional`, or `none`. Preserve applicability in `conditions` and document/version limits in `version_scope`.
- Give extraction and validation different run IDs. Start pass 2 from the source document and the schema, without using pass-1 conclusions as the review context.
- Record extractor, reviewer, completion times, the exact source hash, and the fields checked. These fields are compilation gates, not optional comments.

## Validation statuses

- `validated`: source reread supports the extracted claims.
- `partial`: some claims are supported but gaps remain.
- `unverified`: first-pass extraction has not been independently verified.
- `conflict`: the source or cross-source conclusions conflict.
- `rejected`: unsupported, obsolete, duplicate, or unusable.

Use confidence `high`, `medium`, or `low`. A validated case containing only inferred evidence cannot be treated as trusted.

## Conflict object

```json
{
  "topic": "冻结期间是否允许取消",
  "claims": [
    {
      "value": "允许取消",
      "applicability": ["人工冻结"],
      "evidence": {
        "kind": "explicit",
        "section": "3.2 状态行为"
      }
    },
    {
      "value": "禁止取消",
      "applicability": ["风控冻结"],
      "evidence": {
        "kind": "explicit",
        "section": "4.1 风控规则"
      }
    }
  ],
  "resolution_status": "unresolved",
  "possible_explanation": "冻结类型不同"
}
```

## Validation minimum

Every case must contain non-empty `case_id`, `title`, `source.path`, `source.sha256`, `business_objects`, `change_types`, `validation.status`, `validation.confidence`, and at least one of `scenarios` or `service_changes`. Every scenario and service change must contain evidence kind and source location. `source.sha256` allows the skill to refresh only documents that changed.

A `validated` case is trusted only when `extraction.run_id`, `extraction.executor`, `extraction.completed_at`, `validation.run_id`, `validation.reviewer`, `validation.reviewed_at`, `validation.source_sha256`, `validation.independent_context=true`, and non-empty `validation.verified_fields` are present; extraction and review run IDs must differ, and the reviewed hash must equal `source.sha256`. The compiler rejects promotion when these checks fail. This records independent execution, but it still cannot prove that a model truly ignored prior conclusions; high-risk knowledge remains eligible for human review.
