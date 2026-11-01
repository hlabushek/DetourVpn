from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.verify_beta_apk import (
    VerificationError,
    _read_package_metadata,
    _read_signer_fingerprints,
    _run_tool,
    create_receipt,
)


def test_tool_output_replaces_non_utf8_bytes_from_windows_sdk() -> None:
    completed = subprocess.CompletedProcess(
        args=["android-sdk-tool"], returncode=0, stdout="tool output \ufffd", stderr=""
    )
    with patch("scripts.verify_beta_apk.subprocess.run", return_value=completed) as run_tool:
        assert _run_tool(("android-sdk-tool",)) == "tool output \ufffd"
    assert run_tool.call_args.kwargs["encoding"] == "utf-8"
    assert run_tool.call_args.kwargs["errors"] == "replace"


def test_package_metadata_parser_reads_only_required_fields(tmp_path: Path) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"test apk fixture")
    output = (
        "package: name='com.detourvpn.beta' versionCode='4' "
        "versionName='0.2.0-beta.2' platformBuildVersionCode='36'\n"
    )
    with patch("scripts.verify_beta_apk._run_tool", return_value=output):
        metadata = _read_package_metadata(Path("aapt"), apk)
    assert metadata == {
        "application_id": "com.detourvpn.beta",
        "version_code": 4,
        "version_name": "0.2.0-beta.2",
    }


def test_signature_parser_requires_verified_signer_fingerprint(tmp_path: Path) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"test apk fixture")
    fingerprint = "ab" * 32
    with patch(
        "scripts.verify_beta_apk._run_tool",
        return_value=f"Verifies\nSigner #1 certificate SHA-256 digest: {fingerprint}\n",
    ):
        assert _read_signer_fingerprints(Path("apksigner"), apk) == [fingerprint]
    with patch("scripts.verify_beta_apk._run_tool", return_value="Verifies\n"):
        with pytest.raises(VerificationError, match="no SHA-256 signer fingerprint"):
            _read_signer_fingerprints(Path("apksigner"), apk)


def test_signer_verification_uses_jar_launcher_instead_of_windows_batch_file(
    tmp_path: Path,
) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"test apk fixture")
    fingerprint = "ef" * 32
    output = f"Signer #1 certificate SHA-256 digest: {fingerprint}\n"
    with (
        patch("scripts.verify_beta_apk._find_java", return_value=Path("java.exe")),
        patch("scripts.verify_beta_apk._run_tool", return_value=output) as run_tool,
    ):
        assert _read_signer_fingerprints(Path("apksigner.jar"), apk) == [fingerprint]
    assert run_tool.call_args.args[0] == (
        "java.exe",
        "-jar",
        "apksigner.jar",
        "verify",
        "--print-certs",
        str(apk),
    )


def test_receipt_is_sanitized_and_never_claims_update_or_distribution(
    tmp_path: Path,
) -> None:
    apk = tmp_path / "owner-private-directory" / "beta.apk"
    apk.parent.mkdir()
    apk.write_bytes(b"test apk fixture")
    output = tmp_path / "receipt.json"
    fingerprint = "cd" * 32
    metadata = {
        "application_id": "com.detourvpn.beta",
        "version_code": 4,
        "version_name": "0.2.0-beta.2",
    }

    with (
        patch(
            "scripts.verify_beta_apk.find_android_build_tools",
            return_value=(Path("aapt"), Path("apksigner")),
        ),
        patch("scripts.verify_beta_apk._read_package_metadata", return_value=metadata),
        patch("scripts.verify_beta_apk._read_signer_fingerprints", return_value=[fingerprint]),
    ):
        receipt = create_receipt(apk, "com.detourvpn.beta", output)

    serialized = output.read_text(encoding="utf-8")
    assert json.loads(serialized) == receipt
    assert str(apk) not in serialized
    assert "owner-private-directory" not in serialized
    assert receipt["update_install"] == "not_verified"
    assert receipt["distribution"] == "not_accepted"
    expected_hash = hashlib.sha256(b"test apk fixture").hexdigest()
    assert receipt["artifact_sha256"] == expected_hash


@pytest.mark.parametrize("application_id", ["org.example.vpnclient", "com.example.detourvpn.beta"])
def test_placeholder_application_id_is_rejected_before_tool_access(
    tmp_path: Path,
    application_id: str,
) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"test apk fixture")
    with patch("scripts.verify_beta_apk.find_android_build_tools") as find_tools:
        with pytest.raises(VerificationError, match="stable non-placeholder"):
            create_receipt(apk, application_id, tmp_path / "receipt.json")
    find_tools.assert_not_called()


def test_failed_signature_does_not_create_receipt(tmp_path: Path) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"test apk fixture")
    output = tmp_path / "receipt.json"
    metadata = {
        "application_id": "com.detourvpn.beta",
        "version_code": 4,
        "version_name": "0.2.0-beta.2",
    }
    with (
        patch(
            "scripts.verify_beta_apk.find_android_build_tools",
            return_value=(Path("aapt"), Path("apksigner")),
        ),
        patch("scripts.verify_beta_apk._read_package_metadata", return_value=metadata),
        patch(
            "scripts.verify_beta_apk._read_signer_fingerprints",
            side_effect=VerificationError(
                "APK signature verification returned no SHA-256 signer fingerprint"
            ),
        ),
    ):
        with pytest.raises(VerificationError, match="no SHA-256 signer fingerprint"):
            create_receipt(apk, "com.detourvpn.beta", output)
    assert not output.exists()


def test_apk_changed_during_verification_does_not_create_receipt(tmp_path: Path) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"original APK bytes")
    output = tmp_path / "receipt.json"
    metadata = {
        "application_id": "com.detourvpn.beta",
        "version_code": 4,
        "version_name": "0.2.0-beta.2",
    }

    def change_apk_after_signature_check(*_args: object, **_kwargs: object) -> list[str]:
        apk.write_bytes(b"changed APK bytes")
        return ["ab" * 32]

    with (
        patch(
            "scripts.verify_beta_apk.find_android_build_tools",
            return_value=(Path("aapt"), Path("apksigner")),
        ),
        patch("scripts.verify_beta_apk._read_package_metadata", return_value=metadata),
        patch(
            "scripts.verify_beta_apk._read_signer_fingerprints",
            side_effect=change_apk_after_signature_check,
        ),
    ):
        with pytest.raises(VerificationError, match="changed while verification"):
            create_receipt(apk, "com.detourvpn.beta", output)
    assert not output.exists()


def test_existing_receipt_is_not_overwritten(tmp_path: Path) -> None:
    apk = tmp_path / "beta.apk"
    apk.write_bytes(b"test apk fixture")
    output = tmp_path / "receipt.json"
    output.write_text("preserve", encoding="utf-8")
    with patch("scripts.verify_beta_apk.find_android_build_tools") as find_tools:
        with pytest.raises(VerificationError, match="already exists"):
            create_receipt(apk, "com.detourvpn.beta", output)
    find_tools.assert_not_called()
    assert output.read_text(encoding="utf-8") == "preserve"
