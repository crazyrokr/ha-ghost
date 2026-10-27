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
            self.send_header(name, value)
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


def start_proxy(node, port, upstream, tmp_path, site=SITE_ORIGIN):
    log_path = tmp_path / f"proxy-{port}.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [node, str(PROXY)],
            env={
                **os.environ,
                "GHOST_INGRESS_PROXY_HOST": "127.0.0.1",
                "GHOST_INGRESS_PROXY_PORT": str(port),
                "GHOST_INGRESS_PROXY_UPSTREAM": upstream,
                "GHOST_INGRESS_PROXY_SITE_URL": site,
            },
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return process, log_path


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
    response_headers = {name.lower(): value for name, value in response.getheaders()}
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
    process, log_path = start_proxy(
        node, port, f"http://127.0.0.1:{upstream.server_address[1]}", tmp_path
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
