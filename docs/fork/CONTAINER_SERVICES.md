# Deploying to OSU Container Services

Production runs on Ohio State's Container Services Kubernetes platform, namespace `pit`.
GitHub holds the code and builds the images; the cluster only runs images that the
Flux repo names.

```
push to main ──► build.yml ──► ghcr.io/dial203/open-wearables-{platform,frontend}:sha-<7>
                                              │
merge request in flux_pit changing the tag ◄──┘
        │
        ▼
Flux reconciles namespace pit
```

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
| `ow-redis.yaml` | `redis:8`, no persistence | ClusterIP `ow-redis:6379` | 999 |
| `ow-api.yaml` | platform image, `scripts/start/app.sh`, port 8000 | `openwearables-api.webtest.osu.edu`, public gateway | 1001 |
| `ow-worker.yaml` | platform image, `scripts/start/worker.sh` | internal | 1001 |
| `ow-beat.yaml` | platform image, `scripts/start/beat.sh`, `Recreate` | internal | 1001 |
| `ow-web.yaml` | frontend image, Nitro on 3000 | `openwearables.webtest.osu.edu`, OSU-internal gateway | 1000 |

Web-facing pods (API and dashboard) each have two containers: the Apache proxy
(`docker-apacheshib-revproxy`, UID 48) listens on 8080 and forwards to the app on localhost.
Neither has Shibboleth. The API must stay public: wearable providers call its OAuth callbacks
and webhooks from the internet, and the mobile SDKs post to it from off campus. The dashboard
is on the internal gateway, so it and any link to it (e.g. invitation emails) only work on
campus or VPN.

Not deployed: Svix (`OUTGOING_WEBHOOKS_ENABLED=false`; `create_svix_db.py` then skips itself),
Flower, the SvelteKit portal, FIT file storage. Postgres is Container Services' managed
service; there is no Postgres container.

## First deploy

1. **Images.** Merge the non-root Dockerfile change to `main` and wait for `build.yml`. Replace
   `sha-TODO_AFTER_MERGE` with the new `sha-<7>` tag: the platform tag in `ow-api`, `ow-worker`
   and `ow-beat`, the frontend tag in `ow-web`. Earlier tags run as root and won't start under
   `runAsUser`. The GHCR packages are public. Check the tag exists while logged out:
   `docker manifest inspect ghcr.io/dial203/open-wearables-platform:sha-<7>`.
2. **Ask Container Services** for:
   - managed Postgres (18, to match `docker-compose.prod.yml`) with one database,
     `open-wearables`, owned by the app's user. Alembic creates the schema. No extensions are needed.
   - DNS for `openwearables.webtest.osu.edu` and `openwearables-api.webtest.osu.edu`
   - renewal of the `pit-registry-pull` token, which expired 09-2026. The proxy image is pulled
     from OSU's registry with it.
3. **Fill in values.** In `ow-config.yaml`, replace every `TODO_` value: DB host and user from
   Container Services, and the rest from the Portainer `.env`. In `ow-secrets.yaml`, replace
   every `CUSTOM_BASE64_ENCODED_VALUE` with `echo -n "value" | base64`. Use a new `SECRET_KEY`,
   not Portainer's, and a non-default `ADMIN_PASSWORD`. For providers you don't use, delete the
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

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing appears in the GUI | file not in `kustomization.yaml`; a YAML error, or a leftover `CUSTOM_BASE64_ENCODED_VALUE`, blocking Flux |
| `ImagePullBackOff` on the proxy | expired `pit-registry-pull` token |
| `ImagePullBackOff` on an app container | wrong `sha-` tag, or still `sha-TODO_AFTER_MERGE` |
| `ImagePullBackOff` on `redis:8` with 429 | Docker Hub anonymous rate limit; ask Container Services about a mirror |
| `CrashLoopBackOff`, permission denied | an image built before the non-root change, or a path UID 1001 can't write |
| `CreateContainerConfigError` | `ow-backend-config` or `ow-backend-env` missing or not in `kustomization.yaml` |
| 502/503 from the proxy | API still running startup migrations, or `APACHESHIB_TARGET` port ≠ app port |
| Dashboard loads, API calls fail | `VITE_API_URL` or `CORS_ORIGINS` doesn't match the real hostnames |
