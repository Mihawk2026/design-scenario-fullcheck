# Microservice decomposition

Create a modification contract for every candidate service. A historical association makes a service a review candidate, not an automatic mandatory change.

## Per-service contract

Record:

- service name and business responsibility;
- why the current change may affect it;
- inputs, outputs, state ownership, and upstream/downstream assumptions;
- concrete modification obligations;
- evidence and confidence;
- unresolved decisions and non-applicability reason when excluded.

Check these modification surfaces:

1. Domain rules and validation.
2. State machine and persistence.
3. API requests, responses, error codes, and idempotency.
4. Event production, consumption, ordering, replay, and schema evolution.
5. Database, cache, search index, and analytical storage.
6. Scheduled jobs, async workers, import, export, and backfill.
7. Permissions, tenant isolation, admin UI, and audit.
8. Compatibility, feature flags, publish order, migration, and rollback.
9. Logs, metrics, alerts, reconciliation, and manual recovery.
10. Unit, integration, contract, end-to-end, concurrency, and failure tests.

## Cross-service contract

Define:

- source of truth and the owner of every state transition;
- consistency model and acceptable convergence time;
- end-to-end sequence including asynchronous boundaries;
- failure matrix for each remote call and event;
- retry, deduplication, compensation, and poison-message behavior;
- old/new producer and consumer combinations;
- release order, feature-flag order, data migration, rollback, and roll-forward;
- monitoring owner and manual recovery owner.

## Status guidance

- Use `confirmed` when the current design or a fresh offline code-fact snapshot explicitly supports the change.
- Use `historical-candidate` when comparable historical cases repeatedly changed the service.
- Use `unverified` when supported only by a general rule or weak association.
- Use `not-applicable` only with a business or architectural explanation.
