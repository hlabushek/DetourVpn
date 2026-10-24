"""Loopback-only verified TLS hop to a scoped SSH tunnel. No public endpoint/logging."""

import http.client
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PATHS = frozenset("/api/internal/v3/windows/" + name for name in ("inventory", "start", "poll"))


class Relay(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler interface
        connection = http.client.HTTPConnection("127.0.0.1", 18443, timeout=6)
        try:
            if self.path not in PATHS or self.headers.get("Transfer-Encoding") is not None:
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= 131072:
                self.send_error(413)
                return
            self.connection.settimeout(8)
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError("short_request")
            headers = {"Content-Type": "application/json"}
            for name in (
                "X-Agent-Node-Id",
                "X-Agent-Credential",
                "X-Agent-Timestamp",
                "X-Agent-Nonce",
            ):
                value = self.headers.get(name)
                if value is None or len(value) > 256:
                    self.send_error(401)
                    return
                headers[name] = value
            connection.request("POST", self.path, body=data, headers=headers)
            response = connection.getresponse()
            output = response.read(131073)
            if len(output) > 131072 or response.status not in {
                200,
                401,
                403,
                404,
                409,
                422,
                429,
                503,
            }:
                raise ValueError("invalid_upstream_response")
            self.send_response(response.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(output)
        except (OSError, ValueError, http.client.HTTPException):
            self.send_error(503)
        finally:
            connection.close()


def main() -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    root = Path("/run/detourvpn-i028")
    context.load_cert_chain(root / "localhost.crt", root / "localhost.key")
    server = ThreadingHTTPServer(("127.0.0.1", 443), Relay)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
