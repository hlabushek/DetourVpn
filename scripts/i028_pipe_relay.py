"""TEST ONLY loopback WSL-to-Windows HTTP over a private child-process pipe.

WSL NAT cannot reach Windows loopback. Neither HTTP listener binds a LAN address.
Only the three authenticated v3 node paths are accepted; no credentials are logged.
"""

import base64
import http.client
import json
import os
import select
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

PATHS = {
    "/api/internal/v3/windows/inventory",
    "/api/internal/v3/windows/start",
    "/api/internal/v3/windows/poll",
}
HEADERS = {"x-agent-node-id", "x-agent-timestamp", "x-agent-nonce", "x-agent-credential"}
LIMIT = 131072
WSL = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe")


def worker() -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802 - stdlib protocol method.
            if self.path not in PATHS or self.headers.get("Transfer-Encoding"):
                self.send_error(400)
                return
            try:
                self.connection.settimeout(8)
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= size <= LIMIT:
                    raise ValueError
                headers = {k.lower(): v for k, v in self.headers.items() if k.lower() in HEADERS}
                if set(headers) != HEADERS or any(len(v) > 256 for v in headers.values()):
                    raise ValueError
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError
                print(
                    json.dumps(
                        {
                            "path": self.path,
                            "headers": headers,
                            "body": base64.b64encode(data).decode(),
                        }
                    ),
                    flush=True,
                )
                line = sys.stdin.buffer.readline(LIMIT * 2)
                response = json.loads(line)
                body = base64.b64decode(response["body"], validate=True)
                if len(body) > LIMIT or response["status"] not in (
                    200,
                    400,
                    401,
                    403,
                    409,
                    422,
                    429,
                    503,
                ):
                    raise ValueError
                self.send_response(response["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError, KeyError):
                self.send_error(503)

    server = HTTPServer(("127.0.0.1", 18767), Handler)
    server.timeout = 0.5
    print('"ready"', flush=True)
    try:
        # Outside a request, readability means parent closed its response pipe.
        while not select.select([sys.stdin.buffer], [], [], 0)[0]:
            server.handle_request()
    finally:
        server.server_close()


class PipeRelay:
    def __init__(self, source: Path) -> None:
        translated = (
            subprocess.run(  # noqa: S603 - fixed wsl path conversion.
                [WSL, "-d", "kali-linux", "--", "wslpath", "-a", source.as_posix()],
                capture_output=True,
                check=True,
                timeout=15,
            )
            .stdout.decode()
            .strip()
        )
        self.process = subprocess.Popen(  # noqa: S603 - fixed reviewed child script.
            [WSL, "-d", "kali-linux", "--", "python3", "-u", translated, "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self.ready.wait(15):
            self.close()
            raise RuntimeError("i028_loopback_pipe_not_ready")

    def _run(self) -> None:
        if self.process.stdout is None or self.process.stdin is None:
            return
        if self.process.stdout.readline() != b'"ready"\n':
            return
        self.ready.set()
        for line in self.process.stdout:
            connection = http.client.HTTPConnection("127.0.0.1", 18766, timeout=8)
            result: dict[str, Any] = {"status": 503, "body": ""}
            try:
                request = json.loads(line)
                if request["path"] not in PATHS or set(request["headers"]) != HEADERS:
                    raise ValueError
                body = base64.b64decode(request["body"], validate=True)
                if len(body) > LIMIT:
                    raise ValueError
                connection.request(
                    "POST",
                    request["path"],
                    body,
                    {**request["headers"], "Content-Type": "application/json"},
                )
                response = connection.getresponse()
                content = response.read(LIMIT + 1)
                if len(content) > LIMIT:
                    raise ValueError
                result = {"status": response.status, "body": base64.b64encode(content).decode()}
            except (OSError, ValueError, KeyError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            try:
                self.process.stdin.write(json.dumps(result).encode() + b"\n")
                self.process.stdin.flush()
            except OSError:
                break

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.thread.join(timeout=10)
        if self.process.stdout is not None:
            self.process.stdout.close()


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit("fixed_worker_mode_required")
    worker()
