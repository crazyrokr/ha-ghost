# ADR 0001: Ghost add-on architecture

## Status

Accepted (2026-10-21). This is the single architecture record for the Ghost
add-on; future architectural changes extend this document rather than adding
per-change records. Updated 2026-10-21: the Ghost application moved from a
first-boot volume install to a two-stage image build (no Python in the final
image). Updated 2026-10-22: the database is configured entirely through the
container environment (the generated config file carries no `database`
section, which removes a mysql2 `filename` warning with external databases),
and the `log_console` option was added. Updated 2026-10-27: Node is
installed once in a shared `node` stage; the final image copies only the
node binary instead of re-installing the whole distribution.

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
  selected per `BUILD_ARCH` (the build argument the Home Assistant builder
  passes).
- **Multi-stage build.** A `node` stage downloads the hash-verified tarball
  once; the `app` stage copies `/usr/local` from it, installs Python and the
  Ghost CLI, and installs the Ghost application into `/opt/ghost`; the final
  stage copies only the node binary (`COPY --from=node /usr/local/bin/node`)
  and the application tree (`COPY --from=app /opt/ghost`). The published
  image therefore carries no Python, no Ghost CLI, and no npm: they exist
  only in the discarded stages. The runtime only executes `node`, so the
  npm, corepack, header, and share trees never reach the final image.
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
  writes `config.production.json` once, without a `database` section (see
  Add-on options below). Reboots never rewrite the config, except the
  one-time removal of the add-on-generated `database` section from files
  written by earlier add-on versions (see there as well).
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
  `bashio::config`. **The database is configured entirely through the
  container environment** (`/run/s6/container_environment`, applied by the
  service through `with-contenv`): with `db_type: mysql` the cont-init
  script publishes `database__client=mysql`, `database__connection__host`,
  `...__port`, `...__user`, `...__password`, `...__database` (the same
  mechanism the official Ghost docker image uses); otherwise it publishes
  the built-in database instead — `database__client=sqlite3` and
  `database__connection__filename` (the blog volume's `ghost.db`). Without
  the variables the application's own default (an unreachable local mysql)
  would apply. All `database__*` variables are cleared on every boot and
  republished from the options, so switching between the backends is a
  restart.
- **Why the config file carries no `database` section.** Ghost's nconf
  loader merges the `database` object of *every* store (the config file,
  the container environment, the application's default config) into one on
  every `get('database')` call, and a file-borne section cannot be removed
  through the environment again: nconf re-merges from the file on each
  access (`this.clear` does not work — nconf#235), so the delete+set in
  Ghost's own `sanitizeDatabaseProperties` (added for exactly this
  situation) does not persist. The earlier generated config carried a
  sqlite `database` section, so with `db_type: mysql` the sqlite
  `filename` connection option leaked into the merged connection object
  and the mysql2 driver printed
  `Ignoring invalid configuration option passed to Connection: filename`
  (a warning today, an error in a future mysql2 release). Verified against
  the Ghost 5.130.6 source (`core/server/data/db/connection.js` passes the
  merged object to knex; `shared/config/utils.js` carries the ineffective
  sanitizer) and the nconf 0.13.0 merge behavior. New config files are
  therefore written without the section, and volumes created by earlier
  add-on versions are migrated in place on the next start: a node
  one-liner removes the section from `config.production.json` only while
  it still matches the add-on-generated default (a built-in sqlite client
  whose connection holds exactly the add-on's default database file) — a
  config the user edited by hand is left untouched, and the built-in
  database is published through the environment instead. The `settings`
  (`busyRetryMax`) and `migrate` keys the old section carried are dropped
  as well: Ghost 5.130.6 reads neither (migrations run unconditionally,
  and `useNullAsDefault` plus the sqlite pool are added by Ghost's own
  `configure()`).
- With `db_type: mysql`, all of `db_host`, `db_user`, `db_name` must be set;
  otherwise the add-on refuses to start (a half-configured external
  database would otherwise leave the blog on the built-in one while the
  user believes it is external).
- The mail options (`mail_transport`, `mail_host`, `mail_port`,
  `mail_secure`, `mail_user`, `mail_password`, `mail_from`) are published
  the same way: with `mail_host` set, `mail__transport`,
  `mail__options__host`, `...__port`, and — when set — `...__secure`,
  `...__auth__user`, `...__auth__pass` (Ghost's mail config is
  `mail.from`, `mail.transport`, `mail.options`); without a host the
  built-in `direct` transport is kept and only `mail__from` (when set) is
  published — fixing the "Missing mail.from" fallback without changing
  delivery. All `mail__*` variables are cleared on every boot, so a reset
  restores the app defaults.
- Option `log_console` (`bool`, default `true`): with it on, the
  cont-init script publishes `logging__transports=["stdout","stderr","file"]`
  on every start — the application's production default is file-only, and
  the environment merges above the config file, so the application log is
  printed to the container console (what the add-on log tab and
  `docker logs` show) as well as to the files. With it off the variable is
  cleared and only the application default (the file transport) remains:
  the log files in the volume keep the application log, and the console
  carries only s6 status lines.

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
- **Application log in the add-on log.** Ghost's production default logs
  to file only (`transports: ["file"]` in the app's production config),
  one file per origin in the volume's `content/logs`; the container
  console — what the add-on log tab and `docker logs` show — would carry
  only s6 status lines. The cont-init script therefore publishes
  `logging__transports=["stdout","stderr","file"]` into the container
  environment on every start (the environment merges above the config
  file, so existing volumes get console logs on the next start without a
  config rewrite; s6 longrun services without a `log/` directory print
  directly to the container console, verified with a probe service).

### CI/CD and metadata hygiene

- `config.yaml` gained the required `image: crazyrokr/ha-ghost` field (a
  plain multi-arch reference: the build workflow publishes one manifest for
  all supported arches), the correct port/ingress/webui values, the
  options/schema above, and a valid `map`/arch set (validated by local tests
  that mirror hassfest).
- The CI workflow now targets `main` (the repository branch), the base-image
  digest pin in `.github/docker-lock.json` matches the actual `FROM`, and
  Renovate no longer carries template rules for the old add-on.
- The pipeline runs the test suite through the shared `gha-workflows`
  reusable test workflow before any image build. It detects the test
  files in `tests/` (Python via pytest, installing `tests/requirements.txt`
  first; JavaScript via the project's `npm test` when a `package.json`
  test script exists, otherwise through Node's built-in runner,
  installing the `package.json` dependencies first when that file is
  present). A `tests/` change runs the suite for every app without
  rebuilding; a change to an add-on's payload files tests and rebuilds
  that app; a change to the pipeline workflows tests and rebuilds every
  app. A manual run can set `force_build` to test, build and publish
  every app regardless of the changed files. A failing test fails the
  test job, and the build job depends on it, so no image is built or
  published. Both `gha-workflows` references
  (the auto-locker action and the reusable CI workflow) are pinned to the
  same full commit SHA.
- Renovate manages the version pins in the Dockerfile through
  `customManagers` (regex): `NODE_VERSION` (npm `node`, declared once in the
  shared `node` stage, limited to the 22 line — Ghost 5.130.6 supports
  `^22.13.1`, and the build only ships glibc Node 22 tarballs),
  `ghost-cli` (npm, builder stage only), and the
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
  action. The final stage carries only the node binary (~75 MB less than
  the full distribution) instead of a second copy of the tarball tree, and
  the tarball is downloaded once per build. Volumes written by earlier
  add-on versions are migrated in place on the first start, now including
  the one-time removal of the add-on-generated `database` section from the
  config file (content and everything else in the file are preserved; a
  hand-edited file is left untouched).
- Ghost is pinned to the 5.x line (5.130.6); moving to Ghost 6 requires
  revisiting the Node/pnpm decision.
- Because bashio caches add-on options in `/tmp`, `tmpfs: true` is required
  for option changes to be picked up on container restart.

## Verification

- `tests/` (Python, Given-When-Then, 60 tests; run by CI before any build):
  manifest/hassfest-mirror
  checks, Dockerfile structure (multi-stage layout with the Node pin in the
  shared `node` stage, build-time Ghost install into `/opt/ghost`, Python in
  the builder stage only, no package-install layer or npm in the final
  stage, Node tarball hashes for both architectures),
  CI/renovate/lock consistency (incl. the renovate custom-manager pin
  extraction matched against the actual Dockerfile pins and the Node 22 /
  Ghost 5 version caps), and behavior tests for the s6 scripts
  (first-boot volume setup with content seeding and config generation
  without a `database` section, legacy in-volume cleanup, the config-file
  migration of the add-on-generated `database` section — incl. the
  user-edited-file guard and the idempotent re-run — the sqlite3
  verification incl. the broken-image edge case, the sqlite and mysql
  option publishing (the mysql set without a `filename` variable),
  `log_console` on/off with stale-variable clearing, url/mail option
  publishing and clearing, service start).
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
  option clears it again; the container console (what the add-on log tab
  shows) carries the application log — boot, requests, errors — while the
  log files are still written to the volume; with the mail options set, a
  member magic-link request is delivered through an external SMTP server
  (a fake one in the test) with the configured from address, and resetting
  the mail options clears the mail__* variables again.
- Re-verified in Docker against the real s6 init and a fake supervisor
  (2026-10-22, the database and logging changes): a fresh volume with
  `db_type: mysql` runs the migrations against a real MariaDB container
  (75 tables), creates no SQLite file, and the container console carries
  no `Ignoring invalid configuration option passed to Connection:
  filename` warning (with the config file as written by earlier add-on
  versions the warning did appear); a volume whose config still carried
  the add-on-generated sqlite `database` section is migrated in place on
  the next start (the section removed, the rest of the file preserved)
  and the blog runs on the selected database in both the mysql and the
  sqlite case; with `log_console: false` the container console carries no
  application log lines while the log files in the volume keep them; and
  a restart of a mysql-configured container boots in ~1 s without the
  warning.
