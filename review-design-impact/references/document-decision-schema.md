# Document classification decisions

Discovery intentionally includes supported documents whose path has no design keyword. Before semantic extraction, classify each pending document as `design` or `non-design`. Save only `non-design` decisions to `.design-impact/document-decisions.json`; design documents are represented by their ChangeCases.

```json
{
  "schema_version": 1,
  "documents": [
    {
      "path": "D:/workspace/config/application.yaml",
      "sha256": "document-sha256",
      "classification": "non-design",
      "reason": "runtime configuration with no requirement, behavior, architecture, or change rationale",
      "reviewed_at": "2026-07-22T10:00:00+08:00",
      "reviewer": "codex-review-design-impact"
    }
  ]
}
```

The path and SHA-256 must match the active manifest. A changed or moved file is reconsidered unless its decision is migrated after a unique hash move. Never mark a document `non-design` solely because its filename is generic. Read enough content to establish that it contains no historical requirement, business behavior, architecture, interface/event/data decision, operational rule, defect, incident, or change rationale.
