"""Validate Windows staging ACLs using built-in tools and numeric SIDs."""

import base64
import json
import os
import shutil
import subprocess
import threading

import pytest
from cloudrobo_ingestion.filesystem import checked_copy, manifest, remove_staging

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Requires Windows file system ACLs")


def _read_acls(paths):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if powershell is None:
        raise RuntimeError("Windows ACL verification requires PowerShell")
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$paths = ConvertFrom-Json -InputObject $env:CLOUDROBO_TEST_ACL_PATHS
$sidType = [System.Security.Principal.SecurityIdentifier]
$items = @(foreach ($path in $paths) {
    $acl = Get-Acl -LiteralPath $path
    $rules = @($acl.GetAccessRules($true, $true, $sidType) | ForEach-Object {
        @{
            sid = $_.IdentityReference.Value
            allow = ([int]$_.AccessControlType -eq 0)
            rights = [int]$_.FileSystemRights
        }
    })
    @{
        path = $path
        owner = $acl.GetOwner($sidType).Value
        protected = $acl.AreAccessRulesProtected
        sddl = $acl.Sddl
        rules = $rules
    }
})
$result = @{
    user_sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    items = $items
}
[Console]::WriteLine(($result | ConvertTo-Json -Depth 6 -Compress))
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # A pwsh 7 parent can export module paths incompatible with Windows
    # PowerShell 5.1. Let the selected executable construct its own defaults.
    environment = {
        key: value for key, value in os.environ.items() if key.casefold() != "psmodulepath"
    }
    environment["CLOUDROBO_TEST_ACL_PATHS"] = json.dumps([str(path) for path in paths])
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            env=environment,
            check=True,
            capture_output=True,
            encoding="utf-8-sig",
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"PowerShell ACL query failed: {exc.stderr or exc.stdout}") from None
    return json.loads(result.stdout)


def test_windows_copy_has_private_acls_under_a_shared_parent(config, batch_dir):
    icacls = shutil.which("icacls.exe")
    if icacls is None:
        raise RuntimeError("Windows ACL verification requires icacls")
    # Only this test-owned, synthetic-data directory receives broad permissions.
    # The SID form works on both localized and English Windows installations.
    subprocess.run(
        [icacls, str(config.work_dir), "/grant", "*S-1-1-0:(OI)(CI)F"],
        check=True,
        capture_output=True,
        timeout=30,
    )
    source = batch_dir / "data/file-000.parquet"
    original_bytes = source.read_bytes()
    original_stat = source.stat()
    before = _read_acls([config.work_dir, source])
    assert any(
        rule["sid"] == "S-1-1-0" and rule["allow"] and rule["rights"]
        for rule in before["items"][0]["rules"]
    ), "Test parent must grant Everyone access to expose accidental inheritance"

    target = config.work_dir / "private copy 测试"
    checked_copy(batch_dir, target, manifest(batch_dir), threading.Event())
    copied = target / "data/file-000.parquet"
    after = _read_acls([target, copied.parent, copied, source])
    trusted_owners = {after["user_sid"], "S-1-5-18", "S-1-5-32-544"}
    # CPython's Windows 0700 ACL uses OWNER RIGHTS, rather than necessarily
    # naming the owner's account SID directly. Its owner is checked separately.
    trusted_principals = trusted_owners | {"S-1-3-4"}
    for entry in after["items"][:3]:
        assert entry["owner"] in trusted_owners, entry
        allowed = [rule for rule in entry["rules"] if rule["allow"] and rule["rights"]]
        assert allowed, f"Missing restrictive DACL: {entry}"
        assert all(rule["sid"] in trusted_principals for rule in allowed), entry
    assert all(entry["protected"] for entry in after["items"][:2]), after
    assert after["items"][3]["sddl"] == before["items"][1]["sddl"]

    # Exercise the current account's data access and recursive deletion too.
    assert copied.read_bytes() == original_bytes
    with copied.open("ab") as stream:
        stream.write(b"acl-write-check")
    assert copied.read_bytes() == original_bytes + b"acl-write-check"
    remove_staging(target)
    assert not target.exists()
    assert source.read_bytes() == original_bytes
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
