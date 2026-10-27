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
node binary instead of re-installing the whole distribution. Updated
2026-10-27: ingress traffic is served by an in-image URL-rewriting proxy
(see Ingress URL rewriting), so the whole blog works under the Home
Assistant ingress path prefix, and the sidebar panel opens the admin
panel. Updated 2026-10-27: ingress admin pages synchronize the sign-in,
setup, reset and boot loading screens with the active Home Assistant
theme (see Admin theme synchronization); the authenticated panel keeps
Ghost's own Night shift and direct access is unchanged. Updated
2026-10-27: the add-on ships Ghost 6.73.0, whose release is pnpm-locked and
whose SQLite driver is better-sqlite3; the build installs a pinned pnpm and
needs no Python (see Base image and Node, SQLite default), and the ingress
admin theme targets Ghost 6's React admin (see Admin theme
synchronization). Ghost 6 also requires MySQL 8 for the external database:
MariaDB is no longer supported by Ghost, so the add-on's `db_type: mysql`
backend now means MySQL 8.

## Context

The repository started as a Home Assistant community add-on template (the
files described a different add-on and were only half-wired: broken `COPY`,
missing s6 services, no `image:` field, wrong ports, stale CI/lock/renovate
files, CI targeting the `master` branch). The goal is a working, maintainable
add-on that runs [Ghost](https://ghost.org/) inside Home Assistant with:

- a zero-database default (works out of the box),
- an optional external MySQL 8 database selectable from the add-on
  options (Ghost 6 dropped MariaDB support),
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
  once; the `app` stage copies `/usr/local` from it, installs the Ghost CLI
  and pnpm, and installs the Ghost application into `/opt/ghost`; the final
  stage copies only the node binary (`COPY --from=node /usr/local/bin/node`)
  and the application tree (`COPY --from=app /opt/ghost`). The published
  image therefore carries no Python, no Ghost CLI, and no npm or pnpm: they
  exist only in the discarded stages. The runtime only executes `node`, so
  the npm, corepack, header, and share trees never reach the final image.
- **Node 22, not the distro's Node 20**: ghost-cli 1.34.0 requires
  `^22.13.0 || ^24.0.0`, and Ghost 6.73.0 requires
  `^22.23.1 || ^24.20.0`. The shared `node` stage pins v22.23.4, the 22 LTS
  patch that satisfies both.
- **ghost-cli 1.34.0 and pnpm 12.10.1 are pinned** in the Dockerfile
  (builder stage only) and kept current by Renovate. Ghost 6 releases carry
  a `pnpm-lock.yaml`, so ghost-cli installs them with pnpm and checks at
  install time that pnpm (or corepack) is available; installing the same
  pnpm version Ghost pins avoids corepack's package-signing key drift and
  an extra download.

### First-boot install flow

- The application is installed **at build time** in the `app` stage with
  `ghost install 6.73.0 local --dir /opt/ghost --no-start --no-setup` and
  ships in the final image at
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
- The generated config binds `0.0.0.0:2368` (the exposed port; ingress
  traffic reaches Ghost through the URL rewriter on 2369, see Ingress URL
  rewriting) and sets the built-in SQLite database. Two Ghost loader
  quirks shape it: the config key is `database` (not `db`; the app default
  is MySQL), and relative paths resolve against the *app* directory — so the
  content path and the SQLite file are absolute paths into the blog volume.
- The service script (`etc/services.d/ghost/run`) runs
  `runuser -u ghost -- node current/index.js` with `NODE_ENV=production`
  from the blog directory (whose `current/` symlink resolves into the
  image's application), and fails hard if no configured blog exists (s6
  reports the failure instead of crash-looping silently).

### SQLite default: better-sqlite3, prebuilt binary, no C toolchain

- Ghost 6 swapped the `sqlite3` package for **better-sqlite3** 12.11.1, an
  *optional* dependency whose install script downloads a prebuilt binary.
  The release's `pnpm-workspace.yaml` allows that build script
  (`allowBuilds: better-sqlite3@12.11.1: true`, with `strictDepBuilds:
  true`), so pnpm runs it during the build. Without the module Ghost exits
  silently (`process.exit(2)` in `boot.js`) and the s6 service would
  restart forever without log output.
- **The build verifies the module.** After `pnpm install` the `app` stage
  runs `node -e "require('/opt/ghost/current/node_modules/better-sqlite3')"`
  (after removing the pnpm store), so a missing or broken prebuild fails
  the image build instead of crash-looping at boot. At runtime the
  cont-init script only *verifies* the module through the blog's
  `current/` symlink and fails loudly if the image is broken — no
  download, no install, no network at runtime.
- **No Python and no C toolchain.** Ghost 6.50+ disabled ghost-cli's
  SQLite/Python dependency check (`python-setuptools.ts` returns false for
  `>= 6.50.0`), and better-sqlite3 ships prebuilt binaries. Neither
  `python3`/`python3-setuptools` (which Ghost 5 needed on Node 22) nor
  build-essential appears in any stage.
- **pnpm store.** ghost-cli installs the pnpm-locked release with the store
  at `/opt/ghost/.pnpm-store` (a sibling of `versions/`, so pnpm can
  hardlink into node_modules and the invoking user can write it). The store
  is a build artifact: the `app` stage removes it before `COPY --from=app
  /opt/ghost`; node_modules keeps its hardlinks, so the published image
  does not carry the store.
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
  the Ghost 6.73.0 source (`core/server/data/db/connection.js` passes the
  merged object to knex; `core/shared/config/utils.js` carries the
  ineffective sanitizer, applied in `loader.js`) and the nconf 0.13.0
  merge behavior. New config files are
  therefore written without the section, and volumes created by earlier
  add-on versions are migrated in place on the next start: a node
  one-liner removes the section from `config.production.json` only while
  it still matches the add-on-generated default (a built-in sqlite client
  whose connection holds exactly the add-on's default database file) — a
  config the user edited by hand is left untouched, and the built-in
  database is published through the environment instead. The `settings`
  (`busyRetryMax`) and `migrate` keys the old section carried are dropped
  as well: Ghost 6.73.0 reads neither (migrations run unconditionally,
  and `useNullAsDefault` plus the sqlite pool are added by Ghost's own
  `configure-knex.js`).
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

- `ingress: true` with `ingress_port: 2369` (the URL rewriter, see Ingress
  URL rewriting), `ingress_entry: ghost/` (the sidebar panel opens the
  admin panel) **and** `ports: 2368/tcp` (Ghost directly, standalone). The
  blog front page is `/`, the admin panel `/ghost/` (the admin account is
  created by the user in the setup wizard on first visit; no credentials
  are baked into the add-on). `webui` also points at `/ghost/`, so the
  add-on's **Open Web UI** button opens the admin too; the front page
  remains available at the origin root and under the ingress base URL.
- **Access at the origin root.** Ghost builds its theme and core asset
  references relative to the site root (`getAssetUrl` resolves against the
  configured `url`'s path; an empty `url` falls back to
  `http://localhost:2368/`, which in a visitor's browser resolves to the
  visitor's own machine) and its in-page API calls from the configured
  `url`. The exposed port therefore serves the blog at the origin root
  (direct `http://<host>:2368` or a domain reverse-proxied onto it), with
  the `url` option set to that exact origin (the access protocol must
  match it: Ghost normalizes `http` requests to the `url`'s protocol with a
  301, so an `https` url should be reached through a TLS proxy). Home
  Assistant's ingress serves the add-on under a path prefix
  (`/api/hassio_ingress/<token>/`); the rewriter below makes the full blog
  work there as well, without changing the direct-port behavior.
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

### Ingress URL rewriting

- **Why it is needed.** Home Assistant Core's ingress proxy
  (`homeassistant/components/hassio/ingress.py`) strips the
  `/api/hassio_ingress/<token>` prefix before forwarding and announces it
  in `X-Ingress-Path`; the Supervisor forwards the request and that header
  to the add-on (`supervisor/api/ingress.py`). The token is a 128-hex
  session id (`secrets.token_hex(64)`) that changes with the ingress
  session, so it cannot be baked into Ghost's `url` option. Ghost emits
  root-relative references (`getAssetUrl` = the `url`'s subdirectory +
  path), and its admin app derives its root at runtime from
  `window.location.pathname` — the themes have no such logic, so the front
  page asked the browser for `/assets/...` at the Home Assistant host root
  and every static resource 404'd. A static `url` subdirectory cannot fix
  this because the prefix is per session.
- **Panel entry.** `ingress_entry: ghost/` makes the sidebar panel open
  Ghost's admin panel: the Supervisor builds the panel URL as the ingress
  URL plus this value (verified in `supervisor/apps/app.py`:
  `ingress_url` = `/api/hassio_ingress/<token>/` + `ingress_entry`). The
  value is relative without a leading slash — a leading slash would yield
  a double slash — and the trailing slash avoids Ghost's `/ghost` →
  `/ghost/` redirect. `webui` points at `/ghost/` as well. The rewriter
  still serves the whole blog under the prefix, so the front page remains
  reachable at the ingress base URL.
- **The rewriter.** A dependency-free Node reverse proxy
  (`/usr/local/bin/ghost-ingress-proxy`, started by the `ingress-proxy` s6
  service) listens on the add-on's ingress port **2369**. Ghost keeps
  listening on `0.0.0.0:2368`, which remains the exposed port, so direct
  access bypasses the proxy entirely and is byte-identical to before.
  The proxy is not a new image dependency: Node is already in the final
  image, and the script ships with the `root/` tree (`COPY root /`).
- **What is rewritten.** Only requests carrying a valid
  `X-Ingress-Path` (the pattern `/api/hassio_ingress/<[A-Za-z0-9_-]+>`; any
  other value is ignored, so a spoofed header cannot inject markup).
  `text/html`, `application/xhtml+xml` and `text/css` bodies get:
  every root-relative URL prefixed; absolute URLs on the configured site
  origin (canonical/og URLs, navigation, Ghost's `data-*` URL
  configuration such as Portal's `data-ghost`/`data-api`, the search and
  announcement-bar scripts' `data-*` attributes, inline `url()` and
  `@import`) rewritten to
  `<X-Forwarded-Proto>://<X-Forwarded-Host><prefix>/...` (Home Assistant
  Core supplies those headers; without them the rewrite falls back to a
  root-relative path). `srcset` candidates are rewritten individually.
  `Location` headers are rewritten the same way, including Ghost's
  redirects to the forwarded host. The admin panel needs no URL rewriting
  (its assets are document-relative and its API root comes from
  `window.location.pathname`), but its HTML passes through the same
  rewriter, where the admin theme synchronization injects its script (see
  Admin theme synchronization).
- **What is not rewritten.** JSON/API responses, uploads and other
  non-text types, responses with a `Content-Encoding` (the proxy asks
  upstream for `identity`; if Ghost still compresses, the body is passed
  through rather than corrupted), bodyless 204/304 responses, HEAD
  requests, and WebSocket upgrades (tunnelled). Requests without the
  ingress header never enter the rewriting path.
- **Session cookies.** Ghost scopes its admin session cookie to
  `<subdir>/ghost` (`express-session.js`: `path = urlUtils.getSubdir() +
  '/ghost'`, i.e. `/ghost` at the origin root), which never matches the
  ingress request paths (`<prefix>/ghost/...`). The browser therefore did
  not return the cookie on the next request, the admin session was lost,
  and email 2FA verification failed with HTTP 401 "Your verification code
  is incorrect" (`otp.verify` received `undefined` as the session user id;
  the emailed code itself is sent during the login request, which is why
  the email still arrived). The proxy rewrites the `Path` attribute of
  every non-root cookie on ingress responses (rewritten and pass-through
  alike) to `<prefix><path>`; root paths (`Path=/`) already match every
  path and are left alone, and an already-prefixed path is not rewritten
  twice. The prefix contains the add-on's persisted ingress token
  (`supervisor/apps/validate.py` defaults `ingress_token` through
  `secrets.token_urlsafe` and stores it once per install), so the scoped
  admin session survives sessions, Home Assistant restarts and add-on
  restarts; only a reinstall changes it. Cookies are left untouched for
  direct-port requests.
- **Request origin.** Home Assistant's HTTP middleware adds
  `Referrer-Policy: no-referrer` to every response
  (`homeassistant/components/http/headers.py`), including the ingress
  panel page, so the browser sends no `Referer` with the admin app's
  requests; same-origin GET requests carry no `Origin` either. Ghost 6
  additionally validates the origin of every admin request against the
  admin URL (`assertRequestFromAdminOrigin` in
  `core/server/auth/session/session-service.js`, login CSRF protection).
  The login/setup POST carries the Home Assistant origin in `Origin`,
  which is not the configured site URL, so Ghost answers
  `400 Request made from incorrect origin` and the panel cannot sign in.
  For ingress requests the proxy therefore presents the configured site
  origin to Ghost consistently: an incoming `Origin` is rewritten to the
  site origin, and `Referer` is set to `<site origin>/` (Home Assistant
  sends none). The session stores that origin when it is created, so the
  later session-origin checks match too. Without the `url` option the
  proxy falls back to the site default (`http://localhost:2368`), which
  is also the admin URL Ghost derives in that situation. Direct-port
  requests are untouched.
- **Response hygiene.** Rewritten responses drop the upstream `ETag`
  (the body differs per request), recompute `Content-Length`, add
  `Vary: X-Ingress-Path`, and are gzipped when the browser accepts it.
  Non-rewritten responses keep their `ETag`; Vary is still added under
  ingress.
- **Trust proxy side effect.** Ghost trusts only loopback
  (`app.set('trust proxy', 'loopback')`); the proxy connects from
  `127.0.0.1`, so Home Assistant's `X-Forwarded-Proto` now decides
  `req.secure` (the Supervisor's bridge address was not trusted).
- **Site origin.** The rewriter reads the add-on `url` option (published
  as the `url` container variable) and falls back to Ghost's default
  `http://localhost:2368`. With an empty option, root-relative references
  are still prefixed, so ingress browsing works; the origin only matters
  for absolute references.

### Admin theme synchronization (ingress only)

- **Why the auth screens need help.** Ghost 6's React admin ships a
  complete dark theme behind a `dark` class (the token block in
  `core/built/admin/assets/index-*.css`; `html.dark` also sets
  `color-scheme: dark`). The application resolves it from the signed-in
  user's `nightShift` preference (`useSettings` -> `setTheme`, toggling the
  class on `<html>`), so the sign-in, setup and reset screens — which have
  no user — render light. The default boot splash is also the light
  (white-background) logo video.
- **Scope.** Only ingress traffic (requests carrying a valid
  `X-Ingress-Path`) is themed. The exposed port bypasses the proxy
  entirely, so direct access is unchanged and keeps Ghost's own Night
  shift toggle.
- **Injection.** The ingress proxy, inside its existing HTML rewrite path,
  inlines `ha-admin-theme.js` immediately before `</head>` in a classic
  `<script data-ha-admin-theme>` tag (a separate asset request would delay
  first paint; the admin sets no CSP). The tag carries
  `data-dark-loader="assets/logo-loader-dark-<hash>.mp4"` when the build
  ships the dark boot-loader video, and
  `data-site-icon="assets/ha-admin-theme-logo.png"` when the add-on logo
  shipped.
  The admin document is recognised as a GET `text/html` response whose
  path contains the `/ghost/` segment and whose body carries the boot
  splash element (`id="boot-splash"`: Ghost 6's React index no longer has
  the Ember-era `ghost-admin/config/environment` meta tag), and which is
  not a HEAD, 204/304 or compressed response. The injection runs after
  `rewriteText`, so the URL rewriter never sees the injected script.
- **Startup discovery.** The script source (`GHOST_ADMIN_THEME_SCRIPT`,
  default `/usr/local/lib/ha-admin-theme/ha-admin-theme.js`) is read once
  when the proxy starts; when it is unreadable the proxy logs once and
  disables injection, and the add-on keeps serving everything else. The
  dark loader video (`logo-loader-dark-*.mp4` directly under
  `GHOST_ADMIN_ASSETS_DIR`, default
  `/opt/ghost/current/core/built/admin/assets` — Ghost 6 dropped the
  Ember-era `videos/` subdirectory) is optional: exactly one present means
  its path is stamped on the script tag, otherwise the attribute is omitted.
- **No Ghost theme state.** The script never reads the admin session
  cookie and never writes Ghost's `dark` class on `<html>` or its Night
  shift preference; the injected body is identical for every request. The
  proxy keeps the existing response behaviour (the ETag is dropped because
  the body is rewritten, the length is recomputed, `Vary: X-Ingress-Path`
  remains) and adds no cookie dependency.
- **Theme detection.** The script reads the HA theme from the same-origin
  ingress parent (`window.parent.getComputedStyle(documentElement)`):
  `--clear-background-color`, then `--primary-background-color`, parsed as
  `#rgb`/`#rrggbb`/`#rrggbbaa`/`rgb()`/`rgba()` and classified by WCAG
  relative luminance (dark below 0.5), so stock and custom HA themes work.
  When the parent is self, cross-origin or carries no parseable color, the
  script falls back to `prefers-color-scheme`.
- **Activation.** The script owns a private `ha-auth-dark` class on
  `<html>` and, while it is active, applies Ghost's own `dark` class to
  the **React root** (`#root`), not to `<html>`. Ghost's dark token block
  is `.dark { --... }`, so scoping it to `#root` themes every element
  inside the React tree with Ghost's current palette while leaving the
  application-owned `<html>` class untouched — there is no race with the
  app's theme controller. The private class adds `color-scheme: dark` and
  a dark body background for the boot splash, which lives outside `#root`.
  The theme is applied immediately at load (the boot splash precedes the
  React mount). The script detects the mount with the
  `react-admin-mounted` class the React shell adds to `<body>`; after that
  it keeps the theme only on explicit unauthenticated routes (`#/signin`,
  `#/signin/verify`, `#/setup`, `#/signup/...`, `#/reset/...`) and
  removes both markers on every other route, including the empty hash, so
  they cannot reach the panel. A logout (`#/signin`) re-applies it.
  Parent-theme mutations and `prefers-color-scheme` changes re-sync.
- **Post-login loading.** After the session POST succeeds Ghost keeps the
  sign-in form on screen with its button spinner while the user and
  preferences load. The hash is still an unauthenticated route, so the
  screen keeps the HA theme until the panel route commits. Because the
  script never touches Ghost's `<html>` class, whichever `nightShift`
  preference the application applies for the signed-in user takes effect
  in the panel without a flash or a race.
- **Critical stylesheet.** The script injects one idempotent `<style>`
  element holding the scoped marker rules (`html.ha-auth-dark`) and, when
  the proxy supplies the add-on logo, hides the React auth header's site
  icon (`header > img[alt=""]`) and draws the add-on logo in its place
  with a `::before` at the same 60 px size. The dark styling itself is
  Ghost's own `.dark` token block applied to `#root`; the add-on ships no
  copy of Ghost's palette, so a Ghost update cannot leave stale colors.
- **Boot loader.** Ghost 6's boot splash video is a light logo on white.
  In dark mode the script swaps in `logo-loader-dark-*.mp4` (and hides the
  light video when no dark asset exists), restoring the light source on
  authenticated routes.
- **What it cannot avoid.** If Ghost's `<html>` already carries `dark`
  (for example the last signed-in user's Night shift), the sign-in screen
  stays dark regardless of the HA theme — exactly as Ghost renders it; the
  add-on deliberately does not override the application's global theme.
  Everything the add-on controls inside `#root` otherwise follows the HA
  theme.
- **Files.** The proxy changes live in
  `root/usr/local/bin/ghost-ingress-proxy`; the browser script and the
  add-on logo are `root/usr/local/lib/ha-admin-theme/{ha-admin-theme.js,
  logo.png}`, shipped by the existing `COPY root /`. No manifest or
  direct-access change; the dark loader video is the one Ghost ships in
  the image.
- **Tests.** The proxy behavior tests cover: ingress admin HTML receiving
  the script, the dark loader attribute and the site-icon attribute; the
  site-icon asset served as `image/png` (GET and HEAD); the same document
  with and without the admin session cookie (no dependency); blog HTML,
  JSON, CSS and requests without ingress headers left untouched; the ETag
  dropped and `Vary: X-Ingress-Path` kept without a cookie key; injection
  disabled (the page still served) when the script is missing; and the
  dark loader or site-icon attribute omitted when its asset is absent.
  Node tests load the real script into a fake DOM and cover: color parsing
  (including space-separated `rgb()`) and the luminance threshold; HA
  parent versus `prefers-color-scheme` fallback; the React root receiving
  the dark class while Ghost's `<html>` state is never written; the boot
  splash themed on authenticated panel and empty-hash loads; the markers
  removed at boot on panel routes; logout re-application; the dark loader
  video swap, hide and restore; the site-icon rule present only when the
  logo is supplied; the scoped critical stylesheet; the single style
  element; and observer/media re-sync.

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
  shared `node` stage, limited to the 22 line — Ghost 6.73.0 requires
  `^22.23.1`, and the build only ships glibc Node 22 tarballs),
  `ghost-cli` (npm, builder stage only), `pnpm` (npm, builder stage only,
  installed at the version Ghost pins), and the installed Ghost
  application version (npm `ghost`, limited to the 6 line; a major jump is
  a reviewed change). A Node update PR
  changes `NODE_VERSION` only: the node stage downloads that release's
  `SHASUMS256.txt` from the same dist directory and verifies the
  architecture-specific tarball against the entry for its exact filename,
  so no per-architecture hash is hardcoded. The trade-off is explicit: the
  checksum now travels over the same TLS connection as the tarball, which
  still catches corrupted or truncated downloads but no longer pins the
  artifact against a compromised nodejs.org (the previous hardcoded hashes
  did, at the cost of a manual hash refresh on every Node bump that
  Renovate could not automate).

## Consequences

- The add-on works with zero external services (built-in SQLite, persisted
  in `/config/ghost`) and with an external MySQL 8 when configured.
- The published image is ~950 MB (the ~500 MB application tree ships in the
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
- Ghost is pinned to the 6.x line (6.73.0). Renovate proposes 6.x updates
  only; a major upgrade (7.x) is a reviewed change because it may move the
  Node engine, the package manager, the SQLite driver and the admin bundle
  again.
- Because bashio caches add-on options in `/tmp`, `tmpfs: true` is required
  for option changes to be picked up on container restart.

## Verification

- `tests/` (Python, Given-When-Then, 115 tests; run by CI before any build,
  plus 21 Node tests for the browser script):
  manifest/hassfest-mirror
  checks, Dockerfile structure (multi-stage layout with the Node pin in the
  shared `node` stage, build-time Ghost install into `/opt/ghost`, no Python
  in any stage, no package-install layer or npm/pnpm in the final stage,
  the pinned pnpm and the build-time better-sqlite3 verification, the pnpm
  store removed before handover, Node tarball hashes for both
  architectures),
  CI/renovate/lock consistency (incl. the renovate custom-manager pin
  extraction matched against the actual Dockerfile pins and the Node 22 /
  Ghost 6 version caps), behavior tests for the s6 scripts
  (first-boot volume setup with content seeding and config generation
  without a `database` section, legacy in-volume cleanup, the config-file
  migration of the add-on-generated `database` section — incl. the
  user-edited-file guard and the idempotent re-run — the better-sqlite3
  verification incl. the broken-image edge case, the sqlite and mysql
  option publishing (the mysql set without a `filename` variable),
  `log_console` on/off with stale-variable clearing, url/mail option
  publishing and clearing, service start), and behavior tests for the
  ingress rewriter against a fake Ghost (the real Node proxy is started in
  the test: direct access and non-HTML responses pass through untouched,
  root-relative attributes/`srcset`/CSS `url()`/`@import` are prefixed,
  site-origin absolute URLs and `Location` headers are rewritten to the
  session origin, external URLs and an invalid/spoofed ingress header are
  left alone, gzip and upstream-compressed bodies, 304 validators, HEAD,
  POST bodies/query strings, 502 on an unreachable upstream, ETag/Vary
  handling, and manifest/service/packaging consistency — incl. the
  `ingress_entry`/`webui` admin-panel entry and the ingress port matching
  the proxy default). The cookie tests cover the admin session path being
  scoped to the ingress prefix (the 2FA/login regression), root paths and
  already-prefixed paths being left alone, and direct access keeping
  Ghost's original cookie paths. The request-origin tests cover the
  Origin/Referer presented as the configured site origin (Ghost 6's
  admin-origin check), the default origin used without the `url` option,
  and direct access keeping the browser's headers untouched.
- End-to-end in Docker: an add-on container with the real s6 init against a
  fake supervisor — a fresh volume boots with the image-resident
  application, creates `content/data/ghost.db`, and serves `/` and
  `/ghost/` (HTTP 200) without any download; a restart boots in ~1 s with
  no error log entries; a legacy volume (522 MB, installed by an earlier
  add-on version) is migrated in place to the new layout (1.2 MB, content
  and database preserved); with `db_type: mysql` the same container runs
  the migrations against a real MySQL 8 container (102 tables) and creates
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
  `db_type: mysql` runs the migrations against a real MySQL 8 container
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
- Ghost 6.73.0 (2026-10-27, Docker e2e): the rebuilt image boots a fresh
  volume with the built-in better-sqlite3 database (Ghost 6 migrations,
  `/` and `/ghost/` both HTTP 200; a restart boots in ~1.4 s) and a
  freshly created volume works against MySQL 8.4 (102 tables, no
  `filename` warning); MariaDB 11.8 fails the migration with
  `ER_PARSE_ERROR` on the MySQL 8-only check constraints, which matches
  Ghost 6's documented MySQL-8-only support. The admin document under
  ingress is served with its relative asset paths untouched, the blog
  front page under ingress has its root-relative and site-origin
  references rewritten to the session prefix, and direct-port requests
  are byte-identical with no theme injection. A full browser session
  through a strip-prefix stand-in for the Home Assistant ingress layer
  (Chromium): setup and sign-in render dark with the add-on logo when the
  client prefers dark, the login POST is accepted with the `url` option
  pointing at a different origin than the browser, the 2FA email code is
  delivered through an SMTP relay and verified, and the authenticated
  panel loads with the add-on markers removed and Ghost's own theme
  class left to the app. The release is installed with pnpm and no Python
  is present in any stage; the image is ~950 MB.
- Admin theme synchronization (2026-10-27, automated): the Node suite
  loads the real browser script into a fake DOM (21 tests: HA parent
  versus `prefers-color-scheme`, color parsing in every supported form —
  including space-separated `rgb()` and invalid values — the luminance
  threshold, the loading screen on authenticated panel and empty-hash
  loads, the post-login spinner keeping the HA theme while Ghost toggles
  its own state, the class removal on panel routes and logout
  re-application, the dark loader video swap and hide, the site-icon rule,
  and the auth/loader CSS scoping; the React-root scoping keeping Ghost's
  own `<html>` theme state untouched). The proxy behavior tests (13 new
  tests; 115 Python tests in total) cover ingress script injection and the
  asset attributes, the served site-icon PNG (GET/HEAD), cookie
  independence, untouched direct access/blog HTML/JSON,
  ETag/`Vary: X-Ingress-Path`, idempotency, a spoofed ingress header, and
  disabled/partial injection when the script, video or logo is missing.
  Manual checks pending in a Home Assistant instance: the side panel under
  dark and light HA themes, the boot loader, the sign-in logo, sign-in to
  the panel, sign-out, the setup screen, and the direct 2368 port
  unchanged.
