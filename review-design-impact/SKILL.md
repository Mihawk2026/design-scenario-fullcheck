---
name: review-design-impact
description: Automatically initialize or incrementally update a local historical-design knowledge base and an offline CodeGraph fact snapshot without RAG, find missing business scenarios, review design completeness, infer historically associated impacts, and decompose a change into per-microservice modification obligations. Use when a user naturally asks to initialize historical designs, refresh new documents or code revisions, design a requirement, check a design for omissions, estimate affected scenarios or services, or split a change across microservices. Perform discovery, history preparation, CodeGraph snapshot preparation, full-corpus analysis, and validation internally; do not require the user to run scripts or prepare structured data.
---

# Review design impact

Provide a natural-language-only interface. The user describes a requirement or asks to check a design; perform every preparation and analysis step internally.

Never ask the user to run a script, build a database, create JSON, configure RAG, or manually select historical cases. Ask only when no local design corpus can be found or an unresolved business choice would materially change the result.

Treat local historical designs as a complete corpus, not a Top-K retrieval source. Access CodeGraph only while initializing or incrementally updating generated state. During design, review, and decomposition, use only the compiled offline code-fact snapshot. Absence of a code fact is not proof of no business impact.

## Interpret the request

Infer one or more modes from natural language:

- **Initialize history**: analyze all historical documents and build first-use local state.
- **Incremental update**: detect added, changed, moved, or removed documents and refresh affected cases.
- **Design**: analyze impact first, then produce or improve the design.
- **Review**: check an existing design for missing or conflicting obligations.
- **Decompose**: split the change into per-microservice modification contracts.

Accept prompts such as:

- "Design an order-freeze capability and check the complete impact."
- "Check this design for missing scenarios."
- "Which microservices need changes for this requirement?"
- "Use the historical designs in this directory to review the current solution."
- "Initialize the historical designs in this directory."
- "Incrementally update the history after I added new design documents."

Do not make the user choose a mode when the intent is clear.

## Run the automatic workflow

### 1. Discover inputs and local history

Identify the current requirement or design from the conversation, attachments, or workspace. If the user supplied a historical-document path, use it. Otherwise:

1. Run `scripts/workspace_state.py` from the active workspace.
2. Let it discover design-like Markdown, text, HTML, JSON, YAML, Word, and PDF documents.
3. Prefer directories and names containing design, documentation, architecture, ADR, RFC, solution, change, or requirement terms in the project's language. The bundled discovery script includes common English and Chinese terms.
4. Exclude generated state, dependency, build, vendor, and VCS directories.
5. If no design-like files exist, fall back to other supported documents in the workspace.

When multiple unrelated document roots exist and choosing one would change the business scope, ask one concise path question. Otherwise proceed with the best local scope and state the assumption in the final report.

Store generated state under `<workspace>/.design-impact/`. Never modify source design documents.

Read the `code_snapshot_status` and `code_update_required` fields in `.design-impact/session.json` before analysis. The only phases permitted to query CodeGraph are **Initialize history** and **Incremental update**. If a review request finds a missing, stale, or invalid snapshot and CodeGraph is available, temporarily enter incremental-update mode, refresh the snapshot, then resume the review. Never query live CodeGraph from steps 3 through 8.

### 2. Prepare or refresh history and code facts automatically

Read `.design-impact/session.json` produced by `workspace_state.py`.

Read [references/knowledge-quality-gates.md](references/knowledge-quality-gates.md) before extracting or promoting historical knowledge. Treat historical documents as evidence, not ground truth.

- Reuse unchanged extracted cases.
- Analyze every document in `pending_extraction` without asking the user to preprocess it.
- Use available document or PDF extraction capabilities for binary formats.
- In pass 1, extract one or more document-local ChangeCase objects using [references/change-case-schema.md](references/change-case-schema.md). Do not use other documents to fill omissions in the source document.
- Preserve original terms, source path, SHA-256, version, section or page, and explicit-versus-inferred evidence.
- In pass 2, independently reread the source and verify each object, scenario, service change, relation, and evidence location. Do not validate from the extracted JSON alone.
- Mark each case `validated`, `partial`, `unverified`, `conflict`, or `rejected`; record validation issues and confidence.
- Write cases under `.design-impact/cases/` and checkpoint after each document.
- Link versions with `supersedes`; treat later additions as review candidates, not automatic omissions.
- Normalize terms only after document-local extraction. Preserve original terms and compare behavior signatures before merging aliases.
- Keep contradictory conclusions and their applicability conditions. Never resolve conflicts by majority count alone.

If the initial corpus is large, provide brief progress updates and continue in batches. Do not transfer pipeline operation to the user.

After all pending documents are processed and verified, run `scripts/compile_history.py` internally to rebuild `.design-impact/history.db` and its quality report. SQLite is generated state; extracted cases and original documents remain the evidence sources.

Read [references/code-fact-schema.md](references/code-fact-schema.md). When CodeGraph is available, prepare the code-fact snapshot in this phase only:

- On full initialization, export the business-impact projection for every in-scope repository, including repository path, branch, commit, index time, covered surfaces, uncovered surfaces, services, APIs, events, tables, jobs, entry points, and their relations.
- On incremental update, compare current repository branch and commit with `.design-impact/code-manifest.json`; query CodeGraph only for added or changed repositories and changed graph content, then rebuild the consolidated export.
- Compile the export with `scripts/compile_code_facts.py` into `.design-impact/code-facts.db`, `.design-impact/code-manifest.json`, and `.design-impact/code-coverage.json`.
- Preserve repository, branch, commit, file location, and CodeGraph evidence in every compiled fact. Record partial indexing and unsupported technical surfaces instead of implying full coverage.
- If CodeGraph is unavailable, do not block historical-document initialization. Record the snapshot as unavailable and make that evidence limitation explicit.

When initialization or incremental update is the user's primary request, stop after successful compilation and report document counts, reused and re-extracted counts, failures, trusted/candidate/conflict/rejected case counts, compiled scenario/service counts, the human-review queue, CodeGraph snapshot repositories and commits, snapshot coverage gaps, and the state location. Do not require a design-review request in the same turn.

### 3. Build the current ChangeSpec

Extract the requirement or current design using [references/change-spec-schema.md](references/change-spec-schema.md). Require:

- before and after behavior;
- business objects, capabilities, actions, states, actors, and triggers;
- changed rules and invariants;
- non-goals, compatibility constraints, and unresolved assumptions.

Infer ordinary details from supplied evidence. Put genuine uncertainty in `unknowns`; do not invent decisions. Ask only if an unknown changes the architecture or business outcome materially.

Write the generated specification to `.design-impact/current-change.json` for reproducibility.

### 4. Scan the complete compiled history

Run `scripts/analyze_impact.py` internally against `.design-impact/history.db`. Inspect all compiled cases and retain every match reason. Never truncate results using similarity Top-K.

Match independently on business object, capability, action, state, change type, invariant, rule, and historical service co-change. Use normalized aliases for terminology differences.

Use knowledge tiers in every conclusion:

- `trusted` evidence may support a high-confidence finding.
- `candidate` evidence may suggest a scenario but requires confirmation.
- `conflict` evidence must become an explicit design question with all sides shown.
- `rejected` evidence must not influence design conclusions.

Read `.design-impact/code-facts.db`, `.design-impact/code-manifest.json`, and `.design-impact/code-coverage.json` as immutable inputs for this review. Do not access CodeGraph or inspect live code repositories. Use positive snapshot facts to confirm historical candidates or add technical candidates. Never remove a historically or semantically supported candidate because the snapshot has no corresponding entity or relation.

### 5. Expand expected scenarios

Read [references/scenario-rules.md](references/scenario-rules.md). Form the union of:

- scenarios from all matched historical cases;
- version additions and known historical omissions;
- applicable lifecycle, state, concurrency, failure, compatibility, permission, operations, data, and observability scenarios;
- business associations not represented in code;
- readers, writers, producers, consumers, jobs, and alternate entry points present in the offline code-fact snapshot.

Prune a historically supported scenario only with an explicit non-applicability reason.

### 6. Check coverage and decompose services

Classify every obligation as `covered`, `partial`, `missing`, `conflict`, `unverified`, or `not-applicable` with a reason.

Read [references/service-decomposition.md](references/service-decomposition.md). For each candidate service, create a modification contract covering participation reason, business logic, state, API, events, data, jobs, permissions, compatibility, rollout, observability, recovery, and tests.

Also define cross-service truth ownership, consistency, failure behavior, compensation, version skew, publish order, migration, and rollback.

Use only the offline code-fact snapshot to locate concrete implementation points. Keep historically or semantically supported candidates when no matching snapshot fact is found; mark them for confirmation and distinguish `not-found-in-snapshot` from `proven-not-applicable`.

### 7. Challenge the result

Before completion, answer:

1. Which services and asset types appeared in comparable history but not in the current design?
2. Which alternate entry points act on every changed object?
3. Who reads every new or changed state, field, event value, and business meaning?
4. What final state results when each cross-service step fails, times out, repeats, or reorders?
5. Can old and new versions coexist, and can code and data both roll back?
6. Are manual operations, compensation, observability, analytics, and audit covered?

### 8. Validate and respond

Build the machine-readable report using [references/report-schema.md](references/report-schema.md), save it under `.design-impact/reports/`, and run `scripts/validate_review.py` internally.

Do not claim completion while validation errors remain. Fix structural errors automatically. Present unresolved business decisions as concise open questions.

## Response contract

Do not expose installation, indexing, SQLite, JSON, scripts, or internal pipeline steps unless the user asks for diagnostics.

Lead with the useful result:

1. High-risk missing or conflicting scenarios.
2. Scenario coverage matrix.
3. Per-microservice modification matrix.
4. Cross-service consistency, release, and rollback issues.
5. Only the open questions that require a human decision.
6. Traceable historical-document evidence and offline code-fact evidence with repository, branch, commit, and coverage status.

Keep severity separate from confidence. Label general-rule-only findings `unverified`. Do not treat a completeness score, repeated wording, or document majority as proof that the design is complete.

## Recovery behavior

- If generated state is absent, initialize it automatically.
- If source hashes changed, refresh only affected cases and rebuild generated aggregates.
- If a case is invalid, repair or re-extract it from the source document.
- If the code snapshot is missing, stale, or invalid and CodeGraph is available, refresh it in incremental-update mode before review.
- If snapshot freshness is unknown because local repository revisions cannot be inspected, do not enter a refresh loop. Use the snapshot with an explicit `unknown` freshness label.
- If CodeGraph is unavailable, complete the business review and identify offline code grounding as unavailable or stale. Never substitute a live repository inspection during review.
- If a document cannot be parsed, report that specific evidence gap without blocking analysis of the rest of the corpus.
