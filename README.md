# Design Scenario Full Check

`review-design-impact` 是一个面向 AI 辅助系统设计的 SKILL。它直接分析本地全部历史设计文档，将设计中的业务变化、场景、微服务和修改点编译为本地结构化知识库，然后检查新设计是否遗漏业务场景或跨服务修改。

它不使用向量检索、文档切片召回或 Top-K RAG。每次影响分析都会遍历全部已经结构化的历史案例。CodeGraph 只负责最后的代码定位和技术验证。

## 解决的问题

- AI 只看到代码依赖，遗漏业务上有关联、代码上无调用关系的系统。
- AI 能生成主流程，但容易漏掉并发、异常、兼容、运营、报表或回滚场景。
- 一个需求需要拆给多个微服务，但修改点分散且缺少统一检查。
- 历史设计很多，但整篇文档直接塞入上下文成本高、结果不稳定。

## 工作原理

```text
本地历史设计文档
  → 全量清单和版本识别
  → AI逐份抽取结构化 ChangeCase
  → compile_history.py 编译 SQLite
  → 新需求生成 ChangeSpec
  → analyze_impact.py 全库扫描
  → 场景覆盖和微服务修改检查
  → CodeGraph 定位验证
  → validate_review.py 完整性门禁
```

历史设计形成“变更超边”：同一历史需求涉及的业务对象、状态、场景、服务、API、消息、任务、后台、报表和历史遗漏会被保存在同一个 ChangeCase 中。即使这些服务在代码上没有依赖，也能通过历史共同变更关系被发现。

## 目录

```text
review-design-impact/
├─ SKILL.md
├─ agents/openai.yaml
├─ references/
│  ├─ change-case-schema.md
│  ├─ change-spec-schema.md
│  ├─ scenario-rules.md
│  ├─ service-decomposition.md
│  └─ report-schema.md
└─ scripts/
   ├─ inventory_documents.py
   ├─ compile_history.py
   ├─ analyze_impact.py
   └─ validate_review.py
```

## 安装 SKILL

将 `review-design-impact` 文件夹复制到 Codex skills 目录：

```powershell
Copy-Item -Recurse -Force .\review-design-impact "$env:CODEX_HOME\skills\review-design-impact"
```

如果没有设置 `CODEX_HOME`，通常使用：

```powershell
Copy-Item -Recurse -Force .\review-design-impact "$HOME\.codex\skills\review-design-impact"
```

重新加载 Codex 后，可显式调用：

```text
使用 $review-design-impact 检查这份设计遗漏了哪些业务场景，并按微服务拆分修改点。
```

也可以直接提出下列需求触发它：

- “设计订单冻结功能，先分析完整影响范围。”
- “检查这份设计有没有遗漏场景。”
- “这个需求需要哪些微服务配合修改？”
- “用全部历史设计检查当前方案。”

## 准备本地历史文档

### 1. 生成全量文档清单

```powershell
python .\review-design-impact\scripts\inventory_documents.py `
  --root D:\designs\order `
  --root D:\designs\payment `
  --output D:\design-impact\manifest.json
```

默认记录 Markdown、文本、HTML、JSON、YAML、Word 和 PDF 文件的路径、大小、修改时间、SHA-256 和可能的版本号。该脚本只负责盘点，不把二进制文档内容粗暴转换为文本。

再次执行时增加旧清单，可以得到新增、变化、未变化和删除文件：

```powershell
python .\review-design-impact\scripts\inventory_documents.py `
  --root D:\designs `
  --previous D:\design-impact\manifest.json `
  --output D:\design-impact\manifest-new.json
```

### 2. 逐份抽取 ChangeCase

让 AI 按 `references/change-case-schema.md` 分析清单中的每份设计，将结果保存到一个案例目录：

```text
D:\design-impact\cases\
├─ order-freeze-v1.json
├─ order-freeze-v2.json
└─ refund-rule-change.json
```

重要要求：

- 一份文档可以包含多个 ChangeCase。
- 保留原文件、版本、章节或页码证据。
- 区分原文明示内容和 AI 推断内容。
- 用 `supersedes` 关联版本。
- 将V2比V1新增的内容标为“版本补充候选”，不要自动认定为遗漏。
- 统一服务别名和业务术语，例如 `订单中心`、`order-svc`、`order-service`。

### 3. 编译本地 SQLite

```powershell
python .\review-design-impact\scripts\compile_history.py `
  --cases D:\design-impact\cases `
  --output D:\design-impact\history.db
```

编译器会校验案例结构，并生成：

- 历史案例表。
- 业务对象、能力、动作、状态和变化类型索引。
- 场景表。
- 微服务修改点表。
- 历史遗漏表。
- 服务共同变更统计。

只要任何案例不合法，编译默认失败，避免不完整知识悄悄进入正式数据库。

## 分析一个新需求

先让 AI 根据 `references/change-spec-schema.md` 生成 `change-spec.json`：

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
  "non_goals": [],
  "unknowns": []
}
```

运行全库影响分析：

```powershell
python .\review-design-impact\scripts\analyze_impact.py `
  --db D:\design-impact\history.db `
  --change-spec D:\design-impact\change-spec.json `
  --output D:\design-impact\impact.json
```

可选的别名文件为普通 JSON：

```json
{
  "order-svc": "order-service",
  "订单中心": "order-service",
  "订单单据": "订单"
}
```

```powershell
python .\review-design-impact\scripts\analyze_impact.py `
  --db D:\design-impact\history.db `
  --change-spec D:\design-impact\change-spec.json `
  --aliases D:\design-impact\aliases.json `
  --output D:\design-impact\impact.json
```

输出包含总案例数、全部匹配案例及原因、历史场景、候选服务、历史修改点、服务共同变更和历史遗漏。脚本不使用相似度，也没有 Top-K 参数。

## 检查设计并拆分微服务

将当前设计和 `impact.json` 一起交给 `$review-design-impact`。SKILL会输出：

1. ChangeSpec摘要。
2. 场景覆盖矩阵。
3. 历史案例和版本差异证据。
4. 每个微服务的参与原因及修改点。
5. 跨服务一致性、发布顺序和回滚检查。
6. 缺失、部分覆盖、冲突和待确认项。
7. 本地文档及CodeGraph证据链。

每个场景只能使用以下状态：

- `covered`
- `partial`
- `missing`
- `conflict`
- `unverified`
- `not-applicable`

完成后保存机器可读结果并执行：

```powershell
python .\review-design-impact\scripts\validate_review.py `
  --input D:\design-impact\review.json
```

校验失败时不能宣称设计检查完成。

## CodeGraph接入原则

本项目不绑定某个具体 CodeGraph 产品。接入时需要能够查询：

- 服务、API、表、字段、状态和事件对应的代码节点。
- 字段和状态的读写方。
- API调用方和消息消费者。
- 定时任务、异步任务和配置入口。

调用顺序必须是：

```text
历史业务影响分析 → 候选场景和服务 → CodeGraph定位验证
```

不能用 CodeGraph 查不到依赖作为删除候选服务的理由。

## 增量更新

历史文档变化后：

1. 重新生成 manifest，并与旧 manifest 比较。
2. 只重新抽取 `added` 和 `changed` 文档。
3. 删除或停用 `removed` 文档对应的案例。
4. 使用全部案例重新执行 `compile_history.py`。
5. 运行历史回放测试。

SQLite是可重新生成产物，建议不要作为唯一知识源；原始设计文档和结构化案例JSON才是事实来源。

## 推荐验收方法

选择一批有V1、V2或后续事故记录的历史设计：

1. 只输入当时的V1设计。
2. 不向检查过程泄露V2和事故结论。
3. 使用其他历史案例和通用规则执行检查。
4. 验证是否命中V2补充或事故暴露的真实遗漏。

重点统计严重遗漏召回率、真实遗漏命中率、影响服务召回率、误报率和人工评审耗时。不要用报告长度或模型自评作为效果指标。

## 当前边界

- 文档内容抽取由AI或已有文档解析能力完成，确定性脚本不负责理解Word/PDF业务语义。
- 术语不统一会降低精确匹配效果，应维护别名和规范名称。
- 历史共同出现表示“需要检查”，不自动表示“本次必须修改”。
- 仅由通用规则推导且没有历史、业务关系或代码证据的结论必须标为 `unverified`。
