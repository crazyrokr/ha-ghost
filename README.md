# Home Assistant Add-On: Ghost

![Supports aarch64 Architecture][aarch64-shield] ![Supports amd64 Architecture][amd64-shield]

## Summary

This add-on runs [Ghost](https://ghost.org/) — the headless content management
system — inside Home Assistant. On first start it installs a pinned Ghost
release into the persisted add-on config volume, then serves the blog and the
admin panel on port 2368, reachable from the add-on **Web UI**, the HA side
panel (ingress), or the exposed port.

By default the blog uses Ghost's built-in SQLite database, stored in the
persisted config volume — no separate database container needed. An external
MariaDB/MySQL database can be selected through the add-on options instead.

## Features

- **Self-installing.** The first boot installs Ghost 5.130.6 into
  `/config/ghost` (persisted across updates) and generates its production
  config; later boots simply start the existing blog.
- **SQLite by default, MariaDB if you prefer it.** The built-in SQLite file
  lives in the blog's content volume. Set `db_type` to `mysql` in the options
  to point the blog at an external MariaDB/MySQL database instead.
- **Two ways in.** Ingress side panel (no host port required) and/or the
  exposed 2368 port for standalone access.
- **Safe by design.** The blog runs as a dedicated non-root `ghost` user; the
  container runs under the standard Home Assistant `debian-base` image with
  s6 supervision.
- **Reproducible image.** The base image is digest-pinned and the Ghost CLI /
  Node versions are pinned in the build (kept current by Renovate).
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
   store — install and start it. The first start takes a few minutes
   (Ghost is downloaded and installed into the config volume).

3. **Create the admin account.** Open the add-on **Web UI** and visit
   `/ghost/` — the setup wizard asks for the admin name, email, and password.
   The blog front page is served at `/`.

4. **(Optional) External database.** In **Settings → add-on configuration**
   set `db_type` to `mysql` and fill in `db_host`, `db_port`, `db_user`,
   `db_password`, and `db_name`, then restart the add-on. Reverting to
   `sqlite` switches the blog back to its built-in database.

## Options

| Option       | Schema                          | Description                                                        |
| ------------ | ------------------------------- | ------------------------------------------------------------------ |
| `url`        | `str`                           | Public URL of the blog (used in links and sitemaps). Leave empty to let Ghost default. |
| `db_type`    | `choice(sqlite, mysql, default=sqlite)` | Database backend: the built-in SQLite file, or an external MariaDB/MySQL server. |
| `db_host`    | `str`                           | Host of the external database (required with `db_type: mysql`).    |
| `db_port`    | `int(3306)`                     | Port of the external database.                                      |
| `db_user`    | `str`                           | User of the external database (required with `db_type: mysql`).    |
| `db_password`| `str`                           | Password of the external database.                                  |
| `db_name`    | `str`                           | Name of the external database (required with `db_type: mysql`).    |

With `db_type: mysql` the add-on starts Ghost with the
`database__client=mysql` / `database__connection__*` environment variables
(the same mechanism the official Ghost docker image uses), which Ghost's
config loader merges above the generated config file. All of `db_host`,
`db_user`, and `db_name` must be set; the add-on refuses to start otherwise.

## Persistence

The whole blog (content, uploads, themes, SQLite database, config) lives in
the add-on config directory at `/config/ghost`, persisted across updates and
restarts. The `/share` mount is also available for extra data.

## Documentation

- [`docs/adr/0001-ghost-addon-architecture.md`](docs/adr/0001-ghost-addon-architecture.md) —
  architecture decision record: base image and version pins, first-boot
  install flow, SQLite default with prebuilt-binary self-healing, external
  database via add-on options, and the removal of the template leftovers.

## License

[MIT License](LICENSE)

[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
