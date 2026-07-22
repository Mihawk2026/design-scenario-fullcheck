# Review report schema

Produce human-readable tables and this machine-readable JSON shape.

```json
{
  "change_spec": {},
  "summary": {
    "total_obligations": 0,
    "covered": 0,
    "partial": 0,
    "missing": 0,
    "conflict": 0,
    "unverified": 0,
    "not_applicable": 0
  },
  "knowledge_quality": {
    "trusted_cases": 0,
    "candidate_cases": 0,
    "conflict_cases": 0,
    "rejected_cases": 0,
    "human_review_queue": 0
  },
  "scenario_coverage": [
    {
      "scenario": "冻结与支付并发",
      "expected_behavior": "只能有一个操作成功",
      "status": "missing",
      "responsible_services": ["order-service", "payment-service"],
      "severity": "high",
      "confidence": "high",
      "knowledge_tier": "trusted",
      "requires_confirmation": false,
      "evidence": [
        {
          "type": "historical",
          "source": "D:/designs/order-freeze-v2.docx",
          "location": "异常场景/并发处理"
        }
      ],
      "not_applicable_reason": null
    }
  ],
  "service_modifications": [
    {
      "service": "payment-service",
      "reason": "支付许可依赖订单状态",
      "status": "historical-candidate",
      "knowledge_tier": "candidate",
      "requires_confirmation": true,
      "modifications": ["支付前检查冻结状态"],
      "compatibility": ["兼容订单服务灰度"],
      "observability": ["记录冻结支付拒绝次数"],
      "tests": ["冻结与支付并发"],
      "evidence": []
    }
  ],
  "cross_service_review": {
    "source_of_truth": "order-service",
    "consistency_model": "",
    "failure_matrix": [],
    "publish_order": [],
    "rollback": [],
    "open_items": []
  },
  "findings": [
    {
      "title": "自动关单未适配冻结状态",
      "severity": "high",
      "confidence": "high",
      "status": "missing",
      "consequence": "冻结订单可能被错误关闭",
      "recommendation": "定义并实现关单任务过滤策略",
      "evidence": []
    }
  ],
  "knowledge_conflicts": [
    {
      "topic": "冻结期间是否允许取消",
      "claims": [
        {
          "value": "允许",
          "source": "人工冻结设计"
        },
        {
          "value": "禁止",
          "source": "风控冻结设计"
        }
      ],
      "resolution_status": "unresolved",
      "required_decision": "当前冻结类型和取消规则"
    }
  ],
  "open_questions": [],
  "evidence_trace": []
}
```

## Allowed values

Scenario and finding status:

- `covered`
- `partial`
- `missing`
- `conflict`
- `unverified`
- `not-applicable`

Severity:

- `critical`
- `high`
- `medium`
- `low`

Confidence:

- `high`
- `medium`
- `low`

Knowledge tier:

- `trusted`
- `candidate`
- `conflict`
- `heuristic`

## Completion rules

- Every scenario must have a status.
- Every scenario and service modification must have a knowledge tier.
- Every applicable scenario must name at least one responsible service.
- Every `not-applicable` scenario must contain a reason.
- Every high or critical missing/conflict finding must contain evidence.
- Candidate or heuristic evidence cannot support a high-confidence missing/conflict conclusion by itself.
- Every non-trusted scenario or service must set `requires_confirmation` to true.
- Every knowledge conflict must preserve at least two claims and their sources.
- Every service must contain a reason, modification list, test list, and evidence field.
- Keep unknown information in `open_questions`; do not silently fill it.
