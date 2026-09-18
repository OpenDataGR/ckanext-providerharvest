"""Throwaway HTTP JSON API standing in for a real provider, for the
integration tests in ckanext/providerharvest/tests/integration/. Serves a
fixed page of records on page 1 and an empty page afterwards, matching
DirectHTTPSTransport's page_number pagination style (see
transport/direct_https.py's list_entries: it stops once a page comes back
with no records).

Also exercises the Basic auth and OAuth2 client-credentials
AuthStrategy implementations end to end (not just against fakes, see
test_auth_strategies.py for those), each on its own path so a test picks
which check applies just by choosing which endpoint_url to register:
GET /records (no auth, the original behaviour), GET /records-basic
(rejects unless valid Basic auth is present), GET /records-bearer
(rejects unless a valid bearer token, issued by POST /oauth/token --
just enough of RFC 6749's client-credentials grant for this -- is
present). mTLS isn't exercised here -- see transport/ftp.py's FTPS
reasoning for why a self-signed cert in a disposable test container
isn't worth the added complexity when the unit tests already cover it
directly with fakes.
"""

import base64
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

RECORDS = [
    {"id": 1, "name": "Alpha"},
    {"id": 2, "name": "Bravo"},
    {"id": 3, "name": "Charlie"},
]

BASIC_AUTH_USERNAME = "mock-user"
BASIC_AUTH_PASSWORD = "mock-pass"
OAUTH2_CLIENT_ID = "mock-client"
OAUTH2_CLIENT_SECRET = "mock-secret"
OAUTH2_TOKEN_TTL_S = 300

#: token -> expiry (monotonic-ish; good enough for a throwaway process).
_ISSUED_TOKENS: dict[str, float] = {}


def _decode_basic_auth(header_value: str):
    if not header_value or not header_value.startswith("Basic "):
        return None
    try:
        return base64.b64decode(header_value[len("Basic "):]).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None


def _basic_auth_ok(header_value: str) -> bool:
    return _decode_basic_auth(header_value) == "%s:%s" % (BASIC_AUTH_USERNAME, BASIC_AUTH_PASSWORD)


def _oauth2_client_auth_ok(header_value: str) -> bool:
    return _decode_basic_auth(header_value) == "%s:%s" % (OAUTH2_CLIENT_ID, OAUTH2_CLIENT_SECRET)


def _bearer_token_ok(header_value: str) -> bool:
    if not header_value or not header_value.startswith("Bearer "):
        return False
    token = header_value[len("Bearer "):]
    expires_at = _ISSUED_TOKENS.get(token)
    return expires_at is not None and time.time() < expires_at


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path not in ("/records", "/records-basic", "/records-bearer"):
            self._json(404, {"error": "not found"})
            return

        auth_header = self.headers.get("Authorization", "")
        if path == "/records-basic" and not _basic_auth_ok(auth_header):
            self.send_response(401)
            self.end_headers()
            return
        if path == "/records-bearer" and not _bearer_token_ok(auth_header):
            self.send_response(401)
            self.end_headers()
            return

        query = parse_qs(urlsplit(self.path).query)
        page = int(query.get("page", ["1"])[0])
        self._json(200, {"results": RECORDS if page == 1 else []})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path != "/oauth/token":
            self._json(404, {"error": "not found"})
            return

        auth_header = self.headers.get("Authorization", "")
        if not _oauth2_client_auth_ok(auth_header):
            self._json(401, {"error": "invalid_client"})
            return

        token = uuid.uuid4().hex
        _ISSUED_TOKENS[token] = time.time() + OAUTH2_TOKEN_TTL_S
        self._json(200, {
            "access_token": token, "token_type": "bearer", "expires_in": OAUTH2_TOKEN_TTL_S,
        })

    def log_message(self, format, *args):  # keep CI logs uncluttered
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
