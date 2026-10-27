"""Behavior tests for the ingress URL-rewriting proxy (Given-When-Then).

Home Assistant serves the add-on through the ingress panel under a path
prefix and strips that prefix before the request reaches the container,
announcing it in the X-Ingress-Path header. Ghost's HTML/CSS reference
the site root, so the proxy rewrites those references to the prefix on
the way out. These tests run the real Node proxy against a fake Ghost
upstream: no image build, no Supervisor, no network.
"""

import gzip
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ADDON_DIR = REPO_ROOT / "ghost"
PROXY = ADDON_DIR / "root" / "usr" / "local" / "bin" / "ghost-ingress-proxy"
SERVICE_RUN = ADDON_DIR / "root" / "etc" / "services.d" / "ingress-proxy" / "run"
MANIFEST = ADDON_DIR / "config.yaml"
DOCKERFILE = ADDON_DIR / "Dockerfile"

PREFIX = "/api/hassio_ingress/token-123"
SESSION_ORIGIN = "https://ha.example.com:8123"
SITE_ORIGIN = "https://blog.example.com"
INGRESS_HEADERS = {
    "X-Ingress-Path": PREFIX,
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "ha.example.com:8123",
}

PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<link rel="stylesheet" href="/assets/built/screen.css?v=1">
<meta property="og:url" content="https://blog.example.com/post/">
<script src="https://cdn.example.com/x.js"></script>
<script defer src="/assets/built/portal.min.js" data-ghost="https://blog.example.com/" data-api="https://blog.example.com/ghost/api/content/" data-ghost-comments-counts-api="https://blog.example.com/members/api/comments/counts/"></script>
<script defer src="/assets/built/sodo-search.min.js" data-sodo-search="https://blog.example.com/" data-api-url="https://blog.example.com/members/api/announcement/" data-locale="en"></script>
<style>.hero{background:url(/content/images/hero.jpg)}</style>
</head>
<body>
<a href="/about/">About</a>
<a href="/">Home</a>
<img src="/content/images/a.jpg" srcset="/content/images/a.jpg 300w, /content/images/a-600.jpg 600w">
<a href="https://blog.example.com/contact/">Contact</a>
<a href="https://other.example.org/">Other</a>
</body>
</html>
"""

ADMIN_HTML = """<!DOCTYPE html>
<html class="no-js" lang="en">
<head>
<meta charset="UTF-8" />
<meta name="ghost-admin/config/environment" content="%7B%7D" />
<link rel="stylesheet" href="/assets/vendor.css">
<link rel="stylesheet" href="/assets/ghost-light.css" title="light">
</head>
<body>
<div class="ember-load-indicator"><div class="gh-loading-content"></div></div>
<script src="/assets/vendor.js"></script>
<script src="/assets/ghost.js"></script>
</body>
</html>
"""

THEME_SCRIPT = (
    ADDON_DIR / "root" / "usr" / "local" / "lib" / "ha-admin-theme" / "ha-admin-theme.js"
)
THEME_LOGO_BYTES = b"\x89PNG\r\n\x1a\nfake-addon-logo"

STYLE_CSS = """@import "/imported.css";
.x { background: url(/content/images/bg.png); }
.y { background: url(../relative.png); }
"""

API_JSON = json.dumps({"url": "/assets/x", "origin": SITE_ORIGIN})
ERROR_HTML = '<html><body><img src="/assets/error.png"></body></html>'


class UpstreamHandler(BaseHTTPRequestHandler):
    """Stands in for Ghost behind the proxy."""

    protocol_version = "HTTP/1.1"

    def send_body(self, status, body, content_type, headers=None, compress=False):
        raw = body.encode("utf-8") if isinstance(body, str) else body
        response_headers = {
            "Content-Type": content_type,
            "ETag": '"fake-etag"',
            "Cache-Control": "public, max-age=60",
        }
        if compress:
            raw = gzip.compress(raw)
            response_headers["Content-Encoding"] = "gzip"
        response_headers["Content-Length"] = str(len(raw))
        if headers:
            response_headers.update(headers)
        self.send_response(status)
        for name, value in response_headers.items():
            for item in value if isinstance(value, list) else [value]:
                self.send_header(name, item)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/page":
            self.send_body(200, PAGE_HTML, "text/html; charset=utf-8")
        elif parsed.path == "/style.css":
            self.send_body(200, STYLE_CSS, "text/css; charset=utf-8")
        elif parsed.path == "/api":
            self.send_body(200, API_JSON, "application/json; charset=utf-8")
        elif parsed.path == "/error-page":
            self.send_body(500, ERROR_HTML, "text/html; charset=utf-8")
        elif parsed.path == "/not-modified":
            self.send_response(304)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("ETag", '"fake-etag"')
            self.end_headers()
        elif parsed.path == "/gzip-page":
            self.send_body(200, PAGE_HTML, "text/html; charset=utf-8", compress=True)
        elif parsed.path == "/redirect":
            self.send_body(302, "", "text/html; charset=utf-8", {"Location": "/ghost/"})
        elif parsed.path == "/redirect-absolute":
            self.send_body(
                302,
                "",
                "text/html; charset=utf-8",
                {"Location": f"{SITE_ORIGIN}/ghost/"},
            )
        elif parsed.path == "/login":
            self.send_body(
                201,
                '{"token": "session"}',
                "application/json; charset=utf-8",
                {
                    "Set-Cookie": [
                        "ghost-admin-api-session=s1; Path=/ghost; HttpOnly; Secure; SameSite=None",
                        "members-ssr=m1; Path=/; HttpOnly; SameSite=Lax",
                    ]
                },
            )
        elif parsed.path == "/login-prefixed-cookie":
            self.send_body(
                201,
                '{"token": "session"}',
                "application/json; charset=utf-8",
                {
                    "Set-Cookie": (
                        "ghost-admin-api-session=s1; Path="
                        f"{PREFIX}/ghost; HttpOnly; Secure; SameSite=None"
                    )
                },
            )
        elif parsed.path == "/ghost/":
            self.send_body(200, ADMIN_HTML, "text/html; charset=utf-8")
        elif parsed.path == "/ghost/blog":
            self.send_body(200, PAGE_HTML, "text/html; charset=utf-8")
        elif parsed.path == "/ghost/themed":
            self.send_body(
                200,
                ADMIN_HTML.replace(
                    "</head>", '<script data-ha-admin-theme></script></head>'
                ),
                "text/html; charset=utf-8",
            )
        elif parsed.path == "/echo-headers":
            payload = json.dumps(
                {
                    "origin": self.headers.get("Origin"),
                    "referer": self.headers.get("Referer"),
                }
            )
            self.send_body(200, payload, "application/json; charset=utf-8")
        else:
            self.send_body(404, "not found", "text/plain; charset=utf-8")

    do_HEAD = do_GET

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        parsed = urlparse(self.path)
        payload = json.dumps({"method": "POST", "body": body, "query": parsed.query})
        self.send_body(200, payload, "application/json; charset=utf-8")

    def log_message(self, *args):
        pass


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_proxy(node, port, upstream, tmp_path, site=SITE_ORIGIN, extra_env=None):
    log_path = tmp_path / f"proxy-{port}.log"
    environment = {
        **os.environ,
        "GHOST_INGRESS_PROXY_HOST": "127.0.0.1",
        "GHOST_INGRESS_PROXY_PORT": str(port),
        "GHOST_INGRESS_PROXY_UPSTREAM": upstream,
        "GHOST_INGRESS_PROXY_SITE_URL": site,
    }
    if extra_env:
        environment.update(extra_env)
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [node, str(PROXY)],
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return process, log_path


def theme_env(tmp_path, assets_dir=None, script_path=None, logo_path=None):
    return {
        "GHOST_ADMIN_ASSETS_DIR": str(assets_dir or tmp_path / "admin-assets"),
        "GHOST_ADMIN_THEME_SCRIPT": str(script_path or THEME_SCRIPT),
        "GHOST_ADMIN_THEME_LOGO": str(logo_path or tmp_path / "theme-logo.png"),
    }


def wait_for_port(port, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"the proxy did not listen on port {port}")


def stop_proxy(process):
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def fetch(port, path, headers=None, method="GET", body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    payload = response.read()
    response_headers = {}
    for name, value in response.getheaders():
        key = name.lower()
        if key == "set-cookie":
            response_headers.setdefault("set-cookie", []).append(value)
        else:
            response_headers[key] = value
    status = response.status
    connection.close()
    return status, response_headers, payload


@pytest.fixture(name="addon")
def addon_fixture(tmp_path):
    # Given a fake Ghost upstream and the real proxy in front of it
    # When the proxy starts
    # Then it listens on its ingress port and forwards to the upstream
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on the test host")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    port = free_port()
    assets = tmp_path / "admin-assets"
    (assets / "videos").mkdir(parents=True)
    (assets / "videos" / "logo-loader-dark-test.mp4").write_bytes(b"")
    (tmp_path / "theme-logo.png").write_bytes(THEME_LOGO_BYTES)
    process, log_path = start_proxy(
        node,
        port,
        f"http://127.0.0.1:{upstream.server_address[1]}",
        tmp_path,
        extra_env=theme_env(tmp_path, assets),
    )
    try:
        wait_for_port(port)
    except AssertionError:
        stop_proxy(process)
        raise AssertionError(f"proxy failed to start:\n{log_path.read_text()}")
    yield SimpleNamespace(port=port, node=node, tmp_path=tmp_path)
    stop_proxy(process)
    upstream.shutdown()
    upstream.server_close()
    thread.join(timeout=5)


@pytest.fixture(name="theming_proxy")
def theming_proxy_fixture(tmp_path):
    # Given a fake Ghost upstream and a proxy factory
    # When a test chooses its own theme configuration
    # Then the proxy starts with that environment and is torn down afterwards
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on the test host")
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    started = []

    def start(extra_env):
        port = free_port()
        process, log_path = start_proxy(
            node,
            port,
            f"http://127.0.0.1:{upstream.server_address[1]}",
            tmp_path,
            extra_env=extra_env,
        )
        try:
            wait_for_port(port)
        except AssertionError:
            stop_proxy(process)
            raise AssertionError(f"proxy failed to start:\n{log_path.read_text()}")
        started.append(process)
        return SimpleNamespace(port=port, log_path=log_path)

    yield start
    for process in started:
        stop_proxy(process)
    upstream.shutdown()
    upstream.server_close()
    thread.join(timeout=5)


# --------------------------------------------------------------- pass-through

def test_direct_access_is_not_rewritten(addon):
    # Given a request without the ingress header (the exposed 2368 port)
    # When the proxy forwards it to Ghost
    # Then the body is byte-identical, the ETag survives and no Vary is added
    status, headers, payload = fetch(addon.port, "/page")
    assert status == 200
    assert payload == PAGE_HTML.encode("utf-8")
    assert headers["etag"] == '"fake-etag"'
    assert "vary" not in headers


def test_non_html_responses_are_untouched_under_ingress(addon):
    # Given an ingress request for a JSON API response
    # When the proxy receives it
    # Then the body and the ETag pass through unchanged (only HTML/CSS is
    # rewritten); Vary announces the header dependency for caches
    status, headers, payload = fetch(addon.port, "/api", headers=INGRESS_HEADERS)
    assert status == 200
    assert payload == API_JSON.encode("utf-8")
    assert headers["etag"] == '"fake-etag"'
    assert headers["vary"] == "X-Ingress-Path"


def test_invalid_ingress_header_is_ignored(addon):
    # Given a spoofed ingress header that is not a Home Assistant ingress path
    # When the proxy handles the request
    # Then it does not interpolate it into the page (no rewriting at all)
    status, headers, payload = fetch(
        addon.port,
        "/page",
        headers={"X-Ingress-Path": '/evil" onmouseover="alert(1)'},
    )
    assert status == 200
    assert payload == PAGE_HTML.encode("utf-8")
    assert "vary" not in headers


def test_gzip_upstream_bodies_pass_through_unmodified(addon):
    # Given Ghost compresses a page even though the proxy asked for identity
    # When an ingress request is handled
    # Then the encoded body is passed through as-is (rewriting compressed
    # bodies would corrupt them), and the browser decodes the original page
    status, headers, payload = fetch(addon.port, "/gzip-page", headers=INGRESS_HEADERS)
    assert status == 200
    assert headers["content-encoding"] == "gzip"
    assert gzip.decompress(payload) == PAGE_HTML.encode("utf-8")


def test_not_modified_responses_keep_their_validator(addon):
    # Given Ghost answers a conditional request with 304 Not Modified
    # When the response passes the proxy
    # Then the status and the ETag survive (the browser reuses its cached,
    # already-rewritten body) and Vary still announces the header dependency
    status, headers, payload = fetch(addon.port, "/not-modified", headers=INGRESS_HEADERS)
    assert status == 304
    assert payload == b""
    assert headers["etag"] == '"fake-etag"'
    assert headers["vary"] == "X-Ingress-Path"


# ------------------------------------------------------------ HTML rewriting

def test_root_relative_urls_are_prefixed(addon):
    # Given a theme page with root-relative asset and link references
    # When it is served through ingress
    # Then every root-relative reference carries the session's ingress path
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert f'href="{PREFIX}/about/"' in body
    assert f'href="{PREFIX}/"' in body
    assert f'href="{PREFIX}/assets/built/screen.css?v=1"' in body
    assert f'src="{PREFIX}/content/images/a.jpg"' in body


def test_srcset_candidates_are_prefixed(addon):
    # Given an image with a srcset of two root-relative candidates
    # When the page is served through ingress
    # Then both candidates carry the prefix and their width descriptors survive
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert (
        f'srcset="{PREFIX}/content/images/a.jpg 300w, '
        f'{PREFIX}/content/images/a-600.jpg 600w"' in body
    )


def test_inline_css_urls_are_prefixed(addon):
    # Given a page with an inline style referencing a root-relative image
    # When it is served through ingress
    # Then the CSS url() carries the prefix
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    assert f"url({PREFIX}/content/images/hero.jpg)" in payload.decode("utf-8")


def test_site_origin_urls_are_rewritten_to_the_session(addon):
    # Given a page carrying absolute URLs on the configured site origin
    # (canonical/og URLs, navigation, portal API endpoints)
    # When it is served through ingress
    # Then they point at the browser-facing Home Assistant origin and prefix,
    # so the panel stays inside the ingress path
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert f'content="{SESSION_ORIGIN}{PREFIX}/post/"' in body
    assert f'href="{SESSION_ORIGIN}{PREFIX}/contact/"' in body


def test_portal_configuration_urls_are_rewritten(addon):
    # Given the front page carries Portal's data-ghost/data-api/data-* URL
    # attributes on the configured site origin
    # When it is served through ingress
    # Then Portal calls the API through the panel (same origin) instead of
    # the public origin (which would fail CORS or be unreachable)
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert f'data-ghost="{SESSION_ORIGIN}{PREFIX}/"' in body
    assert f'data-api="{SESSION_ORIGIN}{PREFIX}/ghost/api/content/"' in body
    assert (
        f'data-ghost-comments-counts-api="{SESSION_ORIGIN}{PREFIX}/members/api/comments/counts/"'
        in body
    )
    assert f'data-sodo-search="{SESSION_ORIGIN}{PREFIX}/"' in body
    assert f'data-api-url="{SESSION_ORIGIN}{PREFIX}/members/api/announcement/"' in body
    assert 'data-locale="en"' in body


def test_non_url_data_attributes_are_untouched(addon):
    # Given data-* attributes that do not hold URLs
    # When the page is served through ingress
    # Then they are not modified (the generic data-* match only rewrites
    # root-relative and site-origin URL values)
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert 'data-locale="en"' in body


def test_external_absolute_urls_are_untouched(addon):
    # Given links to third-party origins
    # When the page is served through ingress
    # Then those origins are not rewritten
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert 'src="https://cdn.example.com/x.js"' in body
    assert 'href="https://other.example.org/"' in body


def test_site_origin_urls_fall_back_to_a_root_relative_prefix_without_forwarded_headers(addon):
    # Given an ingress request that carries no X-Forwarded-Proto/Host
    # When absolute site URLs are rewritten
    # Then they become root-relative under the prefix (still correct in the
    # browser; the forwarded headers are always sent by Home Assistant Core)
    _, _, payload = fetch(
        addon.port, "/page", headers={"X-Ingress-Path": PREFIX}
    )
    assert f'href="{PREFIX}/contact/"' in payload.decode("utf-8")


def test_rewritten_responses_drop_the_etag_and_announce_the_vary(addon):
    # Given an ingress request for a page
    # When the proxy rewrites the body
    # Then the stale upstream ETag is removed (the body differs per request)
    # and Vary tells caches the response depends on X-Ingress-Path
    status, headers, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    assert status == 200
    assert "etag" not in headers
    assert "X-Ingress-Path" in headers["vary"]
    assert int(headers["content-length"]) == len(payload)


def test_error_pages_are_rewritten_too(addon):
    # Given Ghost returns an HTML error page with root-relative assets
    # When it is served through ingress
    # Then the status is preserved and the assets carry the prefix
    status, _, payload = fetch(addon.port, "/error-page", headers=INGRESS_HEADERS)
    assert status == 500
    assert f'src="{PREFIX}/assets/error.png"' in payload.decode("utf-8")


def test_gzip_is_applied_when_the_client_accepts_it(addon):
    # Given the browser accepts gzip
    # When the proxy rewrites a page
    # Then the rewritten body is gzip-encoded, decodes to the prefixed page
    # and Vary names Accept-Encoding as well
    _, headers, payload = fetch(
        addon.port, "/page", headers={**INGRESS_HEADERS, "Accept-Encoding": "gzip"}
    )
    assert headers["content-encoding"] == "gzip"
    assert "Accept-Encoding" in headers["vary"]
    body = gzip.decompress(payload).decode("utf-8")
    assert f'href="{PREFIX}/about/"' in body


def test_head_requests_have_no_body(addon):
    # Given a HEAD request through ingress
    # When the proxy handles it
    # Then the status is forwarded and no body is written
    status, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS, method="HEAD")
    assert status == 200
    assert payload == b""


# ------------------------------------------------------------- CSS rewriting

def test_css_bodies_are_rewritten(addon):
    # Given a stylesheet with an @import and root-relative url() references
    # When it is served through ingress
    # Then both are prefixed while document-relative URLs are left alone
    status, headers, payload = fetch(addon.port, "/style.css", headers=INGRESS_HEADERS)
    assert status == 200
    assert "text/css" in headers["content-type"]
    body = payload.decode("utf-8")
    assert f'@import "{PREFIX}/imported.css"' in body
    assert f"url({PREFIX}/content/images/bg.png)" in body
    assert "url(../relative.png)" in body


# --------------------------------------------------------- Location rewriting

def test_root_relative_redirects_stay_inside_the_panel(addon):
    # Given Ghost redirects to a root-relative path
    # When the response passes the proxy
    # Then the Location carries the ingress prefix
    status, headers, _ = fetch(addon.port, "/redirect", headers=INGRESS_HEADERS)
    assert status == 302
    assert headers["location"] == f"{PREFIX}/ghost/"


def test_site_origin_redirects_are_rewritten_to_the_session(addon):
    # Given Ghost redirects to an absolute URL on the configured site origin
    # When the response passes the proxy
    # Then the Location points at the browser-facing origin and prefix
    status, headers, _ = fetch(addon.port, "/redirect-absolute", headers=INGRESS_HEADERS)
    assert status == 302
    assert headers["location"] == f"{SESSION_ORIGIN}{PREFIX}/ghost/"


# ------------------------------------------------------------ request bodies

def test_post_bodies_and_query_strings_are_forwarded(addon):
    # Given a POST with a query string, sent through ingress
    # When the proxy forwards it
    # Then Ghost receives the method, body and query, and the JSON response
    # is passed through without rewriting
    status, headers, payload = fetch(
        addon.port,
        "/echo?x=1",
        headers={**INGRESS_HEADERS, "Content-Type": "text/plain"},
        method="POST",
        body="hello",
    )
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    assert json.loads(payload) == {"method": "POST", "body": "hello", "query": "x=1"}


# --------------------------------------------------------------- failures

def test_upstream_failure_returns_bad_gateway(tmp_path):
    # Given Ghost is not listening on the upstream port
    # When a request reaches the proxy
    # Then it answers 502 instead of hanging
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on the test host")
    port = free_port()
    process, log_path = start_proxy(
        node, port, f"http://127.0.0.1:{free_port()}", tmp_path
    )
    try:
        wait_for_port(port)
        status, _, payload = fetch(port, "/page")
        assert status == 502
        assert payload == b"Bad Gateway\n"
    finally:
        stop_proxy(process)
        assert log_path.exists()


# ------------------------------------------------------------- session cookies

def test_session_cookie_path_is_scoped_to_the_ingress_path(addon):
    # Given Ghost scopes its admin session cookie to Path=/ghost (the
    # subdirectory-derived path it uses at the origin root)
    # When the login response passes the proxy under ingress
    # Then the cookie path carries the ingress prefix, so the browser
    # returns the cookie on /api/hassio_ingress/<token>/ghost/... requests;
    # without it the admin session is lost and 2FA verification fails
    status, headers, _ = fetch(addon.port, "/login", headers=INGRESS_HEADERS)
    assert status == 201
    assert (
        f"ghost-admin-api-session=s1; Path={PREFIX}/ghost; "
        "HttpOnly; Secure; SameSite=None"
    ) in headers["set-cookie"], headers["set-cookie"]


def test_root_cookie_paths_are_left_alone(addon):
    # Given a cookie scoped to the site root (e.g. the members session)
    # When the response passes the proxy under ingress
    # Then its path is unchanged: "/" already matches every request
    _, headers, _ = fetch(addon.port, "/login", headers=INGRESS_HEADERS)
    assert "members-ssr=m1; Path=/; HttpOnly; SameSite=Lax" in headers["set-cookie"]


def test_cookie_paths_are_not_rewritten_for_direct_access(addon):
    # Given the same login response without the ingress header (the
    # exposed port)
    # When the proxy passes it through
    # Then the cookie path is untouched (/ghost is correct at the root)
    _, headers, _ = fetch(addon.port, "/login")
    assert (
        "ghost-admin-api-session=s1; Path=/ghost; HttpOnly; Secure; SameSite=None"
    ) in headers["set-cookie"], headers["set-cookie"]


def test_already_prefixed_cookie_paths_are_not_rewritten_twice(addon):
    # Given a cookie whose path already carries the ingress prefix
    # When the response passes the proxy
    # Then it is left as is (the rewrite is idempotent)
    _, headers, _ = fetch(
        addon.port, "/login-prefixed-cookie", headers=INGRESS_HEADERS
    )
    assert headers["set-cookie"][0].endswith(
        f"Path={PREFIX}/ghost; HttpOnly; Secure; SameSite=None"
    ), headers["set-cookie"]


# --------------------------------------------------------------- request origin

def test_missing_referer_is_filled_with_the_session_origin(addon):
    # Given Home Assistant serves pages with Referrer-Policy: no-referrer,
    # so the admin page's requests carry no Referer, and same-origin GET
    # requests carry no Origin either
    # When such an ingress request reaches the proxy
    # Then it supplies a Referer with the browser-facing origin, so Ghost's
    # CSRF check matches the origin stored in the session at login instead
    # of falling back to the configured site URL
    _, _, payload = fetch(addon.port, "/echo-headers", headers=INGRESS_HEADERS)
    echoed = json.loads(payload)
    assert echoed["referer"] == f"{SESSION_ORIGIN}{PREFIX}/"
    assert echoed["origin"] is None


def test_existing_origin_is_not_overridden(addon):
    # Given a request that already carries Origin (the login POST)
    # When the proxy forwards it
    # Then Ghost sees that Origin unchanged and no Referer is injected
    _, _, payload = fetch(
        addon.port, "/echo-headers", headers={**INGRESS_HEADERS, "Origin": SESSION_ORIGIN}
    )
    echoed = json.loads(payload)
    assert echoed["origin"] == SESSION_ORIGIN
    assert echoed["referer"] is None


def test_existing_referer_is_not_overridden(addon):
    # Given a request that already carries a Referer
    # When the proxy forwards it
    # Then the original Referer is kept
    referer = f"{SESSION_ORIGIN}{PREFIX}/ghost/"
    _, _, payload = fetch(
        addon.port, "/echo-headers", headers={**INGRESS_HEADERS, "Referer": referer}
    )
    assert json.loads(payload)["referer"] == referer


def test_referer_is_not_injected_for_direct_access(addon):
    # Given a request without the ingress header (the exposed port)
    # When the proxy forwards it
    # Then it does not invent a Referer (direct access keeps Ghost's own
    # behavior: the browser's real headers, or the site-URL fallback)
    _, _, payload = fetch(addon.port, "/echo-headers")
    assert json.loads(payload)["referer"] is None


# --------------------------------------------------------- admin theme sync

def test_anonymous_ingress_admin_document_is_themed(addon):
    # Given an anonymous request through ingress for Ghost's admin document
    # When the proxy responds
    # Then the inline theme script is injected before </head>, carrying the
    # hashed dark loader video, and no Ghost dark stylesheet link is added
    status, _, payload = fetch(addon.port, "/ghost/", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert status == 200
    assert (
        '<script data-ha-admin-theme '
        'data-dark-loader="assets/videos/logo-loader-dark-test.mp4" '
        'data-site-icon="assets/ha-admin-theme-logo.png">'
    ) in body
    assert "ha-admin-theme-style" in body
    assert "dark-styles" not in body
    assert body.index("data-ha-admin-theme") < body.index("</head>")


def test_the_add_on_logo_is_served_for_the_site_icon(addon):
    # Given the add-on logo shipped in the image
    # When the browser requests the site-icon asset through ingress
    # Then the proxy serves the PNG itself instead of forwarding to Ghost,
    # and HEAD returns the same headers without a body
    status, headers, payload = fetch(
        addon.port,
        "/ghost/assets/ha-admin-theme-logo.png",
        headers=INGRESS_HEADERS,
    )
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert payload == THEME_LOGO_BYTES

    status, headers, payload = fetch(
        addon.port,
        "/ghost/assets/ha-admin-theme-logo.png",
        headers=INGRESS_HEADERS,
        method="HEAD",
    )
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert payload == b""


def test_the_site_icon_attribute_is_omitted_without_the_logo(tmp_path, theming_proxy):
    # Given a build without the add-on logo
    # When the admin document is served through ingress
    # Then the script is still injected, but without the site-icon attribute
    assets = tmp_path / "assets-with-video"
    (assets / "videos").mkdir(parents=True)
    (assets / "videos" / "logo-loader-dark-test.mp4").write_bytes(b"")
    proxy = theming_proxy(
        theme_env(tmp_path, assets, logo_path=tmp_path / "missing-logo.png")
    )
    status, _, payload = fetch(proxy.port, "/ghost/", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert status == 200
    assert 'data-site-icon="' not in body
    assert 'data-dark-loader="assets/videos/logo-loader-dark-test.mp4"' in body
    assert "ha-admin-theme-style" in body


def test_the_admin_session_cookie_does_not_change_the_document(addon):
    # Given the same admin document requested with and without the admin
    # session cookie
    # When both are served through ingress
    # Then the injected markup is identical: the script no longer uses a
    # session flag and never touches Ghost's authenticated theme
    _, _, anonymous = fetch(addon.port, "/ghost/", headers=INGRESS_HEADERS)
    _, _, authenticated = fetch(
        addon.port,
        "/ghost/",
        headers={**INGRESS_HEADERS, "Cookie": "ghost-admin-api-session=s1"},
    )
    assert anonymous == authenticated


def test_themed_admin_responses_drop_the_etag_and_vary_on_the_ingress_path(addon):
    # Given a themed admin document
    # When it passes the proxy through ingress
    # Then the stale ETag is dropped, the length is recomputed and the
    # response varies only on the ingress path (no cookie dependency)
    status, headers, payload = fetch(addon.port, "/ghost/", headers=INGRESS_HEADERS)
    assert status == 200
    assert "etag" not in headers
    assert "X-Ingress-Path" in headers["vary"]
    assert "Cookie" not in headers["vary"]
    assert int(headers["content-length"]) == len(payload)


def test_direct_admin_access_is_untouched(addon):
    # Given the same admin document without the ingress header (the exposed
    # port)
    # When the proxy passes it through
    # Then it is byte-identical and no theme is injected
    status, headers, payload = fetch(addon.port, "/ghost/")
    assert status == 200
    assert payload == ADMIN_HTML.encode("utf-8")
    assert headers["etag"] == '"fake-etag"'
    assert "vary" not in headers


def test_blog_html_is_not_themed(addon):
    # Given a regular blog page under ingress
    # When it is served
    # Then no admin theme markup is injected
    _, _, payload = fetch(addon.port, "/page", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert "data-ha-admin-theme" not in body
    assert "dark-styles" not in body


def test_an_admin_path_without_the_document_marker_is_not_themed(addon):
    # Given a page under the admin path that is not the admin document
    # When it is served through ingress
    # Then the missing admin marker prevents the injection
    _, _, payload = fetch(addon.port, "/ghost/blog", headers=INGRESS_HEADERS)
    assert "data-ha-admin-theme" not in payload.decode("utf-8")


def test_injection_happens_after_url_rewriting(addon):
    # Given the admin document with root-relative asset references
    # When it is served through ingress
    # Then the references are prefixed, while the injected script source is
    # left untouched
    _, _, payload = fetch(addon.port, "/ghost/", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert f'href="{PREFIX}/assets/vendor.css"' in body
    assert "ha-admin-theme-style" in body
    assert "dark-styles" not in body


def test_an_invalid_ingress_header_never_injects_into_the_admin_document(addon):
    # Given a spoofed ingress header that is not a Home Assistant ingress path
    # When the admin document is served
    # Then nothing is rewritten or injected
    status, _, payload = fetch(
        addon.port,
        "/ghost/",
        headers={"X-Ingress-Path": '/evil" onmouseover="alert(1)'},
    )
    assert status == 200
    assert payload == ADMIN_HTML.encode("utf-8")


def test_injection_is_idempotent(addon):
    # Given an admin document that already carries the theme script marker
    # When it passes the proxy
    # Then the proxy does not inject a second copy
    _, _, payload = fetch(addon.port, "/ghost/themed", headers=INGRESS_HEADERS)
    assert payload.decode("utf-8").count("data-ha-admin-theme") == 1


def test_injection_is_disabled_without_the_theme_script(tmp_path, theming_proxy):
    # Given a missing theme script
    # When the admin document is served through ingress
    # Then the page is still served, but no theme is injected
    assets = tmp_path / "assets-with-video"
    (assets / "videos").mkdir(parents=True)
    (assets / "videos" / "logo-loader-dark-test.mp4").write_bytes(b"")
    proxy = theming_proxy(
        theme_env(tmp_path, assets, tmp_path / "missing-theme.js")
    )
    status, _, payload = fetch(proxy.port, "/ghost/", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert status == 200
    assert "data-ha-admin-theme" not in body
    assert f'href="{PREFIX}/assets/vendor.css"' in body


def test_the_dark_loader_attribute_is_omitted_without_the_video(tmp_path, theming_proxy):
    # Given a build without the dark loader video but with the add-on logo
    # When the admin document is served through ingress
    # Then the script is injected without the dark loader attribute
    assets = tmp_path / "assets-without-video"
    assets.mkdir()
    logo = tmp_path / "theme-logo.png"
    logo.write_bytes(THEME_LOGO_BYTES)
    proxy = theming_proxy(theme_env(tmp_path, assets, logo_path=logo))
    status, _, payload = fetch(proxy.port, "/ghost/", headers=INGRESS_HEADERS)
    body = payload.decode("utf-8")
    assert status == 200
    assert 'data-dark-loader="' not in body
    assert 'data-site-icon="assets/ha-admin-theme-logo.png"' in body
    assert "ha-admin-theme-style" in body
    assert f'href="{PREFIX}/assets/vendor.css"' in body


# ------------------------------------------------------------- packaging

def test_manifest_ingress_port_matches_the_proxy_default():
    # Given the Supervisor forwards ingress traffic to ingress_port
    # When the manifest and the proxy default are compared
    # Then they agree, so the manifest cannot drift from the code
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    match = re.search(r"^const DEFAULT_LISTEN_PORT = (\d+);$", PROXY.read_text(), re.MULTILINE)
    assert match, "the proxy must declare its default listen port"
    assert int(match.group(1)) == manifest["ingress_port"]


def test_exposed_port_bypasses_the_proxy():
    # Given the exposed port serves the blog directly (no ingress rewriting)
    # When the manifest is checked
    # Then the exposed 2368 port still targets Ghost's own port, not the
    # proxy's separate ingress port
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["ports"] == {"2368/tcp": 2368}
    assert manifest["ingress_port"] != 2368


def test_service_script_starts_the_shipped_proxy():
    # Given s6 runs the service definitions from root/
    # When the ingress proxy service is checked
    # Then it exists, is executable, and runs the shipped proxy script
    assert SERVICE_RUN.is_file(), f"missing {SERVICE_RUN}"
    assert SERVICE_RUN.stat().st_mode & 0o111, "the service script must be executable"
    assert "ghost-ingress-proxy" in SERVICE_RUN.read_text(encoding="utf-8")
    assert PROXY.is_file(), f"missing {PROXY}"
    assert PROXY.stat().st_mode & 0o111, "the proxy must be executable (COPY root / keeps the mode)"


def test_dockerfile_ships_the_proxy_with_the_root_tree():
    # Given the Dockerfile copies the s6 root tree into the image
    # When it is checked
    # Then the proxy ships with that tree and no extra package is needed
    # (Node is already in the image)
    assert "COPY root /" in DOCKERFILE.read_text(encoding="utf-8")
