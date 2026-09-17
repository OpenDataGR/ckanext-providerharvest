"""Throwaway HTTP JSON API standing in for a real provider, for the
integration test in ckanext/providerharvest/tests/integration/. Serves a
fixed page of records on page 1 and an empty page afterwards, matching
DirectHTTPSTransport's page_number pagination style (see
transport/direct_https.py's list_entries: it stops once a page comes back
with no records).
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

RECORDS = [
    {"id": 1, "name": "Alpha"},
    {"id": 2, "name": "Bravo"},
    {"id": 3, "name": "Charlie"},
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        page = int(query.get("page", ["1"])[0])
        body = json.dumps({"results": RECORDS if page == 1 else []}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # keep CI logs uncluttered
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
