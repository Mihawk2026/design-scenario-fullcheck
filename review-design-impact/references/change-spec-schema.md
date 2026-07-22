# ChangeSpec schema

Create this object before generating implementation changes or reviewing a design.

```json
{
  "title": "订单增加冻结能力",
  "before_behaviors": ["待支付订单可以支付或自动关闭"],
  "after_behaviors": ["待支付订单可以被冻结"],
  "business_objects": ["订单"],
  "capabilities": ["订单生命周期管理"],
  "change_types": ["新增状态", "新增操作"],
  "actions": ["冻结", "解冻"],
  "states": ["FROZEN"],
  "actors": ["客服", "风控系统"],
  "triggers": ["人工操作", "风控事件"],
  "changed_rules": ["冻结期间禁止支付"],
  "invariants": ["订单不能同时支付成功和冻结成功"],
  "data_or_contract_changes": [],
  "compatibility_constraints": [],
  "non_goals": [],
  "unknowns": []
}
```

## Rules

- Describe observable behavior, not only implementation tasks.
- Require at least one before behavior and one after behavior. If unknown, add an explicit `unknowns` item rather than guessing.
- Use all applicable change types; do not force one primary type.
- Treat permissions, data meaning, rollout, or ownership changes as first-class changes.
- State invariants that must remain true during concurrency, partial failure, and version skew.
- Record non-goals to support justified `not-applicable` decisions.

## Recommended change types

- `新增状态`
- `修改状态流转`
- `新增操作`
- `修改业务规则`
- `修改权限`
- `修改数据口径`
- `修改API协议`
- `修改消息协议`
- `修改流程时序`
- `修改一致性要求`
- `修改生命周期`
- `新增异步流程`
- `新增外部依赖`
- `数据迁移`
- `服务职责迁移`
