# Deploying to OSU Container Services

Production runs on Ohio State's Container Services Kubernetes platform, namespace `pit`.
GitHub holds the code and builds the images; the cluster only runs images that the
Flux repo names.

```
push to main ──► build.yml ──► ghcr.io/michael-dial/open-wearables-{platform,frontend}:sha-<7>
                                              │
merge request in flux_pit changing the tag ◄──┘
        │
        ▼
Flux reconciles namespace pit
```

The images moved from `ghcr.io/dial203/*` to `ghcr.io/michael-dial/*` when the repo moved to
the Michael-Dial org: an org repo's `GITHUB_TOKEN` can't push to a personal account's packages.
Tags built before the move stay at the old path, and new tags exist only at the new one, so the
first `flux_pit` merge request after the move changes the image path as well as the tag. New org
packages start private, and the cluster pulls GHCR anonymously, so set each package's visibility
to public (org package settings) before that merge request.

Images build automatically. Changing the tag is a manual merge request on purpose: the
API runs Alembic and data migrations every time it starts, so every tag change migrates
the production database. That step should have a person checking it and leave a record.

## Where things live

| What | Where |
|---|---|
| Manifests (source of truth) | `https://repo.service.osu.edu/ContainerService/customer_flux/flux_pit`, under `production/` (OSU network or VPN only) |
| Local working copy | `deploy/container-services/` in this repo, gitignored |
| Pods, logs, restarts | https://containers.it.osu.edu/ → namespace `pit` → Pods |
| Access and usage | https://containerbilling.org.ohio-state.edu/ |

The repo is `flux_pit`, not `flux_pit_cs`; Container Services' generic docs show the
`flux_<namespace>_cs` pattern, which doesn't match this namespace. A file in `production/` has
no effect until it is listed in `production/kustomization.yaml`. Our role on `flux_pit` is
Developer, so every change goes in as a merge request that a Maintainer merges.

## Components

| File | Runs | Exposure | UID |
|---|---|---|---|
| `ow-secrets.yaml` | Secret `ow-backend-env` | - | - |
| `ow-config.yaml` | ConfigMap `ow-backend-config` | - | - |
| `ow-redis.yaml` | `redis:8`, no persistence, password from `REDIS_PASSWORD` | ClusterIP `ow-redis:6379` | 999 |
| `ow-api.yaml` | platform image, `scripts/start/app.sh`, port 8000 | `openwearables-api.webtest.osu.edu`, public gateway | 1001 |
| `ow-worker.yaml` | platform image, `scripts/start/worker.sh` | internal | 1001 |
| `ow-beat.yaml` | platform image, `scripts/start/beat.sh`, `Recreate` | internal | 1001 |
| `ow-web.yaml` | frontend image, Nitro on 3000 | `openwearables.webtest.osu.edu`, OSU-internal gateway | 1000 |

Web-facing pods (API and dashboard) each have two containers: the Apache proxy
(`docker-apacheshib-revproxy`, UID 48) listens on 8080 and forwards to the app on localhost.
Neither has Shibboleth. The API must stay public: wearable providers call its OAuth callbacks
and webhooks from the internet, and the mobile SDKs post to it from off campus. The dashboard
is on the internal gateway, so it and any link to it (e.g. invitation emails) only work on
campus or VPN. Its hostname must resolve to the internal gateway's address. `128.146.144.48`
(`LB-VIP128-146-144-48`) is the public gateway; a dashboard name pointed there gets 404 everywhere.

Redis requires a password (`--requirepass` from the Secret key `REDIS_PASSWORD`, which the
backend reads too) because nothing stops other pods on the cluster from reaching its Service.
Drop it only if Container Services confirms a NetworkPolicy isolates namespace `pit`.

Every container has resource requests and limits, sized from memory measured on the published
images when idle: API 285Mi, worker 705Mi, beat 274Mi, dashboard 86Mi, Redis 15Mi. The proxy's
figures are estimates. The worker runs Apple Health XML imports; if it's OOMKilled during one,
raise its limit. Redis runs with `--maxmemory 384mb` under a 512Mi limit, so when full it refuses
writes instead of being killed and losing the queue.

`TELEMETRY_ENABLED` is `false`. Upstream sends daily anonymous usage pings to
`telemetry.openwearables.io` by default (payload in `docs/dev-guides/telemetry.mdx`). Turn it on
only with the IRB/data steward's approval.

Not deployed: Svix (`OUTGOING_WEBHOOKS_ENABLED=false`; `create_svix_db.py` then skips itself),
Flower, the SvelteKit portal, FIT file storage. Postgres is Container Services' managed
service; there is no Postgres container.

## First deploy

1. **Images.** Use a `sha-<7>` tag from `main` at or after `bdfadad`: the platform tag in
   `ow-api`, `ow-worker` and `ow-beat`, the frontend tag in `ow-web`. Earlier tags run as root
   and won't start under `runAsUser`. The GHCR packages are public; check both pull while logged out:
   `docker logout ghcr.io && docker pull ghcr.io/michael-dial/open-wearables-platform:sha-<7> && docker pull ghcr.io/michael-dial/open-wearables-frontend:sha-<7>`.
2. **Ask Container Services** for:
   - managed Postgres with one database, `open-wearables`, owned by the app's user. Alembic
     creates the schema; no extensions are needed. Ask which versions they support. The stack
     is tested on 18, which `docker-compose.prod.yml` also uses.
   - DNS: `openwearables-api.webtest.osu.edu` on the public gateway, and
     `openwearables.webtest.osu.edu` on the **internal** gateway's VIP
   - whether a NetworkPolicy isolates namespace `pit`; this decides whether Redis auth is optional
   - whether `flux_pit` supports SOPS or Sealed Secrets, so `ow-secrets.yaml` needn't sit in
     GitLab as decodable base64
   - whether billing units count requests, limits or actual usage

   `pit-registry-pull` only covers `repo.service.osu.edu:5000`. Neither the GHCR images nor the
   proxy image (`registry.containers.it.osu.edu`) use it, so its 09-2026 expiry doesn't block
   this deploy. The manifests keep it in `imagePullSecrets` because the template does.
3. **Fill in values.** In `ow-config.yaml`, replace every `TODO_` value: DB host and user from
   Container Services, and the rest from the Portainer `.env`. In `ow-secrets.yaml`, replace
   every `CUSTOM_BASE64_ENCODED_VALUE` with `echo -n "value" | base64`. Use a new `SECRET_KEY`,
   not Portainer's, and a non-default `ADMIN_PASSWORD`. Generate `REDIS_PASSWORD` with
   `python3 -c "import secrets; print(secrets.token_hex(32))"`. For providers you don't use, delete the
   `*_CLIENT_ID` and the matching secrets. Before committing, this must print nothing:
   `grep -n "TODO_\|CUSTOM_BASE64_ENCODED_VALUE" deploy/container-services/*.yaml`.
   A leftover `CUSTOM_BASE64_ENCODED_VALUE` is invalid base64 and stops Flux applying everything.
   Base64 is encoding, not encryption, so keep `flux_pit` read access narrow.
4. **Merge request.** On VPN, copy the seven `ow-*.yaml` files into `flux_pit/production/`,
   add them to `production/kustomization.yaml` (see `kustomization-entries.yaml`), and open a
   merge request.
5. **Watch** the pods come up. The API's first start runs every migration on an empty
   database, so it takes longer than usual. Until it finishes, the proxy returns 502/503.
6. **Providers.** Re-register OAuth redirect URIs and webhook URLs with each provider at
   `https://openwearables-api.webtest.osu.edu`.
7. **Data approval.** Before real participant data goes in, confirm with Container Services
   and the IRB/data steward that the platform is approved for identifiable health data.

## Updating

1. Merge to `main`; wait for `build.yml` to finish.
2. Open a merge request in `flux_pit` that changes the `sha-` tag. Use the same platform tag in
   `ow-api`, `ow-worker` and `ow-beat` (all three must run the same code against the same
   migrated schema), and change `ow-web` if the frontend changed.
3. After merge, Flux rolls the Deployments. Watch `ow-api` logs for the migration output.

A ConfigMap or Secret change doesn't reach running pods (`envFrom` is read at start, and the
proxy's `local.conf` is a `subPath` mount). Delete the affected pods in the GUI after the
merge request lands.

## Moving the homelab instance's data

The homelab instance (Proxmox CT 103, Portainer, containers `*__open-wearables`) holds
every account and measurement collected so far. It moves to Container Services once,
by dump and restore, **before** anything else is written to Container Services: the
restore replaces the target database, so anything already there is lost, and an import
into an instance that is later overwritten has to be redone. Database ids (users,
connections, data sources) survive the move, so every pointer the two hubs hold into
OW stays valid; only the base URL and the API key change.

Svix is not deployed on Container Services, so only the `open-wearables` database
moves, not `svix`. `MASTER_KEY` decrypts env-file values, not database columns, so
nothing in the dump depends on it.

### Before the day

1. **Same schema or newer.** Container Services must run a platform tag at or after
   the code homelab runs, so its migrations only move forward. Compare:
   `docker exec postgres__open-wearables psql -U open-wearables -d open-wearables -tAc "select version_num from alembic_version"`
   with the newest `Running upgrade … -> <rev>` line in the `ow-api` pod's logs. If
   homelab is ahead, ship a newer tag to Container Services first.
2. **Postgres version.** Ask Container Services which major version the managed
   database runs. Homelab is 18. A custom-format dump restores only with a `pg_restore`
   of the same or a newer major version, and a dump from 18 into an older server can
   fail on settings that version doesn't know.
3. **Who restores.** We have no route to the managed database and no RBAC to start a
   pod, and the platform image has no `pg_restore`. Ask Container Services (consulting
   hours) to restore the file into `open-wearables`, and agree how the file reaches
   them; it holds identifiable health data, so use an OSU-approved transfer, not a
   public link. Restore flags: `pg_restore --clean --if-exists --no-owner
   --no-privileges --role=<app user> -d open-wearables`.
4. **Vendor app credentials.** A stored connection's refresh token works only with the
   vendor app (client id and secret) that issued it. Every `*_CLIENT_ID` and
   `*_CLIENT_SECRET` in `ow-backend-env` must be homelab's, for every provider with a
   live connection, or those connections fail at their next refresh and need a
   re-login.
5. **`SECRET_KEY` stays new.** It signs dashboard sessions and SDK tokens and derives
   the default Suunto, Strava, Oura and Google webhook secrets. Container Services'
   own key means: everyone signs in to the dashboard again, the hubs mint new SDK
   tokens (they do on demand), and the derived webhook secrets are entered at each
   vendor with the new URL (step 9), which has to happen anyway.

### On the day

1. **Merge request: stop Container Services.** `replicas: 0` on `ow-api`, `ow-worker`
   and `ow-beat`, so nothing writes while the database is replaced and no beat runs
   against a half-restored schema.
2. **Stop every homelab writer**, leaving the database up:
   `docker stop celery-beat__open-wearables celery-worker__open-wearables flower__open-wearables backend__open-wearables svix-server__open-wearables`.
   From here on homelab must never run its worker or beat again: two instances
   refreshing the same OAuth tokens invalidate each other's (most vendors rotate the
   refresh token on use). Change their restart policy to `no` in Portainer so a
   reboot or Watchtower doesn't start them.
3. **Count what is there:**
   ```
   docker exec postgres__open-wearables psql -U open-wearables -d open-wearables -c "
   select 'user', count(*) from \"user\" union all
   select 'user_connection', count(*) from user_connection union all
   select 'data_source', count(*) from data_source union all
   select 'event_record', count(*) from event_record union all
   select 'sleep_details', count(*) from sleep_details union all
   select 'series_sample', count(*) from series_sample union all
   select 'api_key', count(*) from api_key"
   ```
   Keep the output; step 7 compares against it.
4. **Dump:**
   ```
   docker exec postgres__open-wearables pg_dump -U open-wearables -d open-wearables \
     -Fc --no-owner --no-privileges -f /tmp/ow-homelab.dump
   docker cp postgres__open-wearables:/tmp/ow-homelab.dump /root/ow-homelab.dump
   docker exec postgres__open-wearables rm /tmp/ow-homelab.dump
   sha256sum /root/ow-homelab.dump && ls -lh /root/ow-homelab.dump
   ```
   Send the file and its checksum to Container Services; they restore it (above).
5. **Merge request: start Container Services** (replicas back to 1). `ow-api` runs any
   migrations newer than homelab's on start; watch its log for them.
6. **API keys come with the data.** Keys are stored as hashes, so homelab's keys now
   work against Container Services and the keys made there before the restore do not.
   Update every client that holds one: the Validation Hub's `ow_instances` row, the
   Sleep Validation Hub's Open Wearables instance (base URL and key; its user links
   keep working, since the user ids are the same), and any task-environment network
   secret for `openwearables-api.webtest.osu.edu`.
7. **Verify.** The same counts, through the API or by Container Services on the
   database, must match step 3 exactly. Spot-check one participant's recent nights on
   `/users/{id}/events/sleep` against homelab's.
8. **Phones.** Every app that relays through the SDK (Apple Health, Health Connect,
   the participant app) must point at `https://openwearables-api.webtest.osu.edu`.
   Data a phone cannot deliver it retries, but only until the app is updated.
9. **Vendors.** For each provider with live connections, add
   `https://openwearables-api.webtest.osu.edu` OAuth redirect URIs and move webhook
   subscriptions there (Polar's re-registration saves a new signature secret to
   `provider_settings` by itself). Then run one sync per provider from the dashboard
   and confirm new data lands.
10. **Keep homelab's database** stopped but intact until the hubs have run on
    Container Services for a while; it is the rollback. Delete `/root/ow-homelab.dump`
    once Container Services confirms the restore.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing appears in the GUI | file not in `kustomization.yaml`; a YAML error, or a leftover `CUSTOM_BASE64_ENCODED_VALUE`, blocking Flux |
| `ImagePullBackOff` on the proxy | `registry.containers.it.osu.edu` path or availability; ask Container Services (not `pit-registry-pull`, which only covers `repo.service.osu.edu:5000`) |
| `ImagePullBackOff` on an app container | wrong `sha-` tag, or still `sha-TODO_AFTER_MERGE` |
| Dashboard 404 on campus/VPN | its hostname resolves to the public gateway VIP, not the internal one |
| `NOAUTH` / `WRONGPASS` in backend logs | `REDIS_PASSWORD` missing, or changed without restarting `ow-redis` and the backend pods |
| Redis `OOM command not allowed` | queue or cache hit `--maxmemory`; look for a stuck worker or backlog |
| Worker `OOMKilled` | a large Apple Health XML import exceeded the 2Gi limit; raise it |
| `ImagePullBackOff` on `redis:8` with 429 | Docker Hub anonymous rate limit; ask Container Services about a mirror |
| `CrashLoopBackOff`, permission denied | an image built before the non-root change, or a path UID 1001 can't write |
| `CreateContainerConfigError` | `ow-backend-config` or `ow-backend-env` missing or not in `kustomization.yaml` |
| 502/503 from the proxy | API still running startup migrations, or `APACHESHIB_TARGET` port ≠ app port |
| Dashboard loads, API calls fail | `VITE_API_URL` or `CORS_ORIGINS` doesn't match the real hostnames |
