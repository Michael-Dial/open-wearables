-- Ingestion audit: is every connected source still delivering data?
--
-- Read-only. Run against the production database, e.g.
--   psql "$DATABASE_URL" -f scripts/fork/audit_ingestion.sql
--
-- Three result sets:
--   1. per connection: status, last sync, newest sleep / workout / sample on its sources
--   2. per data source: the same, so a stale device under a healthy connection shows up
--   3. connection-less SDK sources holding nights synced live since 2026-09-18, which
--      the stale-sleep sweep filed off their account before the fix in this commit.
--      Apple XML imports made since then are connection-less by design and also
--      appear here; an import's nights usually span far further back than 2026-09-18.

\echo '== 1. Connections: newest data per connection (stalest first) =='
WITH ds_latest AS (
    SELECT
        ds.id AS data_source_id,
        ds.user_connection_id,
        (SELECT max(er.end_datetime) FROM event_record er
          WHERE er.data_source_id = ds.id AND er.category = 'sleep') AS last_sleep,
        (SELECT max(er.end_datetime) FROM event_record er
          WHERE er.data_source_id = ds.id AND er.category = 'workout') AS last_workout,
        (SELECT max(dps.recorded_at) FROM data_point_series dps
          WHERE dps.data_source_id = ds.id) AS last_sample
    FROM data_source ds
)
SELECT
    uc.user_id,
    uc.provider,
    uc.status,
    uc.last_synced_at,
    max(l.last_sleep) AS last_sleep,
    max(l.last_workout) AS last_workout,
    max(l.last_sample) AS last_sample,
    round(extract(epoch FROM now() - greatest(max(l.last_sleep), max(l.last_workout), max(l.last_sample))) / 86400, 1)
        AS days_since_any_data
FROM user_connection uc
LEFT JOIN ds_latest l ON l.user_connection_id = uc.id
GROUP BY uc.id, uc.user_id, uc.provider, uc.status, uc.last_synced_at
ORDER BY days_since_any_data DESC NULLS FIRST, uc.provider;

\echo '== 2. Data sources: newest data per source (stalest first) =='
SELECT
    ds.user_id,
    ds.provider,
    ds.user_connection_id,
    ds.device_model,
    ds.source,
    ds.original_source_name,
    (SELECT max(er.end_datetime) FROM event_record er
      WHERE er.data_source_id = ds.id AND er.category = 'sleep') AS last_sleep,
    (SELECT max(er.end_datetime) FROM event_record er
      WHERE er.data_source_id = ds.id AND er.category = 'workout') AS last_workout,
    (SELECT max(dps.recorded_at) FROM data_point_series dps
      WHERE dps.data_source_id = ds.id) AS last_sample
FROM data_source ds
ORDER BY greatest(
    (SELECT max(er.end_datetime) FROM event_record er WHERE er.data_source_id = ds.id),
    (SELECT max(dps.recorded_at) FROM data_point_series dps WHERE dps.data_source_id = ds.id)
) ASC NULLS FIRST, ds.provider;

\echo '== 3. Live nights filed off their account (connection-less SDK sources) =='
SELECT
    ds.user_id,
    ds.provider,
    ds.device_model,
    ds.source,
    ds.id AS orphan_data_source_id,
    (SELECT uc.id FROM user_connection uc
      WHERE uc.user_id = ds.user_id AND uc.provider = ds.provider::text AND uc.status = 'active'
      ORDER BY uc.created_at LIMIT 1) AS expected_connection_id,
    count(er.id) AS nights,
    min(er.start_datetime) AS first_night,
    max(er.end_datetime) AS last_night
FROM data_source ds
JOIN event_record er ON er.data_source_id = ds.id AND er.category = 'sleep'
WHERE ds.user_connection_id IS NULL
  AND ds.provider::text IN ('apple', 'samsung', 'health_connect')
  AND er.created_at >= '2026-09-18'
  AND EXISTS (
      SELECT 1 FROM user_connection uc
      WHERE uc.user_id = ds.user_id AND uc.provider = ds.provider::text AND uc.status = 'active'
  )
GROUP BY ds.id, ds.user_id, ds.provider, ds.device_model, ds.source
ORDER BY last_night DESC;
