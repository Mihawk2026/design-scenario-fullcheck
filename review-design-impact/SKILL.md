---
name: review-design-impact
description: Analyze local historical design documents without RAG to find missing business scenarios, review design completeness, infer historically associated impacts, and decompose a change into per-microservice modification obligations. Use when designing a requirement, checking an existing system design, estimating affected scenarios or services, compiling local design history into structured change cases, or validating that a cross-service solution is complete.
---

# Review design impact

Treat local historical designs as a complete corpus, not a Top-K retrieval source. Compile every design into structured change cases, scan every compiled case against the current change, and use CodeGraph only to locate or verify technical implementation points.

Do not infer that a component is unaffected merely because CodeGraph contains no dependency edge. Business association, historical co-change, shared state, shared rules, operations, reporting, and compatibility are independent impact evidence.

## Select the operating mode

- **Prepare history**: inventory local documents, extract one or more change cases from every document, and compile them into SQLite.
- **Design-time check**: build an impact baseline before or while producing a new design.
- **Post-design review**: compare a completed design with the expected impact baseline.
- **Service decomposition**: turn scenario obligations into explicit changes for each microservice.
- **Incremental update**: reprocess documents whose hashes or version chains changed.

## Prepare the local corpus

1. Run `scripts/inventory_documents.py` over every configured document root.
2. Compare the manifest with the prior manifest. Analyze new and changed files; retain unchanged extracted cases.
3. For every document, extract one or more JSON change cases following [references/change-case-schema.md](references/change-case-schema.md).
4. Preserve source path, version, section or page, and whether each fact is explicit or inferred.
5. Link versions with `supersedes`. Treat additions in a later version as review candidates, not automatically as omissions.
6. Normalize business-object, capability, action, state, service, and asset aliases before compilation.
7. Run `scripts/compile_history.py` to rebuild the local SQLite database from all extracted cases.

Do not summarize a document into prose and discard its service changes or scenarios. Represent one historical design as a hyperedge connecting its business change, scenarios, services, assets, and known omissions.

## Execute a design impact review

Follow this order. Do not begin with file-level code changes.

### 1. Build the change specification

Extract the current requirement or design according to [references/change-spec-schema.md](references/change-spec-schema.md). Require:

- before behavior and after behavior;
- business objects, capabilities, actions, states, actors, and triggers;
- changed rules and invariants that must remain true;
- non-goals, compatibility constraints, and unresolved assumptions.

If before or after behavior is unknown, mark it unresolved and lower confidence. Do not silently invent it.

### 2. Classify the change

Assign all applicable change types, including state, operation, permission, business rule, data meaning, API or event contract, ordering, consistency, lifecycle, async workflow, dependency, migration, or service-ownership changes.

### 3. Scan the entire compiled history

Run `scripts/analyze_impact.py`. It must inspect all compiled cases and report the total case count, matched case count, every match reason, historical scenarios, service changes, and omissions.

Match independently on:

- business object;
- business capability;
- action or state;
- change type;
- invariant or business rule when normalized mappings exist;
- historical service co-change.

Never truncate evidence to a similarity Top-K. Use normalized aliases to address terminology differences.

### 4. Expand scenario obligations

Read [references/scenario-rules.md](references/scenario-rules.md). Form the union of:

- scenarios explicitly present in matched historical cases;
- scenarios added by later design versions or known omissions;
- general scenario dimensions applicable to the current change;
- business relationships that are not represented by code dependencies;
- current CodeGraph readers, writers, producers, consumers, jobs, and entry points.

Prune a scenario only with an explicit non-applicability reason.

### 5. Build the expected impact baseline

For each scenario record:

- trigger and precondition;
- expected business behavior or unresolved decision;
- responsible business domain;
- candidate services and asset types;
- severity and confidence separately;
- historical, business-relationship, code, or heuristic evidence.

Label heuristic-only findings `unverified`. Evidence absence is not proof of non-impact.

### 6. Compare the design with the baseline

Classify every obligation as:

- `covered`;
- `partial`;
- `missing`;
- `conflict`;
- `unverified`;
- `not-applicable`, with a reason.

Do not use an overall score as proof of completeness. Show the underlying uncovered obligations.

### 7. Decompose by microservice

Read [references/service-decomposition.md](references/service-decomposition.md). For every candidate service, state why it participates and check domain logic, state transitions, API, events, storage, jobs, cache or index, permissions, operations, compatibility, rollout, observability, recovery, and tests.

Also define cross-service truth ownership, consistency, failure behavior, compensation, publish order, version skew, data migration, and rollback.

### 8. Ground with CodeGraph

Use CodeGraph after the business-impact baseline exists. Locate concrete implementations, verify current readers and consumers, and detect conflicts with the proposed design. Keep historically or semantically supported candidates even if CodeGraph finds no direct path; mark them for confirmation.

### 9. Run reverse omission checks

Before completing the review, answer:

1. Which services or asset types appeared in comparable historical changes but not in the current design?
2. Which alternate entry points act on each changed business object?
3. Who reads every new or changed state, field, event value, and business meaning?
4. What final state results when every cross-service step fails, times out, repeats, or arrives out of order?
5. Can old and new versions coexist, and can code and data both be rolled back?
6. Are manual operation, compensation, observability, analytics, and audit covered?

### 10. Validate the output

Format the machine-readable result according to [references/report-schema.md](references/report-schema.md), then run `scripts/validate_review.py`.

Do not claim completion while validation errors remain. Present unknowns as explicit questions rather than filling them with assumptions.

## Required human-readable output

Lead with high-risk missing or conflicting obligations. Then provide:

1. ChangeSpec summary.
2. Scenario coverage matrix.
3. Historical case and version-difference evidence.
4. Per-microservice modification matrix.
5. Cross-service consistency and release review.
6. Open questions and non-applicability decisions.
7. Traceable local document and CodeGraph evidence.

## Evidence rules

- **Historical**: explicitly stated in a local historical design or version difference.
- **Business relation**: supported by a curated relation or repeated historical co-change.
- **Code**: supported by current CodeGraph evidence.
- **Heuristic**: derived only from a general scenario rule.

Keep severity independent from confidence. A rare but catastrophic scenario may have high severity and low confidence.
