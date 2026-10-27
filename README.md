# Home Assistant Add-On: Ghost

![Supports aarch64 Architecture][aarch64-shield] ![Supports amd64 Architecture][amd64-shield]

## Summary

This add-on runs [Ghost](https://ghost.org/) — the headless content management
system — inside Home Assistant. The pinned Ghost release ships inside the
add-on image; on first start the add-on only generates the blog config in the
persisted config volume, then serves the blog and the admin panel on port
2368, reachable from the add-on **Web UI**, the HA side panel (ingress), or
the exposed port.

By default the blog uses Ghost's built-in SQLite database, stored in the
persisted config volume — no separate database container needed. An external
MariaDB/MySQL database can be selected through the add-on options instead.

## Features

- **Fast first start.** The Ghost 5.130.6 application is pre-installed in the
  add-on image (no download at runtime, no Python in the final image); the
  first boot only generates the production config and starts the blog in a
  couple of seconds.
- **SQLite by default, MariaDB if you prefer it.** The built-in SQLite file
  lives in the blog's content volume. Set `db_type` to `mysql` in the options
  to point the blog at an external MariaDB/MySQL database instead.
- **Two ways in.** The exposed 2368 port for the blog (direct
  `http://<host>:2368`, or through a TLS reverse proxy on a domain) and the
  add-on's ingress side panel for the admin panel (append `/ghost/`).
- **Safe by design.** The blog runs as a dedicated non-root `ghost` user; the
  container runs under the standard Home Assistant `debian-base` image with
  s6 supervision.
- **Reproducible image.** The base image is digest-pinned and the Ghost /
  Ghost CLI / Node versions are pinned in the build (kept current by
  Renovate); the Ghost CLI is used at build time only.
- **Log in the UI.** The Ghost application log (boot, requests, errors)
  is visible in the add-on's **Log** tab; the log files themselves stay in
  the volume at `ghost/content/logs`. Set `log_console` to `false` to keep
  the log files but silence the console.
- **aarch64 & amd64.**

## How to Install

1. **Add the add-on repository.** In Home Assistant go to
   **Settings → Add-ons → Add-on store**, open the ⋮ menu in the top-right
   corner and choose **Repositories**, then add:

   ```text
   https://github.com/crazyrokr/ha-ghost
   ```

   and click **Reload**.

2. **Install the add-on.** The **Ghost** add-on now appears in the add-on
   store — install and start it. The first start takes a few seconds (the
   Ghost application ships inside the image; nothing is downloaded at
   runtime).

3. **Point the blog at its public origin.** In **Settings → add-on
   configuration** set `url` to the exact origin you browse the blog at
   (e.g. `http://192.168.1.10:2368` or `https://blog.example.com`), then
   restart the add-on — see [Access](#access).

4. **Create the admin account.** Open the blog at that origin and visit
   `/ghost/` — the setup wizard asks for the admin name, email, and
   password. The blog front page is served at `/`.

5. **(Optional) External database.** In **Settings → add-on configuration**
   set `db_type` to `mysql` and fill in `db_host`, `db_port`, `db_user`,
   `db_password`, and `db_name`, then restart the add-on. Reverting to
   `sqlite` switches the blog back to its built-in database.

6. **(Optional) Email delivery.** In **Settings → add-on configuration**
   set `mail_host`, `mail_port`, `mail_secure`, `mail_user`,
   `mail_password` of your SMTP relay plus `mail_from` (the sender address
   your provider accepts), then restart the add-on — member magic links,
   password resets, and notifications are then delivered through that
   relay.

## Access

Ghost serves its theme and core assets relative to the site root and builds
its links, sitemaps, and in-page API references from the `url` option, so
the blog must be reachable at an origin root — scheme, host, port — without
a path prefix:

- **Exposed port (recommended for the blog).** Browse the blog directly at
  `http://<host>:2368`, or put a TLS reverse proxy (Traefik, nginx, …) on a
  domain that forwards to the add-on's port, and set `url` to that exact
  origin. Without `url`, Ghost falls back to `http://localhost:2368/`,
  whose references resolve to the visitor's own machine. The option is
  applied on every start, so changing it later needs only a restart; the
  access protocol must match it (Ghost redirects `http` requests to the
  `url`'s protocol).
- **Ingress side panel.** Home Assistant serves the add-on under a path
  prefix; the admin panel (`/ghost/`) renders there, but the front page's
  root-relative asset references cannot — this is a Ghost limitation, not
  an add-on defect. Use the exposed port for the blog itself.

## Options

| Option       | Schema                          | Description                                                        |
| ------------ | ------------------------------- | ------------------------------------------------------------------ |
| `url`        | `str`                           | The exact origin visitors use to reach the blog (e.g. `https://blog.example.com` or `http://192.168.1.10:2368`). See [Access](#access). |
| `db_type`    | `choice(sqlite, mysql, default=sqlite)` | Database backend: the built-in SQLite file, or an external MariaDB/MySQL server. |
| `db_host`    | `str`                           | Host of the external database (required with `db_type: mysql`).    |
| `db_port`    | `int(3306)`                     | Port of the external database.                                      |
| `db_user`    | `str`                           | User of the external database (required with `db_type: mysql`).    |
| `db_password`| `password`                      | Password of the external database.                                  |
| `db_name`    | `str`                           | Name of the external database (required with `db_type: mysql`).    |
| `mail_transport`| `choice(SMTP, direct, default=SMTP)` | Mail delivery: via the SMTP relay from the options, or Ghost's built-in direct sending. |
| `mail_host`  | `str`                           | Host of the SMTP relay (enables mail delivery; without it the built-in transport is kept). |
| `mail_port`  | `int(587)`                      | Port of the SMTP relay.                                             |
| `mail_secure`| `bool`                          | Use TLS for the SMTP connection (true for port 465).               |
| `mail_user`  | `str`                           | SMTP login user (optional).                                        |
| `mail_password`| `password`                    | SMTP login password (optional).                                     |
| `mail_from`  | `str`                           | Sender address, e.g. `Your Blog <noreply@yourdomain.com>` (Ghost falls back to a generated one without it). |
| `log_console`| `bool`                          | Also print the application log to the container console (the add-on **Log** tab). `false` keeps the log files in the volume only. |

With `db_type: mysql` the add-on starts Ghost with the
`database__client=mysql` / `database__connection__*` environment variables
(the same mechanism the official Ghost docker image uses), which Ghost's
config loader merges above the generated config file. All of `db_host`,
`db_user`, and `db_name` must be set; the add-on refuses to start
otherwise. The generated config file carries no `database` section, and
the built-in SQLite database is configured through the same container
environment (`database__client=sqlite3`,
`database__connection__filename`) — Ghost's config loader would otherwise
merge the file's sqlite section into an external database connection and
leak the `filename` option to it, which the mysql2 driver warns about.
Volumes created by earlier add-on versions are migrated in place on the
next start (only the add-on-generated section is removed; a hand-edited
config is left as it is).

With `mail_host` set the add-on starts Ghost with the `mail__transport`,
`mail__options__*` (host, port, TLS, authentication) and `mail__from`
environment variables, merged the same way; without it the built-in direct
transport is kept (with `mail__from` if set). Point these at the SMTP relay
your host or provider gives you so that member magic links, password
resets, and notifications can be delivered.

## Persistence

The blog's data (content, uploads, themes, SQLite database) and its config
live in the add-on config directory at `/config/ghost`, persisted across
updates and restarts — typically a couple of megabytes, since the
application itself stays inside the image. The `/share` mount is also
available for extra data.

## Documentation

- [`docs/adr/0001-ghost-addon-architecture.md`](docs/adr/0001-ghost-addon-architecture.md) —
  architecture decision record: multi-stage image build with the Ghost
  application pre-installed (no Python in the final image), first-boot
  volume preparation, SQLite default with a prebuilt binary baked in at
  build time, external database via add-on options, and the removal of the
  template leftovers.

## License

[MIT License](LICENSE)

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
