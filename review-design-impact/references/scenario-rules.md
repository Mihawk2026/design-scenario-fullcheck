# Scenario expansion rules

Apply only relevant dimensions, but require an explicit reason to prune a historically supported scenario.

## Actor and entry-point coverage

- End user, administrator, customer service, operations, scheduled job, message consumer, external system, and batch process.
- Public API, internal API, admin UI, import, retry tool, backfill, event, and timer.
- Alternate or legacy entry points that bypass the new main flow.

## Lifecycle and state coverage

- Create, update, execute, complete, cancel, expire, delete, restore, archive, and migrate.
- Legal transition, illegal transition, repeated transition, intermediate state, recovery state, and terminal state.
- Behavior of all existing actions in every new or changed state.
- Preservation of the previous state when an operation is reversible.

## Timing and concurrency coverage

- Duplicate, concurrent, reordered, delayed, timed-out, and retried requests.
- Operation concurrent with cancellation, expiration, payment, refund, or scheduled processing.
- Request success with response loss.
- Long-running work crossing a state or version change.

## Failure and consistency coverage

- Failure before any durable write.
- Local success and downstream failure.
- Downstream success and local timeout.
- Message publish failure, duplicate delivery, out-of-order delivery, and consumer restart.
- Compensation failure and repeated compensation.
- Cache, index, replica, or analytical data lag.

## Data coverage

- Null, missing, duplicate, dirty, historical, partially migrated, oversized, and boundary data.
- Existing rows that do not contain a new field or enum value.
- Referential integrity and uniqueness under concurrency.
- Changed business meaning without physical schema change.

## Contract and compatibility coverage

- Old producer with new consumer and new producer with old consumer.
- Unknown enum values and optional fields.
- API, event, database, cache, search index, export, and analytics consumers.
- Client or service rollback after new data has been written.

## Permission and audit coverage

- Authentication, authorization, role boundaries, tenant isolation, and ownership.
- Bulk operations, elevated access, break-glass access, and audit trail.
- Sensitive data exposure through logs, messages, exports, or admin tools.

## Release and operations coverage

- Feature flag, gray release, publish order, version skew, rollback, and data rollback.
- Backfill, reconciliation, manual compensation, replay, and emergency disable switch.
- Capacity, throttling, queue accumulation, dependency saturation, and cost.

## Observability and verification coverage

- Success, rejection, failure, latency, backlog, long-lived state, and compensation metrics.
- Correlation identifiers, structured logs, tracing, alerts, dashboards, and audit reports.
- Unit, integration, contract, end-to-end, concurrency, migration, rollback, and fault-injection tests.

## Reverse questions

Ask these before completing any review:

1. What reads the changed state, field, event, or business meaning?
2. What acts automatically without a user request?
3. What uses the data outside the online call chain?
4. What happens after partial success?
5. What happens during mixed-version operation?
6. How does an operator detect, stop, and recover the failure?
