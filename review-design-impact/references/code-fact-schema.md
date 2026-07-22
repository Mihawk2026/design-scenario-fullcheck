# CodeGraph MCP fact snapshot

Treat CodeGraph as an open-source MCP server, not as a database client, REST API, or bulk-export service. Use it only during full initialization or incremental update. Never call it during design review.

## MCP capability rules

- Discover the tools exposed by the current MCP host; host namespaces may change their callable names.
- Prefer `codegraph_explore` when present. It is the only tool exposed by default by `colbymchenry/codegraph` and returns relevant source, call paths, and blast radius.
- Use `codegraph_status`, `codegraph_search`, `codegraph_node`, `codegraph_callers`, `codegraph_callees`, `codegraph_impact`, or `codegraph_files` only when the MCP server already exposes them. Never require the user to enable them.
- Do not replace a missing MCP tool with the CodeGraph CLI, direct reads of `.codegraph/codegraph.db`, grep, or repository crawling.
- Pass `projectPath` when supported so each microservice repository is queried in its own index.
- Treat an inactive server or an absent tool list as `index-unavailable`; do not initialize or delete a CodeGraph index implicitly.
- Capture every staleness or pending-sync banner. Facts from a stale response cannot be marked high-confidence.

## Collection plan

Build query seeds from the verified historical cases before calling MCP:

- service and repository names;
- explicit symbol, class, method, route, topic, table, job, and file names;
- business objects, states, actions, and fields that have a technical anchor;
- source-of-truth services and historically co-changing services.

For each indexed repository, batch related seeds and ask `codegraph_explore` to return observed entry points, readers, writers, callers, callees, call paths, routes, events, jobs, persistence access, and blast radius. Ask for file and line evidence. Follow up only when a response is ambiguous or truncated.

CodeGraph is query-oriented, so do not claim complete repository coverage. Record each seed as `matched`, `not-found`, `ambiguous`, `truncated`, or `not-queried`.

Save exact MCP responses under `.design-impact/codegraph-mcp/raw/`. Create a normalized capture file with this shape before compilation:

```json
{
  "schema_version": 2,
  "generated_at": "2026-07-22T10:00:00+08:00",
  "mcp": {
    "server": "codegraph",
    "implementation": "colbymchenry/codegraph",
    "transport": "mcp",
    "tools_observed": ["codegraph_explore"]
  },
  "repositories": [
    {
      "name": "order-service",
      "path": "D:/repos/order-service",
      "branch": "main",
      "commit": "abc123",
      "indexed_at": "2026-07-22T09:55:00+08:00",
      "coverage": ["order-state", "automatic-close-job"],
      "not_covered": ["external-scheduler"]
    }
  ],
  "mcp_calls": [
    {
      "id": "cg-0001",
      "repository": "order-service",
      "tool": "codegraph_explore",
      "arguments": {
        "projectPath": "D:/repos/order-service",
        "query": "Trace readers and writers of order status and the automatic close flow."
      },
      "response_path": "raw/cg-0001.txt",
      "response_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
      "observed_at": "2026-07-22T09:56:00+08:00",
      "status": "ok",
      "staleness": "fresh"
    }
  ],
  "query_seeds": [
    {
      "repository": "order-service",
      "category": "state",
      "seed": "Order.status / WAIT_PAY",
      "status": "matched",
      "mcp_call_ids": ["cg-0001"],
      "notes": "Reader and writer paths were returned."
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
      "evidence": {"kind": "codegraph-mcp", "mcp_call_id": "cg-0001"}
    }
  ],
  "relations": [
    {
      "source_id": "order-service:CloseExpiredOrderJob",
      "type": "reads-state",
      "target_id": "order-service:Order.status",
      "repository": "order-service",
      "evidence": {"kind": "codegraph-mcp", "mcp_call_id": "cg-0001"}
    }
  ],
  "business_mappings": [
    {
      "business_object": "订单",
      "state": "WAIT_PAY",
      "action": "自动关单",
      "asset_id": "order-service:CloseExpiredOrderJob",
      "confidence": "high",
      "evidence": {"kind": "codegraph-mcp", "mcp_call_id": "cg-0001"}
    }
  ]
}
```

Relations may reference stable external IDs omitted from the captured entity list.

## Compilation and runtime boundary

Run `scripts/compile_code_facts.py` internally to create:

- `code-facts.db`: normalized facts and MCP call provenance;
- `code-manifest.json`: MCP implementation, observed tools, repositories, branches, commits, and call count;
- `code-coverage.json`: queried seeds, uncovered surfaces, call status, and staleness.

During design review, read only these compiled files. Use positive facts to confirm or add technical candidates. Never remove a business candidate because an MCP query or snapshot has no matching relation. Label stale, unknown, ambiguous, truncated, and uncovered evidence explicitly.
