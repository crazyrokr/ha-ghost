# ADR 0001: Ghost add-on architecture

## Status

Accepted (2026-10-21). This is the single architecture record for the Ghost
add-on; future architectural changes extend this document rather than adding
per-change records.

## Context

The repository started as a Home Assistant community add-on template (the
files described a different add-on and were only half-wired: broken `COPY`,
missing s6 services, no `image:` field, wrong ports, stale CI/lock/renovate
files, CI targeting the `master` branch). The goal is a working, maintainable
add-on that runs [Ghost](https://ghost.org/) inside Home Assistant with:

- a zero-database default (works out of the box),
- an optional external MariaDB/MySQL database selectable from the add-on
  options,
- ingress and exposed-port access,
- reproducible, digest-pinned builds for aarch64 and amd64.

## Decision

### Base image and Node

- Build on the official `ghcr.io/hassio-addons/debian-base` image
  (Debian 13 trixie, s6-overlay, bashio). The `home-assistant/docker-base`
  family provides no Node-based image (only alpine/debian/python/ubuntu), so
  the Node runtime comes from the official hash-verified Node 22 tarball,
  selected per `TARGETARCH`.
- **Node 22, not the distro's Node 20**: ghost-cli 1.33.7 pulls an execa
  release that needs Node >= 22.12, and Ghost 5.130.6 supports
  `^18.12.1 || ^20.11.1 || ^22.13.1`. Ghost 6.x was rejected: its CLI flow
  requires pnpm 12, which is not available on trixie.
- **ghost-cli 1.33.7 is pinned** in the Dockerfile and kept current by
  Renovate.

### First-boot install flow

- `etc/cont-init.d/99-ghost` (bashio) runs before the s6 service. On the
  first boot (no `config.production.json` yet) it runs
  `ghost install 5.130.6 local --no-start --no-setup --db client
  --port 2368 --ip 0.0.0.0` into the persisted config volume at
  `/config/ghost`, then writes `config.production.json` once. Reboots never
  re-install or rewrite the config.
- Ghost CLI refuses to run as root while the container runs as root, so a
  dedicated `ghost` user owns the blog volume; the long-running process is
  started with the base image's built-in `runuser` (no su-exec package).
- The generated config binds `0.0.0.0:2368` (ingress and the exposed port
  both reach it) and sets the built-in SQLite database. Two Ghost 5 loader
  quirks shape it: the config key is `database` (not `db`; the app default
  is MySQL), and relative paths resolve against the *app* directory — so the
  content path and the SQLite file are absolute paths into the blog volume.
- The service script (`etc/services.d/ghost/run`) runs
  `runuser -u ghost -- node current/index.js` with `NODE_ENV=production`
  from the blog directory, and fails hard if no blog is installed (s6
  reports the failure instead of crash-looping silently).

### SQLite default: prebuilt binary, no C toolchain

- Ghost's `sqlite3` 5.1.7 is an *optional* dependency that is silently
  dropped when its install script fails — without it Ghost exits silently
  (`process.exit(2)` in `boot.js`) and the s6 service would restart forever
  without log output. The add-on therefore verifies the module on every
  boot and fails loudly when it is missing.
- Ghost's own install (yarn, per the app's `yarn.lock`) pins
  **prebuild-install 7.1.1**, whose napi-build-utils 1.0.2 computes the
  N-API target with a lexicographic string comparison
  (`'6' <= '10'`) that is false on Node >= 22.12 (napi v10): the prebuild
  download is skipped and the source build fails without a compiler. A plain
  `npm install sqlite3@5.1.7` resolves prebuild-install 7.1.3, which
  downloads the prebuilt binary (published by TryGhost for
  linux-x64/arm64) in seconds.
- **Consequence:** the image carries no C toolchain (no build-essential,
  ~400 MB smaller). The cont-init script heals a dropped module by running
  that npm install in a scratch directory and copying the package into the
  app tree (idempotent, every boot; the copy is only attempted when the
  module is actually missing). `python3` and `python3-setuptools` stay in
  the image: ghost-cli 1.33.x's SQLite3 dependency check hard-fails on
  Node 22 without them, regardless of whether a build would happen.
- Trade-off accepted: first boot needs reachability of the npm registry and
  GitHub release assets (for the ~1 MB binary). If both are blocked the
  add-on fails at boot with a clear error instead of a silent crash-loop,
  and re-booting retries the download.

### External database via add-on options

- Options `db_type` (`sqlite`|`mysql`, default `sqlite`), `db_host`,
  `db_port`, `db_user`, `db_password`, `db_name` are read with
  `bashio::config`. With `db_type: mysql` the cont-init script publishes
  `database__client`, `database__connection__host`, `...__port`,
  `...__user`, `...__password`, `...__database` into the s6 container
  environment (`/run/s6/container_environment`), which the service applies
  through `with-contenv`. Ghost's nconf loader merges those variables above
  the config file — the same mechanism the official Ghost docker image
  uses — so no config file editing is needed, and switching the options
  back to `sqlite` (the variables are cleared on every boot) restores the
  built-in database.
- With `db_type: mysql`, all of `db_host`, `db_user`, `db_name` must be set;
  otherwise the add-on refuses to start (a half-configured external
  database would otherwise leave the blog on the built-in one while the
  user believes it is external).

### Access and persistence

- `ingress: true` on port 2368 (side panel, no host exposure) **and**
  `ports: 2368/tcp` (standalone). The blog front page is `/`, the admin
  panel `/ghost/` (the admin account is created by the user in the setup
  wizard on first visit; no credentials are baked into the add-on).
- `map:` exposes `addon_config` at `/config` (writable; the whole blog
  lives there) and `share` at `/share`; `tmpfs: true` keeps the container's
  `/tmp` (including bashio's option cache) fresh on every start, so option
  changes always take effect.

### CI/CD and metadata hygiene

- `config.yaml` gained the required `image: crazyrokr/ghost-{arch}` field,
  the correct port/ingress/webui values, the options/schema above, and a
  valid `map`/arch set (validated by local tests that mirror hassfest).
- The CI workflow now targets `main` (the repository branch), the base-image
  digest pin in `.github/docker-lock.json` matches the actual `FROM`, and
  Renovate no longer carries template rules for the old add-on.

## Consequences

- The add-on works with zero external services (built-in SQLite, persisted
  in `/config/ghost`) and with an external MariaDB/MySQL when configured.
- The image is ~400 MB lighter than a build-toolchain variant, at the cost
  of a first-boot network dependency (documented above).
- Ghost is pinned to the 5.x line (5.130.6); moving to Ghost 6 requires
  revisiting the Node/pnpm decision.
- Because bashio caches add-on options in `/tmp`, `tmpfs: true` is required
  for option changes to be picked up on container restart.

## Verification

- `tests/` (Python, Given-When-Then): manifest/hassfest-mirror checks,
  Dockerfile structure, CI/renovate/lock consistency, and behavior tests
  for the s6 scripts (first-boot install args, config generation, SQLite
  healing incl. the npm-failure edge case, mysql option publishing, stale
  env clearing, service start).
- End-to-end in Docker: an add-on container with the real s6 init against a
  fake supervisor — first boot installs, heals the SQLite module from the
  prebuilt binary, serves `/` and `/ghost/` (HTTP 200), creates
  `content/data/ghost.db`; with `db_type: mysql` the same container runs
  the migrations against a real MariaDB container (75 tables) and creates
  no SQLite file; reverting the options clears the stale `database__*`
  variables and the blog returns to SQLite.
