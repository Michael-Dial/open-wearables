#!/usr/bin/env python3
"""Re-pull the Whoop history each account already holds, so its stored nights gain their respiratory rate.

Whoop states a respiratory rate on every scored sleep. It used to be kept only inside the sleep score's
components, which no summary reads, so every stored Whoop night reaches the sleep summary without one. Sleeps
synced from now on file it as Whoop's figure for the sleep (Whoop247Data._save_sleep_scalars).

The stored components are not copied across instead: Whoop sleep scores carry no data source, so on a profile
with two Whoop accounts (a 4.0 and a 5.0 worn together) a score cannot be told apart from the other strap's at
the same wake time. A re-pull runs each account's sleeps through the live path under that account's own
connection, so every figure is filed under the strap that measured it. Re-saving a sleep is idempotent
(matched by Whoop's sleep id; samples are unique per source, series and instant).

One historical sync per active Whoop connection that holds sleep, from the start of its earliest stored sleep
(a day earlier, for the time zone), queued on Celery so startup does not wait. Historical, so the live-sync
cursor is not moved. Runs once per instance (data_migration); a connection whose re-pull fails is retried by
hand with POST /api/v1/providers/whoop/users/{user_id}/sync/historical?connection_id=...&days=...

Usage (inside Docker):
    docker compose exec app uv run python scripts/data_migrations/resync_whoop_sleep_respiratory_rate.py --dry-run
    docker compose exec app uv run python scripts/data_migrations/resync_whoop_sleep_respiratory_rate.py
"""

import argparse
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import engine
from app.integrations.celery.core import create_celery
from app.services.data_migration_service import data_migration_service

MIGRATION = "resync_whoop_sleep_respiratory_rate"

# Each active Whoop connection that holds sleep, with the start of its earliest stored sleep.
_CONNECTIONS = text("""
    SELECT uc.id AS connection_id, uc.user_id, min(er.start_datetime) AS first_sleep
    FROM user_connection uc
    JOIN data_source ds ON ds.user_connection_id = uc.id
    JOIN event_record er ON er.data_source_id = ds.id AND er.category = 'sleep'
    WHERE uc.provider = 'whoop' AND uc.status = 'active'
    GROUP BY uc.id, uc.user_id
    ORDER BY uc.user_id, uc.id
""")


def whoop_resyncs(db: Session) -> list[tuple[UUID, UUID, datetime]]:
    """(connection, user, start) for every re-pull this migration queues; writes nothing."""
    return [(row.connection_id, row.user_id, row.first_sleep - timedelta(days=1)) for row in db.execute(_CONNECTIONS)]


def queue_resyncs(db: Session, *, dry_run: bool) -> bool:
    plan = whoop_resyncs(db)
    for connection_id, user_id, start in plan:
        print(f"  user={user_id} connection={connection_id} from {start.date().isoformat()}")
    if dry_run:
        print(f"Would queue {len(plan)} Whoop history re-pull(s).\n\nDry run: no changes made.")
        return False
    if plan:
        # Imported here: the task module pulls in the provider stack, which a dry run does not need.
        from app.integrations.celery.tasks.sync_vendor_data_task import sync_vendor_data

        for connection_id, user_id, start in plan:
            sync_vendor_data.delay(
                user_id=str(user_id),
                start_date=start.isoformat(),
                providers=["whoop"],
                is_historical=True,
                connection_ids=[str(connection_id)],
            )
    print(f"{MIGRATION}: queued {len(plan)} Whoop history re-pull(s).")
    return True


def main(dry_run: bool) -> None:
    # Outside the app, the Celery app is not configured yet: without this .delay() goes to the default broker.
    create_celery()
    with engine.connect() as connection, Session(bind=connection) as db:
        data_migration_service.run(db, MIGRATION, lambda: queue_resyncs(db, dry_run=dry_run), dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="List the re-pulls without queueing them")
    main(dry_run=parser.parse_args().dry_run)
