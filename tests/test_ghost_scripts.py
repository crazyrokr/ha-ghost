"""Behavior tests for the add-on s6 service scripts (Given-When-Then).

The scripts run under the HA debian-base image where s6 invokes them
with the container environment and '/command/with-contenv' on PATH.
The Ghost application ships inside the image (GHOST_APP_DIR); the
persisted volume holds only the user's blog data. Here the scripts are
executed with the same interpreter the image would use (bashio-based
scripts under bash, plain scripts under sh), the same environment
exported, and 'ghost'/'node'/'runuser' replaced by stubs that record
their invocation (the fake image app is a real directory tree). This
keeps the tests hermetic (no network, no image build, no root).
"""

import json
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ROOT = REPO_ROOT / "ghost" / "root"
CONT_INIT = ROOT / "etc" / "cont-init.d" / "99-ghost"
SERVICE_RUN = ROOT / "etc" / "services.d" / "ghost" / "run"

# Stands in for the bashio library: reports add-on options from STUB_*
# environment variables. Like the real bashio (which uses jq -r), a
# missing option comes back as the literal string "null".
BASHIO_STUB = textwrap.dedent(
    """\
    bashio::config() {
        case "$1" in
            url)        printf '%s' "${STUB_URL:-null}" ;;
            db_type)    printf '%s' "${STUB_DB_TYPE:-null}" ;;
            db_host)    printf '%s' "${STUB_DB_HOST:-}" ;;
            db_port)    printf '%s' "${STUB_DB_PORT:-null}" ;;
            db_user)    printf '%s' "${STUB_DB_USER:-}" ;;
            db_password) printf '%s' "${STUB_DB_PASSWORD:-}" ;;
            db_name)    printf '%s' "${STUB_DB_NAME:-}" ;;
            mail_transport) printf '%s' "${STUB_MAIL_TRANSPORT:-null}" ;;
            mail_host)    printf '%s' "${STUB_MAIL_HOST:-}" ;;
            mail_port)    printf '%s' "${STUB_MAIL_PORT:-null}" ;;
            mail_secure)  printf '%s' "${STUB_MAIL_SECURE:-false}" ;;
            mail_user)    printf '%s' "${STUB_MAIL_USER:-}" ;;
            mail_password) printf '%s' "${STUB_MAIL_PASSWORD:-}" ;;
            mail_from)    printf '%s' "${STUB_MAIL_FROM:-}" ;;
            log_console)  printf '%s' "${STUB_LOG_CONSOLE:-null}" ;;
        esac
        return 0
    }
    """
)


@pytest.fixture(name="sandbox")
def sandbox_fixture(tmp_path):
    # Given a fake container filesystem
    # When the test sandbox is prepared
    # Then the config volume and container environment exist and the
    # command stubs record their calls
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    blog_dir = config_dir / "ghost"
    container_env = tmp_path / "s6env"
    container_env.mkdir()

    # The Ghost CLI is never invoked at runtime (the app is installed at
    # build time); if a script tried, the boot must fail.
    ghost_stub = tmp_path / "ghost"
    ghost_stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo "ghost $@" >> "$CALL_LOG"
            exit 1
            """
        )
    )
    # Like the real node: requiring the sqlite3 module succeeds only when the
    # package (marker file) is actually present in the referenced app tree.
    # The scripts also run node for the config-file migration, which must
    # execute for real: the stub therefore hands the invocation to the
    # host's node when one is available (the fake package above resolves
    # under a real node exactly like the image's module), and emulates the
    # module check from the marker otherwise.
    host_node = shutil.which("node")
    if host_node:
        node_stub_body = (
            '#!/bin/sh\n'
            'echo "node cwd=$(pwd) NODE_ENV=${NODE_ENV:-} $@" >> "$CALL_LOG"\n'
            f'exec "{host_node}" "$@"\n'
        )
    else:
        node_stub_body = textwrap.dedent(
            """\
            #!/bin/sh
            echo "node cwd=$(pwd) NODE_ENV=${NODE_ENV:-} $@" >> "$CALL_LOG"
            for a in "$@"; do
                case "$a" in
                    *node_modules/sqlite3*)
                        ROOT="${a%%node_modules/*}"
                        case "$ROOT" in
                            *require\\(*) ROOT="${ROOT#*require\\(}" ;;
                        esac
                        ROOT="${ROOT#\\'}"
                        test -f "${ROOT}node_modules/sqlite3/lib/index.js" || exit 1
                        ;;
                esac
            done
            exit 0
            """
        )
    node_stub = tmp_path / "node"
    node_stub.write_text(node_stub_body)
    # The Ghost application as installed in the image: versions/ tree with
    # the sqlite3 module baked in, the current/ symlink, and the .ghost-cli
    # version marker.
    app_dir = tmp_path / "app"
    app_version = app_dir / "versions" / "5.130.6"
    (app_version / "node_modules" / "sqlite3" / "lib").mkdir(parents=True)
    (app_version / "node_modules" / "sqlite3" / "lib" / "index.js").write_text(
        "module.exports = 1\n", encoding="utf-8"
    )
    # A real node (the stub's delegate, see below) resolves a package
    # directory through its manifest; without one it would look for
    # index.js at the package root and report the module missing even with
    # the marker present.
    (app_version / "node_modules" / "sqlite3" / "package.json").write_text(
        '{"name": "sqlite3", "version": "5.1.7", "main": "lib/index.js"}\n',
        encoding="utf-8",
    )
    (app_version / "index.js").write_text("console.log('x')", encoding="utf-8")
    (app_version / "content" / "data").mkdir(parents=True)
    (app_version / "content" / "themes" / "casper").mkdir(parents=True)
    (app_dir / "current").symlink_to(app_version)
    # The image's application carries the content tree the installer created
    # for it (subdirectories plus the linked built-in themes).
    (app_dir / "content" / "data").mkdir(parents=True)
    (app_dir / "content" / "themes" / "casper").mkdir(parents=True)
    (app_dir / ".ghost-cli").write_text(
        '{"active-version": "5.130.6", "cli-version": "1.33.7", "channel": "stable"}',
        encoding="utf-8",
    )
    runuser_stub = tmp_path / "runuser"
    runuser_stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            # usage: runuser -u USER -- CMD...
            USER_ARG="$2"
            shift 3
            echo "runuser ${USER_ARG} $*" >> "$CALL_LOG"
            exec "$@"
            """
        )
    )
    for stub in (ghost_stub, node_stub, runuser_stub):
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "CALL_LOG": str(tmp_path / "calls.log"),
        "GHOST_BLOG_DIR": str(blog_dir),
        "GHOST_APP_DIR": str(app_dir),
        "CONTAINER_ENV_DIR": str(container_env),
        "HOME": str(tmp_path / "root"),
        "HOST_NODE": host_node or "",
    }
    return tmp_path, config_dir, blog_dir, container_env, env


def strip_shebang(script: Path):
    # The image provides /command/with-contenv; on a dev host the same
    # script body runs with the environment exported directly.
    return [
        line for line in script.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#!")
    ]


def run_script(script: Path, env: dict, tmp_path: Path, shell: str = "sh"):
    # The bashio-based cont-init script runs under bash in the image
    # (its shebang is /command/with-contenv bashio); the service script
    # runs under sh.
    body = BASHIO_STUB + "\n".join(strip_shebang(script)) + "\n"
    body_file = tmp_path / "script_body.sh"
    body_file.write_text(body, encoding="utf-8")
    result = subprocess.run(
        [shell, str(body_file)], env=env, capture_output=True, text=True, timeout=30
    )
    return result, read_calls(tmp_path / "calls.log")


def read_calls(call_log: Path):
    if not call_log.is_file():
        return []
    return [line for line in call_log.read_text().splitlines() if line]


def ghost_calls(calls):
    return [c for c in calls if c.startswith("ghost ")]


def node_calls(calls):
    return [c for c in calls if c.startswith("node ")]


def database_env_files(container_env: Path):
    return sorted(p.name for p in container_env.glob("database__*"))


def mail_env_files(container_env: Path):
    return sorted(p.name for p in container_env.glob("mail__*"))


def env_value(container_env: Path, name: str):
    return (container_env / name).read_text() if (container_env / name).is_file() else None


# ---------------------------------------------------------------- cont-init

def test_scripts_exist():
    # Given the add-on ships its service definitions in root/
    # When the expected script paths are checked
    # Then the cont-init and service run scripts exist
    assert CONT_INIT.is_file(), f"missing {CONT_INIT}"
    assert SERVICE_RUN.is_file(), f"missing {SERVICE_RUN}"


def test_scripts_are_executable():
    # Given s6 executes the scripts directly (via their shebang)
    # When their file mode is checked
    # Then both scripts carry the executable bit (COPY root / preserves
    # the mode; a non-executable script aborts the container at boot)
    for script in (CONT_INIT, SERVICE_RUN):
        mode = script.stat().st_mode
        assert mode & 0o111, f"{script.name} must be executable (mode {oct(mode)})"


def test_cont_sets_up_blog_on_first_boot(sandbox):
    # Given no Ghost blog exists in the config volume yet (first boot),
    # while the Ghost application already ships in the image
    # When the cont-init script runs
    # Then it never invokes the Ghost CLI (the app is pre-installed, no
    # download needed), generates the production config bound to all
    # interfaces on port 2368 without a 'database' section, publishes the
    # built-in sqlite database through the container environment, and
    # prepares the content directory
    _, _, blog_dir, container_env, env = sandbox
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not ghost_calls(calls), (
        f"the Ghost CLI must not run at runtime (app ships in the image): {calls}"
    )
    assert (blog_dir / "content" / "data").is_dir(), (
        "the content subdirectories must be seeded from the image's application"
    )
    current = blog_dir / "current"
    assert current.is_symlink(), "the blog's current/ must be a symlink into the image's app"
    assert current.resolve() == (Path(env["GHOST_APP_DIR"]) / "versions" / "5.130.6").resolve(), (
        "current/ must resolve to the image's application"
    )

    config = (blog_dir / "config.production.json").read_text()
    assert '"host": "0.0.0.0"' in config, f"config must bind all interfaces: {config}"
    assert '"port": 2368' in config
    assert '"database"' not in config, (
        "the config file must carry no 'database' section (the database is "
        "configured through the container environment; a file-borne sqlite "
        "section would leak 'filename' into an external database connection): "
        + config
    )
    assert f'"contentPath": "{blog_dir}/content/"' in config, (
        "content must live in the persisted blog directory: " + config
    )
    assert '"url": "null"' not in config, (
        "bashio's 'null' placeholder must be normalized to an absent url: " + config
    )
    assert env_value(container_env, "database__client") == "sqlite3", (
        "the built-in sqlite database must be published through the "
        "container environment"
    )
    assert env_value(container_env, "database__connection__filename") == (
        f"{blog_dir}/content/data/ghost.db"
    ), "the sqlite file must be absolute"


def test_cont_generates_config_with_url_option(sandbox):
    # Given no blog exists yet and the add-on option 'url' is set
    # When the cont-init script runs
    # Then the generated config carries the public url for Ghost and the
    # url option is published as the container environment variable
    _, _, blog_dir, container_env, env = sandbox
    env = {**env, "STUB_URL": "https://blog.example.com"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    config = (blog_dir / "config.production.json").read_text()
    assert '"url": "https://blog.example.com"' in config, config
    assert env_value(container_env, "url") == "https://blog.example.com"


def test_cont_removes_legacy_app_from_volume(sandbox):
    # Given a volume created by an older add-on version, which installed
    # the Ghost app into the volume (versions/ tree, current/ symlink,
    # .ghost-cli marker) alongside the user's content and config
    # When the cont-init script runs
    # Then the legacy app files are removed (the app now ships in the
    # image) while the user's content and config survive
    _, _, blog_dir, _, env = sandbox
    (blog_dir / "content" / "data").mkdir(parents=True)
    (blog_dir / "content" / "data" / "ghost.db").write_bytes(b"old-db")
    legacy = blog_dir / "versions" / "5.130.6"
    legacy.mkdir(parents=True)
    (legacy / "stale-file").write_text("x", encoding="utf-8")
    (blog_dir / "current").symlink_to(legacy)
    (blog_dir / ".ghost-cli").write_text('{"active-version": "5.130.6"}', encoding="utf-8")
    (blog_dir / "config.production.json").write_text('{"user_edit": true}', encoding="utf-8")

    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not (blog_dir / "versions").exists(), "legacy versions/ must be removed"
    assert not (blog_dir / ".ghost-cli").exists(), "legacy .ghost-cli must be removed"
    assert (blog_dir / "content" / "data" / "ghost.db").read_bytes() == b"old-db", (
        "the user's database must survive the cleanup"
    )
    assert (blog_dir / "config.production.json").read_text() == '{"user_edit": true}'
    # The legacy current/ symlink is repointed at the image's application.
    current = blog_dir / "current"
    assert current.is_symlink(), "current/ must be a symlink"
    assert current.resolve() == (Path(env["GHOST_APP_DIR"]) / "versions" / "5.130.6").resolve(), (
        "current/ must now resolve to the image's application"
    )
    # The cleanup must not touch the app shipped in the image.
    image_app_module = Path(env["GHOST_APP_DIR"]) / "current" / "node_modules" / "sqlite3" / "lib" / "index.js"
    assert image_app_module.is_file(), "the image's app must survive the volume cleanup"


def test_cont_does_not_rewrite_existing_config(sandbox):
    # Given a blog already exists (config present) and the user changed
    # options
    # When the cont-init script runs
    # Then the existing config file is not touched (user edits to the
    # config must survive restarts)
    _, _, blog_dir, _, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "content").mkdir()
    (blog_dir / "config.production.json").write_text('{"user_edit": true}', encoding="utf-8")
    env = {**env, "STUB_URL": "https://blog.example.com"}
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not ghost_calls(calls), f"the CLI must not run for an existing blog: {calls}"
    assert (blog_dir / "config.production.json").read_text() == '{"user_edit": true}'


def test_cont_publishes_mysql_env_when_selected(sandbox):
    # Given the add-on options select an external MariaDB
    # (db_type=mysql plus host, user, password, database name)
    # When the cont-init script runs
    # Then the container environment carries the mysql database__*
    # variables Ghost's config loader merges above config files - and no
    # 'filename' variable, which would leak into the mysql connection
    # (the config file carries no database section either)
    _, _, _, container_env, env = sandbox
    env = {
        **env,
        "STUB_DB_TYPE": "mysql",
        "STUB_DB_HOST": "db.example.com",
        "STUB_DB_PORT": "3307",
        "STUB_DB_USER": "ghost_user",
        "STUB_DB_PASSWORD": "secret",
        "STUB_DB_NAME": "ghost_db",
    }
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert database_env_files(container_env) == [
        "database__client",
        "database__connection__database",
        "database__connection__host",
        "database__connection__password",
        "database__connection__port",
        "database__connection__user",
    ], database_env_files(container_env)
    assert env_value(container_env, "database__client") == "mysql"
    assert env_value(container_env, "database__connection__host") == "db.example.com"
    assert env_value(container_env, "database__connection__port") == "3307"
    assert env_value(container_env, "database__connection__user") == "ghost_user"
    assert env_value(container_env, "database__connection__password") == "secret"
    assert env_value(container_env, "database__connection__database") == "ghost_db"


def test_cont_clears_stale_database_env_on_sqlite(sandbox):
    # Given a previous start published mysql database variables, but the
    # options now select sqlite again (or the options were reset)
    # When the cont-init script runs
    # Then the stale mysql variables are cleared and the built-in sqlite
    # database is published instead
    _, _, _, container_env, env = sandbox
    (container_env / "database__client").write_text("mysql", encoding="utf-8")
    (container_env / "database__connection__host").write_text("db.example.com", encoding="utf-8")
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert database_env_files(container_env) == [
        "database__client",
        "database__connection__filename",
    ], (
        f"stale mysql env must be cleared and the sqlite database published: "
        f"{database_env_files(container_env)}"
    )
    assert env_value(container_env, "database__client") == "sqlite3"


def test_cont_publishes_url_env_for_existing_config(sandbox):
    # Given a blog already exists (its config was generated on the first
    # boot before the url option was set, so the file carries no url) and
    # the user has now set the url option
    # When the cont-init script runs
    # Then the url option is published as the container environment
    # variable 'url', which Ghost's config loader merges above the config
    # file; the existing config file itself is not rewritten
    _, _, blog_dir, container_env, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "content").mkdir()
    (blog_dir / "config.production.json").write_text('{"no_url": true}', encoding="utf-8")
    env = {**env, "STUB_URL": "https://blog.example.com"}
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert env_value(container_env, "url") == "https://blog.example.com"
    assert not ghost_calls(calls), f"the CLI must not run for an existing blog: {calls}"
    assert (blog_dir / "config.production.json").read_text() == '{"no_url": true}'


def test_cont_migrates_generated_database_section_from_existing_config(sandbox):
    # Given a blog whose config was generated by an earlier add-on version
    # (it carries the built-in sqlite 'database' section in the file), and
    # the user edited another part of the file since
    # When the cont-init script runs
    # Then the add-on-generated 'database' section is removed from the file
    # (Ghost's config loader would merge it into an external database
    # connection and leak the sqlite 'filename' option to it, where the
    # mysql2 driver warns about it and will reject it in a future release)
    # while the user's edit survives, and the built-in database is
    # published through the container environment instead
    _, _, blog_dir, container_env, env = sandbox
    if not env.get("HOST_NODE"):
        pytest.skip("no node on the test host: the config migration cannot execute")
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "content").mkdir()
    (blog_dir / "config.production.json").write_text(
        json.dumps(
            {
                "server": {"host": "0.0.0.0", "port": 2368},
                "logging": {"level": "DEBUG"},
                "paths": {"contentPath": f"{blog_dir}/content/"},
                "database": {
                    "client": "sqlite3",
                    "connection": {"filename": f"{blog_dir}/content/data/ghost.db"},
                    "settings": {"busyRetryMax": 10000},
                    "migrate": True,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    migrated = json.loads((blog_dir / "config.production.json").read_text())
    assert "database" not in migrated, (
        "the add-on-generated 'database' section must be removed from the file: "
        + json.dumps(migrated)
    )
    assert migrated["logging"] == {"level": "DEBUG"}, "user edits must survive the migration"
    assert migrated["server"] == {"host": "0.0.0.0", "port": 2368}
    assert migrated["paths"] == {"contentPath": f"{blog_dir}/content/"}
    assert env_value(container_env, "database__client") == "sqlite3"
    assert env_value(container_env, "database__connection__filename") == (
        f"{blog_dir}/content/data/ghost.db"
    )


def test_cont_migrates_generated_database_section_only_once(sandbox):
    # Given a config file that was already migrated (no 'database' section,
    # re-serialized by the migration)
    # When the cont-init script runs again
    # Then the file is not rewritten (the migration is a no-op)
    _, _, blog_dir, _, env = sandbox
    if not env.get("HOST_NODE"):
        pytest.skip("no node on the test host: the config migration cannot execute")
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "content").mkdir()
    original = json.dumps(
        {
            "server": {"host": "0.0.0.0", "port": 2368},
            "logging": {"level": "DEBUG"},
            "paths": {"contentPath": f"{blog_dir}/content/"},
        },
        indent=2,
    )
    (blog_dir / "config.production.json").write_text(original + "\n", encoding="utf-8")
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert (blog_dir / "config.production.json").read_text() == original + "\n", (
        "an already migrated config must not be rewritten"
    )


def test_cont_leaves_user_edited_database_section_alone(sandbox):
    # Given a blog whose config file carries a 'database' section the user
    # pointed at a custom sqlite file (not the add-on-generated default)
    # When the cont-init script runs
    # Then the file is not rewritten (only the add-on-generated section is
    # migrated; a hand-edited config belongs to the user)
    _, _, blog_dir, _, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "content").mkdir()
    original = json.dumps(
        {
            "logging": {"level": "INFO"},
            "database": {
                "client": "sqlite3",
                "connection": {"filename": f"{blog_dir}/content/data/custom.db"},
            },
        },
        indent=2,
    )
    (blog_dir / "config.production.json").write_text(original + "\n", encoding="utf-8")
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert (blog_dir / "config.production.json").read_text() == original + "\n", (
        "a hand-edited 'database' section must not be migrated"
    )


def test_cont_publishes_smtp_env_when_host_configured(sandbox):
    # Given the add-on options configure an SMTP relay (transport, host,
    # non-default port, TLS, credentials and the from address)
    # When the cont-init script runs
    # Then the container environment carries the mail settings Ghost's
    # config loader merges above the config file
    _, _, _, container_env, env = sandbox
    env = {
        **env,
        "STUB_MAIL_TRANSPORT": "SMTP",
        "STUB_MAIL_HOST": "smtp.example.com",
        "STUB_MAIL_PORT": "465",
        "STUB_MAIL_SECURE": "true",
        "STUB_MAIL_USER": "me@example.com",
        "STUB_MAIL_PASSWORD": "secret",
        "STUB_MAIL_FROM": "Ghost Blog <noreply@example.com>",
    }
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert mail_env_files(container_env) == [
        "mail__from",
        "mail__options__auth__pass",
        "mail__options__auth__user",
        "mail__options__host",
        "mail__options__port",
        "mail__options__secure",
        "mail__transport",
    ], mail_env_files(container_env)
    assert env_value(container_env, "mail__transport") == "SMTP"
    assert env_value(container_env, "mail__options__host") == "smtp.example.com"
    assert env_value(container_env, "mail__options__port") == "465"
    assert env_value(container_env, "mail__options__secure") == "true"
    assert env_value(container_env, "mail__options__auth__user") == "me@example.com"
    assert env_value(container_env, "mail__options__auth__pass") == "secret"
    assert env_value(container_env, "mail__from") == "Ghost Blog <noreply@example.com>"


def test_cont_publishes_smtp_without_credentials_when_host_set(sandbox):
    # Given only the SMTP host (and port) is configured - no TLS, no
    # credentials, no from address
    # When the cont-init script runs
    # Then the transport and connection variables are published, but no
    # secure/authentication variables (Ghost/nodemailer defaults apply)
    _, _, _, container_env, env = sandbox
    env = {**env, "STUB_MAIL_HOST": "smtp.example.com", "STUB_MAIL_PORT": "25"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert mail_env_files(container_env) == [
        "mail__options__host",
        "mail__options__port",
        "mail__transport",
    ], mail_env_files(container_env)
    assert env_value(container_env, "mail__transport") == "SMTP"
    assert env_value(container_env, "mail__options__port") == "25"


def test_cont_publishes_only_from_without_host(sandbox):
    # Given only the from address is configured (no SMTP host)
    # When the cont-init script runs
    # Then only mail__from is published: the built-in direct transport is
    # kept, with the configured sender address
    _, _, _, container_env, env = sandbox
    env = {**env, "STUB_MAIL_FROM": "Ghost Blog <noreply@example.com>"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert mail_env_files(container_env) == ["mail__from"], mail_env_files(container_env)
    assert env_value(container_env, "mail__from") == "Ghost Blog <noreply@example.com>"


def test_cont_clears_stale_mail_env_when_options_reset(sandbox):
    # Given a previous start published mail variables (SMTP host and
    # from address), but the options were reset since
    # When the cont-init script runs
    # Then all mail variables are cleared (the built-in direct transport
    # with the app-default from address takes effect again)
    _, _, _, container_env, env = sandbox
    for name, value in (
        ("mail__transport", "SMTP"),
        ("mail__options__host", "smtp.example.com"),
        ("mail__from", "Ghost Blog <noreply@example.com>"),
    ):
        (container_env / name).write_text(value, encoding="utf-8")
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert mail_env_files(container_env) == [], (
        f"stale mail env must be cleared: {mail_env_files(container_env)}"
    )


def test_cont_publishes_logging_transports_env(sandbox):
    # Given Ghost's production default logs to file only (so the add-on
    # log tab would show no application log at all)
    # When the cont-init script runs
    # Then the container environment carries the transports stdout,
    # stderr and file, so the application log is printed to the
    # container console (the add-on log tab) as well as to the files
    _, _, _, container_env, env = sandbox
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert env_value(container_env, "logging__transports") == '["stdout","stderr","file"]'


def test_cont_publishes_logging_transports_when_console_explicitly_enabled(sandbox):
    # Given the log_console option is explicitly set to true (the default)
    # When the cont-init script runs
    # Then the stdout, stderr and file transports are published
    _, _, _, container_env, env = sandbox
    env = {**env, "STUB_LOG_CONSOLE": "true"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert env_value(container_env, "logging__transports") == '["stdout","stderr","file"]'


def test_cont_clears_logging_transports_when_console_disabled(sandbox):
    # Given a previous start published the console transports, but the
    # log_console option is now false
    # When the cont-init script runs
    # Then the transports variable is cleared (the application default of
    # file-only logging applies; the log files keep being written to the
    # volume)
    _, _, _, container_env, env = sandbox
    (container_env / "logging__transports").write_text(
        '["stdout","stderr","file"]', encoding="utf-8"
    )
    env = {**env, "STUB_LOG_CONSOLE": "false"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not (container_env / "logging__transports").exists(), (
        "the transports variable must be cleared when console logging is disabled"
    )


def test_cont_clears_stale_url_env_when_option_empty(sandbox):
    # Given a previous start published a url variable, but the url option
    # is now empty (or was reset)
    # When the cont-init script runs
    # Then the stale url variable is cleared (Ghost falls back to its own
    # default instead of a stale origin)
    _, _, _, container_env, env = sandbox
    (container_env / "url").write_text("https://stale.example.com", encoding="utf-8")
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not (container_env / "url").exists(), "the stale url env must be cleared"


def test_cont_fails_on_incomplete_mysql_options(sandbox):
    # Given db_type is mysql but the user forgot credentials
    # When the cont-init script runs
    # Then it exits non-zero instead of starting a half-configured blog
    _, _, _, _, env = sandbox
    env = {
        **env,
        "STUB_DB_TYPE": "mysql",
        "STUB_DB_HOST": "db.example.com",
        "STUB_DB_NAME": "ghost_db",
    }
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode != 0, "incomplete mysql options must fail the boot"
    assert database_env_files(sandbox[3]) == [], "no partial env may be published"


def test_cont_fails_when_image_app_lacks_sqlite3(sandbox):
    # Given the image's application is missing the sqlite3 module (a broken
    # image build: the module is baked in at build time, verified there)
    # When the cont-init script runs
    # Then it exits non-zero (without the driver Ghost would exit silently
    # and the s6 service would crash-loop without any log output)
    _, _, blog_dir, _, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    module = Path(env["GHOST_APP_DIR"]) / "versions" / "5.130.6" / "node_modules" / "sqlite3"
    for path in module.rglob("*"):
        if path.is_file():
            path.unlink()
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode != 0, "boot must fail when the image app lacks the sqlite3 module"
    assert not [c for c in calls if c.startswith("npm ")], (
        f"no runtime package installs are expected: {calls}"
    )


# ------------------------------------------------------------------ service

def test_service_runs_ghost_with_production_node(sandbox):
    # Given a configured blog (config.production.json present) and the
    # Ghost application shipped in the image
    # When the s6 run script starts
    # Then it executes the image's entry point as the ghost user with
    # NODE_ENV=production from the blog directory (Ghost resolves the
    # config file from the working directory)
    _, _, blog_dir, _, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    # The cont-init script created the blog's current/ symlink into the
    # image's application (Ghost's root detection requires it).
    (blog_dir / "current").symlink_to(Path(env["GHOST_APP_DIR"]) / "current")
    result, calls = run_script(SERVICE_RUN, env, sandbox[0])
    assert any(c.startswith("runuser ghost node ") for c in calls), (
        f"ghost must run as the ghost user: {calls}"
    )
    calls = node_calls(calls)
    assert calls, f"node was never started: {calls}"
    assert f"cwd={blog_dir}" in calls[0], f"wrong working directory: {calls[0]}"
    assert "NODE_ENV=production" in calls[0], f"missing NODE_ENV=production: {calls[0]}"
    assert calls[0].endswith("current/index.js"), (
        f"the blog's current/ entry point must be started: {calls[0]}"
    )


def test_service_fails_when_blog_missing(sandbox):
    # Given no Ghost blog exists (install never happened)
    # When the s6 run script starts
    # Then it exits non-zero so s6 can report the failure
    _, _, _, _, env = sandbox
    result, calls = run_script(SERVICE_RUN, env, sandbox[0])
    assert result.returncode != 0, "run script must not succeed without an installed blog"
    assert not node_calls(calls)
