"""Real isolated WSL namespace/NAT/AWG lab; systemd scheduling is explicitly simulated."""

import hashlib
import importlib.util
import io
import json
import os
import secrets
import select
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
DNS_SERVER = """import socket
# SYNTHETIC LOCAL RESPONDER ONLY: example.com A -> fake WAN HTTP address.
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
    s.bind(('1.1.1.1',53))
    while True:
        q,peer=s.recvfrom(512)
        if len(q)>=12:
            s.sendto(q[:2]+bytes.fromhex('81800001000100000000')+q[12:]+
                     bytes.fromhex('c00c0001000100000000000408080808'),peer)
"""
DNS_QUERY = """import socket,sys
q=bytes.fromhex('492801000001000000000000076578616d706c6503636f6d0000010001')
try:
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
        s.settimeout(2);s.connect(('1.1.1.1',53));s.send(q);r=s.recv(512)
    if r[:4]!=bytes.fromhex('49288180') or r[-4:]!=bytes([8,8,8,8]):sys.exit(1)
    print('I028 LOCAL DNS PASS')
except OSError:sys.exit(1)
"""


def main() -> int:
    report: dict[str, Any] = {
        "scope": "isolated WSL net/mount namespace, real AWG/NAT/kernel; simulated systemd",
        "wan_or_android_acceptance": False,
        "passed": False,
    }
    report["root_runtime_sha256"] = hashlib.sha256(
        (ROOT / "infra/staging-v3/runtime.py").read_text().encode()
    ).hexdigest()
    report["source_hashes"] = {
        name: hashlib.sha256((ROOT / name).read_text().encode()).hexdigest()
        for name in (
            "scripts/check_i028_bootstrap.py",
            "infra/staging-v3/install.sh",
            "infra/staging-v3/runtime.py",
            "infra/staging-v3/dns_observer.py",
            "infra/staging-v3/archive_closed.py",
            "infra/staging-v3/tls_relay.py",
            "services/node-agent/window_gate.py",
            "services/node-agent/window_peer.py",
            "services/api/src/vpn_api/tunnels/window_runtime.py",
            "services/api/src/vpn_api/tunnels/window_journal.py",
            "services/api/src/vpn_api/tunnels/v3_contracts.py",
            "services/api/src/vpn_api/tunnels/keys.py",
        )
    }
    if getattr(os, "geteuid", lambda: -1)() != 0 or any(
        Path(f"/proc/self/ns/{name}").stat().st_ino == Path(f"/proc/1/ns/{name}").stat().st_ino
        for name in ("net", "mnt")
    ):
        raise RuntimeError("outer_private_namespaces_required")
    spec = importlib.util.spec_from_file_location(
        "i028_runtime", ROOT / "infra/staging-v3/runtime.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("operator_module_missing")
    runtime: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    runtime.call(["/usr/bin/mount", "--make-rprivate", "/"])
    runtime.call(["/usr/bin/mount", "-t", "tmpfs", "-o", "mode=0700,nosuid,nodev", "tmpfs", "/run"])
    processes: dict[str, subprocess.Popen[bytes]] = {}
    supervision = {"timeout_active": False}
    original = runtime.call
    stage = "lab_environment"
    with tempfile.TemporaryDirectory(prefix="i028-lab-", dir="/root") as directory:
        lab = Path(directory)
        runtime.ROOT = lab
        runtime.AWG, runtime.CORE = lab / "awg", lab / "amneziawg-go"
        runtime.CONFIG_DIR, runtime.STATE_DIR, runtime.GRANT = (
            lab / "lab-config",
            lab / "lab-state",
            lab / "grant",
        )
        for binary in ("awg", "amneziawg-go"):
            expected = {
                "awg": "4bfe07f714b514f9f2c1455f4848dc95666ae356e170830ae53182d4615b00ce",
                "amneziawg-go": "84a3913e432b12850ae19873953b0d25b4aa2f13480453af04422d5910d86ffe",
            }[binary]
            if (
                hashlib.sha256((ROOT / ".cache/i-010/artifacts" / binary).read_bytes()).hexdigest()
                != expected
            ):
                raise RuntimeError("native_artifact_hash_mismatch")
            shutil.copyfile(ROOT / ".cache/i-010/artifacts" / binary, lab / binary)
            (lab / binary).chmod(0o700)
        for name in ("window_gate.py", "window_peer.py"):
            shutil.copyfile(ROOT / "services/node-agent" / name, lab / name)
        shutil.copyfile(ROOT / "infra/staging-v3/tls_relay.py", lab / "tls_relay.py")
        shutil.copyfile(ROOT / "infra/staging-v3/dns_observer.py", lab / "dns_observer.py")

        def service(name: str, command: list[str], *, tls: bool = False) -> None:
            del tls
            if command[0].endswith(".venv/bin/python"):
                command[0] = sys.executable
            processes[name] = subprocess.Popen(  # noqa: S603 - fixed private lab processes.
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=runtime.ENV,
            )

        def stop(name: str) -> None:
            process = processes.get(name)
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        def call(args: list[str], data: str | None = None, *, required: bool = True) -> str:
            if args[0] == "/usr/bin/systemd-run":
                if "--on-active=30m" not in args:
                    raise RuntimeError("unexpected_lab_supervisor_action")
                report["independent_timer_scheduled_before_exposure"] = True
                supervision["timeout_active"] = True
                return ""
            if args[0] == "/usr/bin/systemctl":
                if args[-1] == "detourvpn-i028-timeout.service" and args[1] == "stop":
                    raise RuntimeError("cleanup_must_not_stop_its_own_service")
                if args[-1] == "detourvpn-i028-timeout.timer":
                    if args[1] == "stop":
                        supervision["timeout_active"] = False
                        return ""
                    if args[1] == "is-active" or (
                        args[1] == "show" and "--property=ActiveState" in args
                    ):
                        return "active" if supervision["timeout_active"] else "inactive"
                    raise RuntimeError("unexpected_lab_timer_action")
                name = args[-1].removeprefix("detourvpn-i028-").removesuffix(".service")
                if args[1] == "show":
                    return str(processes[name].pid)
                if args[1] == "stop":
                    stop(name)
                    return ""
                if args[1] == "is-active":
                    return (
                        "active"
                        if name in processes and processes[name].poll() is None
                        else "inactive"
                    )
                raise RuntimeError("unexpected_lab_supervisor_action")
            try:
                value = original(args, data, required=required)
                if not isinstance(value, str):
                    raise RuntimeError("invalid_lab_command_result")
                return value
            except Exception:
                report["failed_command_family"] = Path(args[0]).name
                raise

        runtime.call, runtime.unit = call, service
        try:
            ip = runtime.IP
            call([ip, "link", "set", "lo", "up"])
            # Isolated fake WAN with a documentation-only HTTP probe, never real internet.
            call([ip, "netns", "add", "i028wan"])
            call([ip, "netns", "add", "i028client"])
            call([ip, "-n", "i028wan", "link", "add", "br0", "type", "bridge"])
            call([ip, "-n", "i028wan", "address", "add", "45.10.166.1/24", "dev", "br0"])
            call([ip, "-n", "i028wan", "link", "set", "br0", "up"])
            call([ip, "link", "add", "ens3", "type", "veth", "peer", "name", "wanhost"])
            call([ip, "link", "set", "wanhost", "netns", "i028wan"])
            call([ip, "address", "add", runtime.HOST + "/24", "dev", "ens3"])
            call([ip, "link", "set", "ens3", "up"])
            call([ip, "-n", "i028wan", "link", "set", "wanhost", "master", "br0"])
            call([ip, "-n", "i028wan", "link", "set", "wanhost", "up"])
            call([ip, "route", "add", "default", "via", "45.10.166.1"])
            call(
                [
                    ip,
                    "-n",
                    "i028wan",
                    "link",
                    "add",
                    "wanclient",
                    "type",
                    "veth",
                    "peer",
                    "name",
                    "outer0",
                ]
            )
            call([ip, "-n", "i028wan", "link", "set", "outer0", "netns", "i028client"])
            call([ip, "-n", "i028wan", "link", "set", "wanclient", "master", "br0"])
            call([ip, "-n", "i028wan", "link", "set", "wanclient", "up"])
            call([ip, "-n", "i028client", "address", "add", "45.10.166.200/24", "dev", "outer0"])
            call([ip, "-n", "i028client", "link", "set", "outer0", "up"])
            for namespace in ("i028wan", "i028client"):
                call([ip, "-n", namespace, "link", "set", "lo", "up"])
            call([ip, "-n", "i028wan", "address", "add", "8.8.8.8/32", "dev", "lo"])
            call([ip, "-n", "i028wan", "address", "add", "1.1.1.1/32", "dev", "lo"])
            service("dns", [ip, "netns", "exec", "i028wan", sys.executable, "-c", DNS_SERVER])
            web = lab / "web"
            web.mkdir()
            (web / "probe").write_text("I028 LOCAL SYNTHETIC PACKETS ONLY\n")
            (web / "large").write_bytes(b"X" * (2 * 1048576))
            service(
                "http",
                [
                    ip,
                    "netns",
                    "exec",
                    "i028wan",
                    sys.executable,
                    "-m",
                    "http.server",
                    "8080",
                    "--bind",
                    "8.8.8.8",
                    "--directory",
                    str(web),
                ],
            )
            stage = "actual_operator_prepare"
            result = runtime.prepare()
            if result.get("prepared") is not True or result.get("slot_count") != 2:
                raise RuntimeError("slot_bootstrap_failed")
            report["two_real_closed_slots_bootstrapped"] = True
            stage = "real_private_identity_and_link_guards"
            identity = {"node_id": str(uuid4()), "credential": secrets.token_urlsafe(32)}
            runtime.configure(json.dumps(identity).encode())
            runtime.configure(json.dumps(identity).encode())
            try:
                runtime.configure(
                    json.dumps({**identity, "credential": secrets.token_urlsafe(32)}).encode()
                )
            except runtime.Failure:
                report["real_immutable_private_configuration"] = True
            else:
                raise RuntimeError("identity_was_replaced")
            target = lab / "link-target"
            target.write_text("TEST ONLY")
            alias = lab / "link-alias"
            alias.symlink_to(target)
            try:
                runtime.write_private(alias, "TEST ONLY REPLACEMENT")
            except FileExistsError:
                report["real_no_follow_and_no_overwrite"] = True
            else:
                raise RuntimeError("link_was_followed")
            if (
                target.read_text() != "TEST ONLY"
                or (runtime.CONFIG_DIR / "credential").stat().st_mode & 0o077
            ):
                raise RuntimeError("private_file_protection_failed")
            stage = "verified_loopback_tls"
            context = ssl.create_default_context(cafile=str(runtime.RUN / "localhost.crt"))
            for _ in range(50):
                try:
                    urllib.request.urlopen(
                        "https://localhost/forbidden", context=context, timeout=2
                    )  # noqa: S310 - fixed isolated loopback URL.
                except urllib.error.HTTPError as error:
                    if error.code == 501:
                        break
                    raise
                except urllib.error.URLError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("local_tls_not_ready")
            report["real_certificate_verified_without_system_trust_change"] = True
            stage = "real_awg_client"
            service("client", [ip, "netns", "exec", "i028client", str(runtime.CORE), "-f", "dv3c1"])
            for _ in range(100):
                if Path("/run/amneziawg/dv3c1.sock").exists():
                    break
                time.sleep(0.05)
            secret = call([str(runtime.AWG), "genkey"])
            public = call([str(runtime.AWG), "pubkey"], secret + "\n")
            slot = json.loads((runtime.RUN / "slots.json").read_text())[0]
            config = runtime.RUN / "lab-client.conf"
            runtime.write_private(
                config,
                "[Interface]\nPrivateKey = "
                + secret
                + "\n"
                + runtime.PARAMETERS
                + "HeaderProtectionKey = "
                + slot["header_protection_key"]
                + "\n[Peer]\nPublicKey = "
                + slot["server_public_key"]
                + "\nEndpoint = "
                + slot["endpoint"]
                + "\nAllowedIPs = 0.0.0.0/0\n",
            )
            call([str(runtime.AWG), "setconf", "dv3c1", str(config)])
            config.unlink()
            call([ip, "-n", "i028client", "address", "add", "10.203.71.2/30", "dev", "dv3c1"])
            call([ip, "-n", "i028client", "link", "set", "dv3c1", "mtu", "1280", "up"])
            call([ip, "-n", "i028client", "route", "add", "default", "dev", "dv3c1"])
            call(
                [str(runtime.AWG), "set", "dv3s1", "peer", public, "allowed-ips", "10.203.71.2/32"]
            )
            current = runtime.gate(1)
            try:
                current.install_closed(1048576)
                current.open(20000)

                def curl(target: str) -> bool:
                    return call(
                        [
                            ip,
                            "netns",
                            "exec",
                            "i028client",
                            "/usr/bin/curl",
                            "--fail",
                            "--silent",
                            "--max-time",
                            "3",
                            target,
                        ],
                        required=False,
                    ).startswith("I028 LOCAL")

                def udp_dns() -> bool:
                    return (
                        call(
                            [ip, "netns", "exec", "i028client", sys.executable, "-c", DNS_QUERY],
                            required=False,
                        ).strip()
                        == "I028 LOCAL DNS PASS"
                    )

                stage = "real_nat_transfer"
                if not curl("http://8.8.8.8:8080/probe"):
                    raise RuntimeError("nat_forward_probe_failed")
                report["real_native_tunnel_and_double_nat_transfer"] = True
                stage = "real_udp_dns_nat_transfer"
                if not udp_dns():
                    raise RuntimeError("udp_dns_forward_probe_failed")
                report["real_udp_dns_packets_through_awg_and_double_nat"] = True
                stage = "dns_observation_real_awg"
                observer_spec = importlib.util.spec_from_file_location(
                    "i028_dns_observer", ROOT / "infra/staging-v3/dns_observer.py"
                )
                if observer_spec is None or observer_spec.loader is None:
                    raise RuntimeError("dns_observer_missing")
                observer = importlib.util.module_from_spec(observer_spec)
                observer_spec.loader.exec_module(observer)
                read_fd, write_fd = os.pipe()
                try:
                    def issue_observed_dns() -> None:
                        report["dns_observer_started"] = True
                        fixed_query = DNS_QUERY.replace(
                            "s.settimeout(2);s.connect",
                            "s.bind(('10.203.71.2',42428));s.settimeout(2);s.connect",
                        )
                        output = call(
                            [ip, "netns", "exec", "i028client", sys.executable, "-c", fixed_query]
                        )
                        if output.strip() != "I028 LOCAL DNS PASS":
                            raise RuntimeError("observed_dns_probe_failed")
                        os.write(write_fd, b"done\n")
                        report["dns_observer_query_completed"] = True

                    with os.fdopen(read_fd, "rb") as stop_stream:
                        try:
                            observed = observer.observe(1, issue_observed_dns, stop_stream)
                        except observer.ObservationError as exc:
                            allowed = {
                                "capture_linktype_unavailable", "capture_linktype_unsupported",
                                "capture_unavailable", "capture_not_ready", "capture_ended_early",
                                "capture_sync_timeout", "capture_sync_invalid",
                                "packet_header_invalid", "packet_filter_mismatch",
                                "capture_completion_unknown", "capture_incomplete",
                                "capture_stream_closed", "capture_output_limit",
                            }
                            report["dns_observer_error"] = (
                                str(exc) if str(exc) in allowed else "other"
                            )
                            raise
                    expected = {"inbound": 1, "outbound": 1}
                    if observed.get("counts") != {"tunnel": expected, "uplink": expected}:
                        report["dns_observer_synthetic_counts"] = observed.get("counts")
                        raise RuntimeError("dns_observation_incorrect")
                    report["dns_observer_real_awg_two_points"] = True
                finally:
                    os.close(write_fd)
                stage = "dns_observation_operator_protocol"
                input_read, input_write = os.pipe()
                output_read, output_write = os.pipe()
                child = os.fork()
                if child == 0:
                    try:
                        os.close(input_write)
                        os.close(output_read)
                        os.dup2(input_read, 0)
                        os.dup2(output_write, 1)
                        os.close(input_read)
                        os.close(output_write)
                        sys.stdin = io.TextIOWrapper(os.fdopen(0, "rb", buffering=0))
                        sys.stdout = io.TextIOWrapper(os.fdopen(1, "wb", buffering=0))
                        sys.argv = ["fixed-test-helper", "dns-observe"]
                        os._exit(runtime.main())
                    except BaseException:
                        os._exit(1)
                os.close(input_read)
                os.close(output_write)
                child_waited = False
                try:
                    with os.fdopen(output_read, "rb") as stream:
                        if not select.select([stream], [], [], 8)[0]:
                            raise RuntimeError("dns_operator_not_ready")
                        if stream.readline(256) != b'{"ready":true}\n':
                            raise RuntimeError("dns_operator_not_ready")
                        fixed_query = DNS_QUERY.replace(
                            "s.settimeout(2);s.connect",
                            "s.bind(('10.203.71.2',42428));s.settimeout(2);s.connect",
                        )
                        output = call(
                            [ip, "netns", "exec", "i028client", sys.executable, "-c", fixed_query]
                        )
                        if output.strip() != "I028 LOCAL DNS PASS":
                            raise RuntimeError("dns_operator_probe_failed")
                        os.write(input_write, b"done\n")
                        if not select.select([stream], [], [], 8)[0]:
                            raise RuntimeError("dns_operator_result_timeout")
                        value = json.loads(stream.readline(4096))
                        if value.get("complete") is not True or value.get("counts") != {
                            "tunnel": expected, "uplink": expected
                        }:
                            raise RuntimeError("dns_operator_result_invalid")
                    _, child_status = os.waitpid(child, 0)
                    child_waited = True
                    if os.waitstatus_to_exitcode(child_status) != 0:
                        raise RuntimeError("dns_operator_failed")
                    report["dns_observer_fixed_operator_protocol"] = True
                finally:
                    os.close(input_write)
                    if not child_waited:
                        try:
                            os.kill(child, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        os.waitpid(child, 0)
                report["dns_responder_is_local_synthetic_only"] = True
                stage = "private_slot_metadata_isolation"
                if curl("http://10.203.80.6:8080/probe") or curl(
                    "http://169.254.169.254:8080/probe"
                ):
                    raise RuntimeError("private_destination_not_blocked")
                report["other_slot_and_metadata_destinations_blocked"] = True
                if not curl("http://8.8.8.8:8080/probe"):
                    raise RuntimeError("isolation_negative_was_only_an_outage")
                current.close()
                if current.closed_bytes() <= 0 or curl("http://8.8.8.8:8080/probe") or udp_dns():
                    raise RuntimeError("closed_gate_still_transfers")
                stage = "real_byte_budget_exhaustion"
                current.install_closed(1048576)
                current.open(15000)
                if runtime.quota_exhausted(current) is not False:
                    raise RuntimeError("budget_lab_initial_quota_invalid")
                call(
                    [
                        ip, "netns", "exec", "i028client", "/usr/bin/curl",
                        "--fail", "--silent", "--max-time", "3", "--output", "/dev/null",
                        "http://8.8.8.8:8080/large",
                    ],
                    required=False,
                )
                stage = "budget_live_after_bulk"
                if not current.is_live():
                    raise RuntimeError("budget_lab_deadline_expired")
                stage = "budget_block_probe"
                if curl("http://8.8.8.8:8080/probe") or udp_dns():
                    raise RuntimeError("budget_lab_traffic_not_blocked")
                stage = "budget_live_after_probe"
                if not current.is_live():
                    raise RuntimeError("budget_lab_deadline_expired")
                quota_rows = [
                    row["quota"]
                    for row in json.loads(current.nft(
                        ["-j", "list", "quota", "inet", "detour_window_v3", "budget"]
                    ))["nftables"]
                    if "quota" in row
                ]
                if (
                    len(quota_rows) != 1
                    or quota_rows[0].get("bytes") != 1048576
                    or type(quota_rows[0].get("used")) is not int
                    or quota_rows[0]["used"] < 1048576
                ):
                    raise RuntimeError("budget_lab_quota_not_exhausted")
                if runtime.quota_exhausted(current) is not True:
                    raise RuntimeError("budget_lab_exhaustion_diagnostic_invalid")
                report["real_byte_budget_quota_object"] = quota_rows[0]
                current.close()
                final_bytes = current.closed_bytes()
                report["real_byte_budget_closed_meter_bytes"] = final_bytes
                stage = "budget_final_meter"
                if not 0 < final_bytes <= 1048576:
                    raise RuntimeError("budget_lab_unexpected_final_meter")
                report["real_byte_budget_blocks_traffic_while_gate_timer_live"] = True
                current.install_closed(1048576)
                current.open(200)
                time.sleep(0.4)
                if current.is_live() or curl("http://8.8.8.8:8080/probe") or udp_dns():
                    raise RuntimeError("kernel_deadline_failed")
                report["closed_gate_and_independent_kernel_timeout_block_transfer"] = True
                report["closed_gate_and_kernel_timeout_also_block_udp_dns"] = True
            finally:
                current.dispose()
            stage = "cleanup"
            first_cleanup = runtime.cleanup()
            if (
                first_cleanup.get("cleanup_complete") is not True
                or first_cleanup.get("timeout_timer_disarmed") is not True
                or first_cleanup.get("gate_close_errors_before_core_stop") != 0
            ):
                raise RuntimeError("cleanup_failed")
            if supervision["timeout_active"]:
                raise RuntimeError("old_timer_still_active")
            supervision["timeout_active"] = True  # Older closed run can retain its timer.
            retry_cleanup = runtime.cleanup()
            if (
                retry_cleanup.get("cleanup_complete") is not True
                or retry_cleanup.get("timeout_timer_disarmed") is not True
                or retry_cleanup.get("gate_close_errors_before_core_stop") != 0
            ):
                raise RuntimeError("cleanup_not_idempotent")
            if supervision["timeout_active"]:
                raise RuntimeError("idempotent_cleanup_left_old_timer")
            report["simulated_timeout_disarmed_after_cleanup_and_retry"] = True
            report["operator_cleanup_and_idempotent_retry"] = True
            stage = "closed_archive_real_filesystem"
            archive_spec = importlib.util.spec_from_file_location(
                "i028_archive", ROOT / "infra/staging-v3/archive_closed.py"
            )
            if archive_spec is None or archive_spec.loader is None:
                raise RuntimeError("archive_module_missing")
            archiver: Any = importlib.util.module_from_spec(archive_spec)
            archive_spec.loader.exec_module(archiver)
            fixture = lab / "closed-archive-fixture"
            fixture.mkdir(mode=0o700)
            archiver.SOURCES = {
                name: fixture / name for name in ("release", "node", "state", "run")
            }
            for path in archiver.SOURCES.values():
                path.mkdir(mode=0o700)
            # /run is the private tmpfs; exercise the real copy/move boundary too.
            archiver.SOURCES["run"] = runtime.RUN
            archiver.HELPER = fixture / "control-wrapper"
            archiver.HELPER.write_text("TEST ONLY INERT WRAPPER")
            archiver.ARCHIVES = lab / "closed-archives"
            archiver.ABSENT = (fixture / "absent-runtime",)
            archiver.run = call
            marker = archiver.SOURCES["state"] / "i028-cleanup.json"
            marker.write_text('{"cleanup_complete":false}')
            try:
                archiver.archive_closed()
            except archiver.Refused:
                pass
            else:
                raise RuntimeError("unconfirmed_archive_allowed")
            marker.write_text('{"cleanup_complete":true}')
            secret_alias = archiver.SOURCES["run"] / "slots.json"
            secret_alias.symlink_to(fixture / "missing-secret")
            try:
                archiver.archive_closed()
            except archiver.Refused:
                pass
            else:
                raise RuntimeError("archive_with_dangling_secret_link_allowed")
            secret_alias.unlink()
            marker_backup = marker.with_suffix(".saved")
            marker.rename(marker_backup)
            marker.symlink_to(marker_backup)
            try:
                archiver.archive_closed()
            except archiver.Refused:
                pass
            else:
                raise RuntimeError("archive_with_linked_receipt_allowed")
            marker.unlink()
            marker_backup.rename(marker)

            def active_service(args: list[str], *, required: bool = True) -> str:
                if args == ["/usr/bin/systemctl", "is-active", "detourvpn-i028-agent.service"]:
                    return "active"
                return call(args, required=required)

            archiver.run = active_service
            try:
                archiver.archive_closed()
            except archiver.Refused:
                pass
            else:
                raise RuntimeError("archive_with_live_service_allowed")
            archiver.run = call
            archiver.ARCHIVES.mkdir(mode=0o755)
            archiver.ARCHIVES.chmod(0o755)
            try:
                archiver.archive_closed()
            except archiver.Refused:
                pass
            else:
                raise RuntimeError("public_private_journal_archive_allowed")
            archiver.ARCHIVES.chmod(0o700)
            private_journal = archiver.SOURCES["state"] / "synthetic-journal"
            private_journal.write_bytes(b"TEST ONLY PRIVATE RECEIPT")
            private_journal.chmod(0o600)
            if archiver.SOURCES["run"].stat().st_dev == lab.stat().st_dev:
                raise RuntimeError("cross_filesystem_archive_not_exercised")
            destination = archiver.archive_closed()
            if (
                (destination / "state/synthetic-journal").read_bytes()
                != b"TEST ONLY PRIVATE RECEIPT"
                or destination.stat().st_mode & 0o077
                or any(path.exists() for path in archiver.SOURCES.values())
            ):
                raise RuntimeError("private_archive_preservation_failed")
            report["real_closed_archive_preserves_private_files_and_refuses_unsafe_state"] = True
            report["real_archive_copies_private_run_tmpfs_to_persistent_filesystem"] = True

            reboot_fixture = lab / "closed-archive-after-reboot"
            reboot_fixture.mkdir(mode=0o700)
            archiver.SOURCES = {
                name: reboot_fixture / name for name in ("release", "node", "state", "run")
            }
            for label, path in archiver.SOURCES.items():
                if label != "run":
                    path.mkdir(mode=0o700)
            reboot_marker = archiver.SOURCES["state"] / "i028-cleanup.json"
            reboot_marker.write_text('{"cleanup_complete":true}')
            archiver.HELPER = reboot_fixture / "control-wrapper"
            archiver.HELPER.write_text("TEST ONLY INERT WRAPPER")
            archiver.ARCHIVES = reboot_fixture / "archives"
            archiver.ABSENT = (reboot_fixture / "absent-runtime",)
            reboot_archive = archiver.archive_closed()
            reboot_metadata = json.loads((reboot_archive / "archive.json").read_text())
            if (
                not (reboot_archive / "run").is_dir()
                or any((reboot_archive / "run").iterdir())
                or reboot_metadata.get("missing_sources") != ["run"]
                or archiver.SOURCES["run"].exists()
            ):
                raise RuntimeError("rebooted_archive_did_not_record_missing_run")
            report["rebooted_closed_archive_records_absent_volatile_run"] = True

            symlink_fixture = lab / "closed-archive-dangling-run-link"
            symlink_fixture.mkdir(mode=0o700)
            archiver.SOURCES = {
                name: symlink_fixture / name for name in ("release", "node", "state", "run")
            }
            for label, path in archiver.SOURCES.items():
                if label != "run":
                    path.mkdir(mode=0o700)
            (archiver.SOURCES["state"] / "i028-cleanup.json").write_text(
                '{"cleanup_complete":true}'
            )
            archiver.SOURCES["run"].symlink_to(symlink_fixture / "missing-run-target")
            archiver.HELPER = symlink_fixture / "control-wrapper"
            archiver.HELPER.write_text("TEST ONLY INERT WRAPPER")
            archiver.ARCHIVES = symlink_fixture / "archives"
            archiver.ABSENT = (symlink_fixture / "absent-runtime",)
            try:
                archiver.archive_closed()
            except archiver.Refused:
                pass
            else:
                raise RuntimeError("rebooted_archive_allowed_dangling_run_link")
            report["rebooted_archive_still_refuses_dangling_run_link"] = True
            report["passed"] = True
        except Exception as exc:
            report["failed_stage"] = stage
            if stage == "dns_observation_real_awg":
                report["dns_lab_exception_type"] = type(exc).__name__
                allowed_errors = {
                    "observed_dns_probe_failed", "dns_observer_missing", "dns_observation_incorrect"
                }
                if str(exc) in allowed_errors:
                    report["dns_lab_failure"] = str(exc)
        finally:
            for name in list(processes):
                stop(name)
    print(json.dumps(report, sort_keys=True))
    (ROOT / "docs/evidence/i-028-bootstrap-local-dns-observer.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
