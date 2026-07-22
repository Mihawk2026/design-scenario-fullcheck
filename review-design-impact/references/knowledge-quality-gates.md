# Historical knowledge quality gates

Historical documents are evidence written by different authors at different times. Never treat extracted text as a single authoritative truth.

## Three layers

Maintain three separate layers:

1. **Raw evidence**: original document, hash, version, page or section, and exact local meaning.
2. **Document cases**: one document-local ChangeCase; do not fill its omissions from other documents.
3. **Compiled knowledge**: verified, normalized, versioned, and conflict-aware patterns used for design review.

Never overwrite raw terms when creating canonical terms.

## Two independent passes

### Pass 1: faithful extraction

- Read only the target document.
- Extract what the document explicitly states.
- Mark missing behavior as not mentioned.
- Mark any necessary inference as `inferred`.
- Preserve original business-object, action, state, and service names.
- Record a behavior signature instead of merging aliases immediately.

### Pass 2: source verification

- Reread the original source, not only the extracted JSON.
- Verify every behavior, scenario, service change, relation, and evidence location.
- Reject statements not supported by the source.
- Add fields the first pass omitted only when the source supports them.
- Record contradictions and unclear language.
- Assign validation status and confidence.

When an independent execution context is available, use it for pass 2 without exposing the first pass's intended conclusions. Otherwise perform a separate source-first pass before viewing the extracted claims.

## Behavior signature

Compare concepts using:

```text
business object
+ precondition and previous state
+ trigger and actor
+ operation and target state
+ allowed behavior
+ forbidden behavior
+ recovery behavior
+ downstream effects
+ applicability scope
```

Do not merge concepts only because their names are similar. Do not keep concepts separate only because their names differ.

## Validation statuses and knowledge tiers

| Validation status | Knowledge tier | Meaning |
|---|---|---|
| `validated` | `trusted` | Independent source verification passed |
| `partial` | `candidate` | Some claims are verified; gaps remain |
| `unverified` | `candidate` | Extracted but not independently verified |
| `conflict` | `conflict` | Source or cross-source conclusions disagree |
| `rejected` | `rejected` | Unsupported, obsolete, duplicate, or unusable |

Only trusted knowledge can directly support a high-confidence missing or conflicting design finding. Candidate knowledge generates a confirmation question. Conflict knowledge must show every side and its source.

## Evidence requirements

Every scenario, service change, relation, omission, and conflict must contain:

- evidence kind: `explicit`, `inferred`, `version-diff`, `review-comment`, `defect`, or `incident`;
- source location: section, page, paragraph, line, or another stable locator;
- applicability conditions when the behavior is not universal.

Do not promote an inferred-only claim to trusted knowledge.

## Version handling

- Link revisions with `supersedes`.
- Prefer the final known effective version for current rules.
- Preserve earlier versions to identify possible review additions.
- Do not count multiple revisions of one design as independent supporting cases.
- Distinguish proposed, approved, implemented, and still-effective states when evidence exists.

## Conflict handling

Preserve conflicts rather than choosing by frequency. Record:

- topic;
- competing claims;
- supporting case and evidence for every claim;
- possible scope, role, version, or business-line differences;
- resolution status and required owner decision.

## Human review queue

Send only high-value items for human review:

- high-severity candidate knowledge;
- conflicting business rules;
- uncertain alias merges or splits;
- unclear final version;
- inferred claims proposed for promotion;
- historical design that conflicts with current CodeGraph behavior.

Do not require people to review every clear, explicitly evidenced case.
