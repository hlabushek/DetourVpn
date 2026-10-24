"""Opt-in real WSL pipe transport; synthetic local HTTP peer, no staging or phone."""

import importlib.util
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    os.environ.get("VPN_I028_LOOPBACK_TEST") != "1", reason="WSL loopback drill not requested"
)


def test_real_wsl_pipe_roundtrip_and_scope() -> None:
    spec = importlib.util.spec_from_file_location("i028_pipe", ROOT / "scripts/i028_pipe_relay.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            assert self.path == "/api/internal/v3/windows/poll"
            assert self.headers["X-Agent-Credential"] == "TEST ONLY SYNTHETIC"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert body == b'{"synthetic":true}'
            self.send_response(200)
            self.send_header("Content-Length", "15")
            self.end_headers()
            self.wfile.write(b'{"passed":true}')

    server = HTTPServer(("127.0.0.1", 18766), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bridge = None
    try:
        bridge = module.PipeRelay(ROOT / "scripts/i028_pipe_relay.py")
        code = """import http.client,json
c=http.client.HTTPConnection('127.0.0.1',18767,timeout=8)
h={'X-Agent-Node-Id':'TEST ONLY','X-Agent-Credential':'TEST ONLY SYNTHETIC',
   'X-Agent-Timestamp':'TEST ONLY','X-Agent-Nonce':'TEST ONLY'}
c.request('POST','/api/internal/v3/windows/poll',b'{"synthetic":true}',h)
r=c.getresponse(); assert r.status==200 and json.loads(r.read())=={'passed':True}; c.close()
c=http.client.HTTPConnection('127.0.0.1',18767,timeout=8)
c.request('POST','/api/v1/auth/refresh',b'{}',h)
r=c.getresponse(); assert r.status==400; r.read(); c.close()
print('roundtrip_and_path_rejection_passed')
"""
        result = subprocess.run(  # noqa: S603 - fixed local isolated drill.
            [module.WSL, "-d", "kali-linux", "--", "python3", "-c", code],
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == b"roundtrip_and_path_rejection_passed"
    finally:
        if bridge is not None:
            bridge.close()
            assert bridge.process.poll() is not None and not bridge.thread.is_alive()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    (ROOT / "docs/evidence/i-028-loopback-transport.json").write_text(
        json.dumps(
            {
                "passed": True,
                "real_wsl_windows_loopback_pipe": True,
                "synthetic_local_http_only": True,
                "forbidden_path_rejected": True,
                "child_and_listener_cleanup": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
