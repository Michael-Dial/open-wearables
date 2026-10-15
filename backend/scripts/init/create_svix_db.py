#!/usr/bin/env python3
"""Ensure the 'svix' database exists, creating it if necessary.

Runs before migrations so that svix-server can connect on first deploy.
Uses autocommit because CREATE DATABASE cannot run inside a transaction.
"""

import logging

import psycopg
import psycopg.errors

from app.config import settings

logger = logging.getLogger(__name__)


def create_svix_db() -> None:
    if not settings.outgoing_webhooks_enabled:
        logger.info("Outgoing webhooks disabled — skipping svix database creation.")
        return
    try:
        # Keyword arguments, not a "key=value" string: psycopg quotes them, so a
        # password with spaces or quotes still connects.
        with psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password.get_secret_value(),
            autocommit=True,
        ) as conn:
            try:
                conn.execute("CREATE DATABASE svix")
                logger.info("Created 'svix' database.")
            except psycopg.errors.DuplicateDatabase:
                logger.info("Svix database already exists, skipping.")
    except Exception:
        # Best-effort: never block app boot (e.g. managed Postgres without CREATEDB).
        # A real connectivity problem still surfaces at the next alembic step.
        logger.warning("Could not ensure 'svix' database - continuing without it.", exc_info=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s - %(name)s] (%(levelname)s) %(message)s")
    create_svix_db()
