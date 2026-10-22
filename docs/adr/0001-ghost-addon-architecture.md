# ADR 0001: Ghost add-on architecture

## Status

Accepted (2026-10-21). This is the single architecture record for the Ghost
add-on; future architectural changes extend this document rather than adding
per-change records. Updated 2026-10-21: the Ghost application moved from a
first-boot volume install to a two-stage image build (no Python in the final
image).

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
- **Two-stage build.** The `app` stage installs Node, Python, and the Ghost
  CLI and installs the Ghost application into `/opt/ghost`; the final stage
  re-installs the same Node tarball and copies only the application tree
  (`COPY --from=app /opt/ghost`). The published image therefore carries no
  Python and no Ghost CLI: they exist only in the discarded builder stage.
- **Node 22, not the distro's Node 20**: ghost-cli 1.33.7 pulls an execa
  release that needs Node >= 22.12, and Ghost 5.130.6 supports
  `^18.12.1 || ^20.11.1 || ^22.13.1`. Ghost 6.x was rejected: its CLI flow
  requires pnpm 12, which is not available on trixie.
- **ghost-cli 1.33.7 is pinned** in the Dockerfile (builder stage only) and
  kept current by Renovate.

### First-boot install flow

- The application is installed **at build time** in the `app` stage with
  `ghost install 5.130.6 local --dir /opt/ghost --no-start --no-setup
  --db client --port 2368 --ip 0.0.0.0` and ships in the final image at
  `/opt/ghost`, owned by the `ghost` user (its `current` symlink points
  into `/opt/ghost/versions/<version>`).
- `etc/cont-init.d/99-ghost` (bashio) runs before the s6 service and
  prepares the persisted volume at `/config/ghost`. Every boot it removes
  the legacy in-volume application (earlier add-on versions installed
  `versions/` and `.ghost-cli` into the volume; the content and the config
  are kept) and (re)creates the blog's `current` symlink pointing at the
  image's application. The symlink is required by Ghost's internal root
  detection (`@tryghost/root-utils` requires a `current` directory in the
  working directory) and is refreshed on every boot so an add-on update
  (new app version) takes effect.
- On the first boot (no `config.production.json` yet) the script seeds the
  persisted `content/` from the image's `content/` tree (the installer
  creates the subdirectories and links the built-in themes; without them the
  SQLite database could not be created and Ghost would exit silently) and
  writes `config.production.json` once. Reboots never rewrite the config.
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
  from the blog directory (whose `current/` symlink resolves into the
  image's application), and fails hard if no configured blog exists (s6
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
- **Consequence:** the image carries no C toolchain (no build-essential).
  The module is installed **at build time** by running that npm install in a
  staging directory inside the application tree and copying the package into
  `current/node_modules`, with a final `node -e "require('sqlite3')"` check;
  the staging directory is removed before the stage is handed over. At
  runtime the cont-init script only *verifies* the module (through the
  blog's `current/` symlink) and fails loudly if the image is broken — no
  download, no install, no network at runtime.
- **The staging directory lives in the application tree, not in `/tmp`:**
  Home Assistant mounts the container `/tmp` as a `noexec` tmpfs, where
  `sh -c "prebuild-install ..."` fails with `Permission denied` (verified on
  a real instance). The staging directory is therefore
  `/opt/ghost/.sqlite3-staging` and is removed before the stage is handed
  over to the final image.
- The staging install runs with `--loglevel=error --no-update-notifier`:
  the pinned sqlite3 5.1.7 tree is from 2023, so npm prints deprecation
  warnings for its build-time-only dependencies (prebuild-install 7.x,
  node-gyp 8, npmlog, …) at every install. Those transitive pins cannot be
  bumped without replacing the pinned sqlite3 itself, so the noise is
  suppressed instead; a failed install still aborts the build. (The
  long-term silence would be Ghost 6, which swapped sqlite3 for
  better-sqlite3 — blocked today by its pnpm 12 requirement, see above.)
  `python3` and `python3-setuptools` are installed in the `app` stage only:
  ghost-cli 1.33.x's SQLite3 dependency check hard-fails on Node 22 without
  them, regardless of whether a build would happen — and the stage is
  discarded, so the published image carries no Python.
- Trade-off accepted: the npm registry and the GitHub release assets must be
  reachable **at build time only**; a running container never downloads
  anything.

### Add-on options: environment above the config file

- The `url` option is published on every boot as the `url` environment
  variable (lowercase: Ghost's nconf env source does not normalize case
  and the config key is `url` — an uppercase `URL` would be ignored,
  verified against the app's nconf store). This matters because the
  config file is generated only on the first boot, before the user has
  set the option: without the environment, a `url` set later would never
  reach the running blog, which would keep Ghost's default
  `http://localhost:2368/` — whose links, sitemaps, and in-page API
  references (e.g. `members/api/member/`) resolve to the visitor's own
  machine. An empty option clears the variable on the next boot.
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

- `ingress: true` on port 2368 **and** `ports: 2368/tcp` (standalone). The
  blog front page is `/`, the admin panel `/ghost/` (the admin account is
  created by the user in the setup wizard on first visit; no credentials
  are baked into the add-on).
- **Access at the origin root.** Ghost builds its theme and core asset
  references relative to the site root (`getAssetUrl` resolves against the
  configured `url`'s path; an empty `url` falls back to
  `http://localhost:2368/`, which in a visitor's browser resolves to the
  visitor's own machine) and its in-page API calls from the configured
  `url`. The blog therefore must be reachable at the origin root without a
  path prefix: the exposed port 2368 (direct `http://<host>:2368` or a
  domain reverse-proxied onto it), with the `url` option set to that exact
  origin (the access protocol must match it: Ghost normalizes `http`
  requests to the `url`'s protocol with a 301, so an `https` url should be
  reached through a TLS proxy). Home Assistant's built-in ingress serves
  the add-on under a path prefix (`/api/ghost/...`): the admin panel there
  still renders (its assets are referenced relative to the document), the
  front page does not (its root-relative asset references 404 at the host
  root). This is a Ghost limitation, not an add-on defect.
- `map:` exposes `addon_config` at `/config` (writable; the blog's content
  and config live there, the application stays in the image) and `share` at
  `/share`; `tmpfs: true` keeps the container's `/tmp` (including bashio's
  option cache) fresh on every start, so option changes always take effect.

### CI/CD and metadata hygiene

- `config.yaml` gained the required `image: crazyrokr/ghost-{arch}` field,
  the correct port/ingress/webui values, the options/schema above, and a
  valid `map`/arch set (validated by local tests that mirror hassfest).
- The CI workflow now targets `main` (the repository branch), the base-image
  digest pin in `.github/docker-lock.json` matches the actual `FROM`, and
  Renovate no longer carries template rules for the old add-on.
- Renovate manages the version pins in the Dockerfile through
  `customManagers` (regex): `NODE_VERSION` (npm `node`, limited to the 22
  line — Ghost 5.130.6 supports `^22.13.1`, and the build only ships glibc
  Node 22 tarballs), `ghost-cli` (npm, builder stage only), and the
  installed Ghost application version (npm `ghost`, limited to the 5 line
  until Ghost 6's pnpm 12 requirement is revisited). A Node update PR must
  also refresh the two per-architecture sha256 hashes (from the nodejs.org
  dist `SHASUMS256.txt` of the new version).

## Consequences

- The add-on works with zero external services (built-in SQLite, persisted
  in `/config/ghost`) and with an external MariaDB/MySQL when configured.
- The published image is ~1.0 GB (the ~520 MB application tree ships in the
  layers) instead of ~622 MB with a first-boot install — at the benefit of a
  network-free first boot, a ~1.2 MB persisted volume (smaller HA backups),
  and add-on updates that deliver new Ghost versions without any user
  action. Volumes written by earlier add-on versions are migrated in place
  on the first start.
- Ghost is pinned to the 5.x line (5.130.6); moving to Ghost 6 requires
  revisiting the Node/pnpm decision.
- Because bashio caches add-on options in `/tmp`, `tmpfs: true` is required
  for option changes to be picked up on container restart.

## Verification

- `tests/` (Python, Given-When-Then, 48 tests): manifest/hassfest-mirror
  checks, Dockerfile structure (two-stage layout, build-time Ghost install
  into `/opt/ghost`, Python in the builder stage only, no package-install
  layer in the final stage, Node tarball hashes for both architectures),
  CI/renovate/lock consistency (incl. the renovate custom-manager pin
  extraction matched against the actual Dockerfile pins and the Node 22 /
  Ghost 5 version caps), and behavior tests for the s6 scripts
  (incl. the url option published/cleared through the container
  environment with the existing config file left unrewritten)
  (first-boot volume setup with content seeding, legacy in-volume cleanup,
  config generation, the sqlite3 verification incl. the broken-image edge
  case, mysql option publishing, stale env clearing, service start).
- End-to-end in Docker: an add-on container with the real s6 init against a
  fake supervisor — a fresh volume boots with the image-resident
  application, creates `content/data/ghost.db`, and serves `/` and
  `/ghost/` (HTTP 200) without any download; a restart boots in ~1 s with
  no error log entries; a legacy volume (522 MB, installed by an earlier
  add-on version) is migrated in place to the new layout (1.2 MB, content
  and database preserved); with `db_type: mysql` the same container runs
  the migrations against a real MariaDB container (75 tables) and creates
  no SQLite file; reverting the options clears the stale `database__*`
  variables and the blog returns to SQLite; setting the `url` option on an
  existing volume takes effect on the next start through the container
  environment (the config file stays unrewritten, the app references the
  configured origin instead of `http://localhost:2368`), and resetting the
  option clears it again.
