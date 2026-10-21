"""Behavior tests for the add-on s6 service scripts (Given-When-Then).

The scripts run under the HA debian-base image where s6 invokes them
with the container environment and '/command/with-contenv' on PATH.
Here they are executed with the same interpreter the image would use
(bashio-based scripts under bash, plain scripts under sh), the same
environment exported, and 'ghost'/'node'/'runuser' replaced by stubs
that record their invocation. This keeps the tests hermetic (no
network, no image build, no root).
"""

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

    ghost_stub = tmp_path / "ghost"
    ghost_stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo "ghost $@" >> "$CALL_LOG"
            mkdir -p "$GHOST_BLOG_DIR/current/node_modules"
            touch "$GHOST_BLOG_DIR/config.production.json"
            """
        )
    )
    # Like the real node: requiring the sqlite3 module succeeds only when the
    # package (marker file) is actually present in the app tree.
    node_stub = tmp_path / "node"
    node_stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo "node cwd=$(pwd) NODE_ENV=${NODE_ENV:-} $@" >> "$CALL_LOG"
            for a in "$@"; do
                case "$a" in
                    *node_modules/sqlite3*)
                        test -f "$GHOST_BLOG_DIR/current/node_modules/sqlite3/lib/index.js" || exit 1
                        ;;
                esac
            done
            exit 0
            """
        )
    )
    # Like npm install in a scratch directory: on success the sqlite3 package
    # (with its prebuilt binary) appears under node_modules/ in the cwd.
    # NPM_STUB_FAIL=1 simulates a failed download.
    npm_stub = tmp_path / "npm"
    npm_stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo "npm cwd=$(pwd) $@" >> "$CALL_LOG"
            case "${NPM_STUB_FAIL:-0}" in
                1) exit 1 ;;
            esac
            mkdir -p node_modules/sqlite3/lib
            printf 'module.exports = 1\n' > node_modules/sqlite3/lib/index.js
            exit 0
            """
        )
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
    for stub in (ghost_stub, node_stub, npm_stub, runuser_stub):
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "CALL_LOG": str(tmp_path / "calls.log"),
        "GHOST_BLOG_DIR": str(blog_dir),
        "CONTAINER_ENV_DIR": str(container_env),
        "SQLITE3_STAGING_DIR": str(tmp_path / "sq3staging"),
        "HOME": str(tmp_path / "root"),
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


def test_cont_installs_blog_on_first_boot(sandbox):
    # Given no Ghost blog exists in the config volume yet (first boot)
    # When the cont-init script runs
    # Then it installs the pinned Ghost version into the blog directory
    # without starting it or prompting, as the ghost user (Ghost CLI
    # refuses to run as root), and generates the production config
    # bound to all interfaces on port 2368 with the sqlite database
    _, _, blog_dir, container_env, env = sandbox
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert any(c.startswith("runuser ghost ") for c in calls), (
        f"install must run as the ghost user: {calls}"
    )
    calls = ghost_calls(calls)
    assert calls, "ghost install was never called"
    assert "install" in calls[0]
    assert "5.130.6" in calls[0], "Ghost version must be pinned"
    assert f"--dir {blog_dir}" in calls[0]
    assert "--no-start" in calls[0]
    assert "--no-setup" in calls[0], "install must not prompt (no TTY in add-on)"
    assert "--db client" in calls[0]
    assert "--port 2368" in calls[0]
    assert "--ip 0.0.0.0" in calls[0], "Ghost must bind all interfaces for ingress"

    config = (blog_dir / "config.production.json").read_text()
    assert '"host": "0.0.0.0"' in config, f"config must bind all interfaces: {config}"
    assert '"port": 2368' in config
    assert '"client": "sqlite3"' in config, "sqlite must be the default database"
    assert '"database": {' in config, "Ghost 5 reads the 'database' key, not 'db'"
    assert f'"contentPath": "{blog_dir}/content/"' in config, (
        "content must live in the persisted blog directory: " + config
    )
    assert f'"filename": "{blog_dir}/content/data/ghost.db"' in config, (
        "the sqlite file must be absolute: " + config
    )
    assert '"url": "null"' not in config, (
        "bashio's 'null' placeholder must be normalized to an absent url: " + config
    )
    assert database_env_files(container_env) == [], "no database env by default"


def test_cont_generates_config_with_url_option(sandbox):
    # Given no blog exists yet and the add-on option 'url' is set
    # When the cont-init script runs
    # Then the generated config carries the public url for Ghost
    _, _, blog_dir, _, env = sandbox
    env = {**env, "STUB_URL": "https://blog.example.com"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    config = (blog_dir / "config.production.json").read_text()
    assert '"url": "https://blog.example.com"' in config, config


def test_cont_skips_install_when_blog_exists(sandbox):
    # Given a Ghost blog already exists (config.production.json present)
    # When the cont-init script runs
    # Then it does not call ghost at all (idempotent, no re-install)
    _, _, blog_dir, _, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not ghost_calls(calls), f"unexpected reinstall: {calls}"


def test_cont_does_not_rewrite_existing_config(sandbox):
    # Given a blog already exists and the user changed options
    # When the cont-init script runs
    # Then neither ghost nor the existing config file is touched
    # (user edits to the config must survive restarts)
    _, _, blog_dir, _, env = sandbox
    blog_dir.mkdir(parents=True, exist_ok=True)
    (blog_dir / "current" / "node_modules").mkdir(parents=True)
    (blog_dir / "config.production.json").write_text('{"user_edit": true}', encoding="utf-8")
    env = {**env, "STUB_URL": "https://blog.example.com"}
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not ghost_calls(calls), f"existing blog must not be reinstalled: {calls}"
    assert (blog_dir / "config.production.json").read_text() == '{"user_edit": true}'


def test_cont_publishes_mysql_env_when_selected(sandbox):
    # Given the add-on options select an external MariaDB
    # (db_type=mysql plus host, user, password, database name)
    # When the cont-init script runs
    # Then the container environment carries the database__* variables
    # Ghost's config loader merges above config files
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
    # Then no database__* variables are published (the built-in sqlite
    # config takes effect)
    _, _, _, container_env, env = sandbox
    (container_env / "database__client").write_text("mysql", encoding="utf-8")
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert database_env_files(container_env) == [], (
        f"stale database env must be cleared: {database_env_files(container_env)}"
    )


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


def test_cont_heals_missing_sqlite3_module(sandbox):
    # Given an installed blog whose yarn install dropped the sqlite3 module
    # (Ghost's pinned prebuild-install cannot resolve its N-API target on
    # this Node, so the optionalDependency was removed)
    # When the cont-init script runs
    # Then it installs the module from its prebuilt binary with a plain npm
    # install in a scratch directory and copies it into the app tree
    _, _, blog_dir, _, env = sandbox
    (blog_dir / "current" / "node_modules").mkdir(parents=True, exist_ok=True)
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    npm_calls = [c for c in calls if c.startswith("npm ")]
    assert any("install sqlite3@5.1.7" in c for c in npm_calls), (
        f"the module must be reinstalled from the prebuilt binary: {calls}"
    )
    marker = blog_dir / "current" / "node_modules" / "sqlite3" / "lib" / "index.js"
    assert marker.is_file(), "the sqlite3 package must be present in the app tree"


def test_cont_skips_heal_when_sqlite3_present(sandbox):
    # Given an installed blog whose node_modules already carries a working
    # sqlite3 module
    # When the cont-init script runs
    # Then it leaves it alone (no npm call at all)
    _, _, blog_dir, _, env = sandbox
    module = blog_dir / "current" / "node_modules" / "sqlite3" / "lib"
    module.mkdir(parents=True)
    (module / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    result, calls = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if c.startswith("npm ")], (
        f"the module is already present, no install expected: {calls}"
    )


def test_cont_fails_when_sqlite3_install_fails(sandbox):
    # Given an installed blog without the sqlite3 module, and the prebuilt
    # binary download fails (npm exits non-zero)
    # When the cont-init script runs
    # Then it exits non-zero (the blog cannot start) without leaving a
    # half-installed package behind
    _, _, blog_dir, _, env = sandbox
    (blog_dir / "current" / "node_modules").mkdir(parents=True, exist_ok=True)
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    env = {**env, "NPM_STUB_FAIL": "1"}
    result, _ = run_script(CONT_INIT, env, sandbox[0], shell="bash")
    assert result.returncode != 0, "boot must fail when the sqlite3 module cannot be installed"
    assert not (blog_dir / "current" / "node_modules" / "sqlite3").exists(), (
        "no half-installed package may be left behind"
    )


# ------------------------------------------------------------------ service

def test_service_runs_ghost_with_production_node(sandbox):
    # Given a fully installed Ghost blog (current/ directory present)
    # When the s6 run script starts
    # Then it executes the Ghost entry point as the ghost user with
    # NODE_ENV=production from the blog directory
    _, _, blog_dir, _, env = sandbox
    (blog_dir / "current").mkdir(parents=True)
    (blog_dir / "current" / "index.js").write_text("console.log('x')", encoding="utf-8")
    (blog_dir / "config.production.json").write_text("{}", encoding="utf-8")
    result, calls = run_script(SERVICE_RUN, env, sandbox[0])
    assert any(c.startswith("runuser ghost node ") for c in calls), (
        f"ghost must run as the ghost user: {calls}"
    )
    calls = node_calls(calls)
    assert calls, f"node was never started: {calls}"
    assert f"cwd={blog_dir}" in calls[0], f"wrong working directory: {calls[0]}"
    assert "NODE_ENV=production" in calls[0], f"missing NODE_ENV=production: {calls[0]}"
    assert calls[0].endswith("current/index.js")


def test_service_fails_when_blog_missing(sandbox):
    # Given no Ghost blog exists (install never happened)
    # When the s6 run script starts
    # Then it exits non-zero so s6 can report the failure
    _, _, _, _, env = sandbox
    result, calls = run_script(SERVICE_RUN, env, sandbox[0])
    assert result.returncode != 0, "run script must not succeed without an installed blog"
    assert not node_calls(calls)
