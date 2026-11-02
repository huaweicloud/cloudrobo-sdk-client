"""Explicit local receipts for supervisor tests that do not start a publisher."""

from cloudrobo_ingestion.progress import PublishReceipt


def acknowledge_round(supervisor):
    batch_id = supervisor.outbox_id
    payload = supervisor.reporter.slots[batch_id][0]
    supervisor.reporter.acks.put(
        PublishReceipt(batch_id, payload, supervisor.outbox_token, supervisor.clock(), 0)
    )
    supervisor.drain()


def complete_result(supervisor, batch_id):
    for _ in range(3):
        if supervisor.store.get(batch_id)["report_count"] == 3:
            break
        supervisor.store.update(batch_id, report_next_ms=0)
        supervisor.queue_final(supervisor.store.get(batch_id))
        if supervisor.outbox_id != batch_id:
            raise RuntimeError(f"Expected outbox batch {batch_id}, got {supervisor.outbox_id}")
        acknowledge_round(supervisor)
