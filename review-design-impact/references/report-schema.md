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
  "scenario_coverage": [
    {
      "scenario": "冻结与支付并发",
      "expected_behavior": "只能有一个操作成功",
      "status": "missing",
      "responsible_services": ["order-service", "payment-service"],
      "severity": "high",
      "confidence": "high",
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

## Completion rules

- Every scenario must have a status.
- Every applicable scenario must name at least one responsible service.
- Every `not-applicable` scenario must contain a reason.
- Every high or critical missing/conflict finding must contain evidence.
- Every service must contain a reason, modification list, test list, and evidence field.
- Keep unknown information in `open_questions`; do not silently fill it.
