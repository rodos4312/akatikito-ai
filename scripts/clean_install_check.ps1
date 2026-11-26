<#
.SYNOPSIS
Clean-install, zero-PATH boot, Doctor, upgrade, and rollback verification for Akatikito portable ZIP (F2).
#>
param(
    [string]$OldZip = "",
    [string]$NewZip = "",
    [string]$WorkDir = "",
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $WorkDir) {
    $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("akatikito-clean-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
}
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$Lines = [System.Collections.Generic.List[string]]::new()
$Failed = $false

function Record([string]$Key, [string]$Status, [string]$Detail = "") {
    $line = if ($Detail) { "${Key}: ${Status} ($Detail)" } else { "${Key}: ${Status}" }
    $Lines.Add($line)
    Write-Output $line
    if ($Status -eq "FAIL") { $script:Failed = $true }
}

function Run-ZeroPath([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory, [string]$TempDir) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.EnvironmentVariables.Clear()
    $psi.EnvironmentVariables["SystemRoot"] = $env:SystemRoot
    $psi.EnvironmentVariables["ComSpec"] = $env:ComSpec
    $psi.EnvironmentVariables["TEMP"] = $TempDir
    $psi.EnvironmentVariables["TMP"] = $TempDir
    $psi.EnvironmentVariables["PATH"] = ""
    $psi.EnvironmentVariables["PYTHONUTF8"] = "1"
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    if (-not $proc.WaitForExit(60000)) {
        try { $proc.Kill() } catch {}
        return @{ Code = 124; Out = $stdout; Err = "Timeout" }
    }
    return @{ Code = $proc.ExitCode; Out = $stdout; Err = $stderr }
}

try {
    # 1. Build portable ZIPs if caller did not pass prebuilt archives
    if (-not $OldZip) {
        $pyHost = Join-Path $RepoRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path $pyHost)) { $pyHost = "python" }
        $oldOutDir = Join-Path $WorkDir "dist-old"
        & $pyHost (Join-Path $RepoRoot "packaging\build_portable.py") --out-dir $oldOutDir --version "2.5.0-clean" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to build portable ZIP" }
        $OldZip = (Get-ChildItem -Path $oldOutDir -Filter "*.zip" | Select-Object -First 1).FullName
    }
    if (-not $NewZip) {
        $pyHost = Join-Path $RepoRoot ".venv\Scripts\python.exe"
        if (-not (Test-Path $pyHost)) { $pyHost = "python" }
        $newOutDir = Join-Path $WorkDir "dist-new"
        & $pyHost (Join-Path $RepoRoot "packaging\build_portable.py") --no-python --out-dir $newOutDir --version "2.5.1-clean" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Failed to build update ZIP" }
        $NewZip = (Get-ChildItem -Path $newOutDir -Filter "*.zip" | Select-Object -First 1).FullName
    }

    $zipBytes = (Get-Item $OldZip).Length
    $zipMb = [math]::Round($zipBytes / 1MB, 2)
    Record "PORTABLE_ZIP" "PASS" "$([System.IO.Path]::GetFileName($OldZip)), ${zipMb} MB"

    # 2. Extract into clean install folder
    $InstallRoot = Join-Path $WorkDir "install"
    $ZeroTemp = Join-Path $WorkDir "zero-tmp"
    New-Item -ItemType Directory -Force -Path $InstallRoot, $ZeroTemp | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($OldZip, $InstallRoot)
    $AppDir = Join-Path $InstallRoot "Akatikito"
    $BundledPy = Join-Path $AppDir "python\python.exe"
    if (-not (Test-Path $BundledPy)) {
        Record "EXTRACT_BUNDLED_PYTHON" "FAIL" "Missing python\python.exe"
        throw "Bundled python.exe missing"
    }
    Record "EXTRACT_BUNDLED_PYTHON" "PASS" "python\python.exe present"

    # 3. Seed realistic user data (settings.json + real SQLite memory.db) and record SHA-256 BEFORE
    $DataDir = Join-Path $AppDir "data"
    $MemDir = Join-Path $DataDir "memory"
    New-Item -ItemType Directory -Force -Path $MemDir | Out-Null
    $SettingsFile = Join-Path $DataDir "settings.json"
    $MemoryDb = Join-Path $MemDir "memory.db"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($SettingsFile, '{"provider":"OpenAI","daily_budget":2.5}', $utf8NoBom)
    $seedRes = Run-ZeroPath $BundledPy @("-c", "from pathlib import Path; from akatikito.memory_store import MemoryStore; s = MemoryStore(Path(r'$MemoryDb')); s.add('Clean-install owner memory'); s.close()") $AppDir $ZeroTemp
    if ($seedRes.Code -ne 0) { throw "Failed to seed real SQLite memory.db: $($seedRes.Err)" }
    $HashSettingsBefore = (Get-FileHash -Path $SettingsFile -Algorithm SHA256).Hash
    $HashMemoryBefore = (Get-FileHash -Path $MemoryDb -Algorithm SHA256).Hash

    # 4. Zero-PATH --version and --headless-check
    $verRes = Run-ZeroPath $env:ComSpec @("/c", (Join-Path $AppDir "AkatikitoApp.cmd"), "--version") $AppDir $ZeroTemp
    if ($verRes.Code -eq 0 -and $verRes.Out -match "Akatikito") {
        Record "ZERO_PATH_VERSION" "PASS" ($verRes.Out.Trim())
    } else {
        Record "ZERO_PATH_VERSION" "FAIL" "rc=$($verRes.Code) $($verRes.Err.Trim())"
    }

    $hlRes = Run-ZeroPath $env:ComSpec @("/c", (Join-Path $AppDir "AkatikitoApp.cmd"), "--headless-check") $AppDir $ZeroTemp
    if ($hlRes.Code -eq 0 -and $hlRes.Out -match "OK: headless-check") {
        Record "ZERO_PATH_HEADLESS_BOOT" "PASS" ($hlRes.Out.Trim())
    } else {
        Record "ZERO_PATH_HEADLESS_BOOT" "FAIL" "rc=$($hlRes.Code) $($hlRes.Out.Trim()) $($hlRes.Err.Trim())"
    }

    # 5. Zero-PATH Doctor health check
    $docRes = Run-ZeroPath $BundledPy @("-m", "akatikito.doctor", "--json") $AppDir $ZeroTemp
    if ($docRes.Out -and $docRes.Out.Trim().StartsWith("{")) {
        $docJson = $docRes.Out | ConvertFrom-Json
        $docErrs = @($docJson.checks | Where-Object { $_.status -eq "error" })
        if ($docRes.Code -eq 0 -and $docErrs.Count -eq 0) {
            Record "ZERO_PATH_DOCTOR" "PASS" "badge=$($docJson.badge), errors=0"
        } else {
            $errIds = ($docErrs | ForEach-Object { "$($_.id): $($_.details)" }) -join "; "
            Record "ZERO_PATH_DOCTOR" "FAIL" "rc=$($docRes.Code) $errIds"
        }
    } else {
        Record "ZERO_PATH_DOCTOR" "FAIL" "rc=$($docRes.Code) $($docRes.Err.Trim())"
    }

    # 6. Upgrade from OldZip to NewZip + Rollback using bundled Python (zero PATH)
    $NewSums = Join-Path (Split-Path $NewZip -Parent) "SHA256SUMS.txt"
    $updScript = Join-Path $WorkDir "run_upgrade_rollback.py"
    @"
import json, sys
from pathlib import Path
from akatikito.github_updater import apply_github_update, restore_github_update_backup

app_dir = Path(sys.argv[1])
new_zip = Path(sys.argv[2])
new_sums = Path(sys.argv[3])
zip_url = f"https://github.com/rodos4312/akatikito-ai/releases/download/v2.5.1/{new_zip.name}"
sha_url = "https://github.com/rodos4312/akatikito-ai/releases/download/v2.5.1/SHA256SUMS.txt"
def fetcher(url, _m, _a, _t):
    return new_sums.read_bytes() if url.endswith("SHA256SUMS.txt") else new_zip.read_bytes()
upd = apply_github_update(app_dir, zip_url=zip_url, sha_url=sha_url, zip_name=new_zip.name, fetcher=fetcher)
print(json.dumps({"phase": "upgrade", "ok": upd["ok"], "version": upd["version"], "backup_dir": upd["backup_dir"]}))
"@ | Set-Content -Path $updScript -Encoding UTF8

    $updRes = Run-ZeroPath $BundledPy @($updScript, $AppDir, $NewZip, $NewSums) $AppDir $ZeroTemp
    if ($updRes.Code -ne 0) {
        Record "UPGRADE_APPLY" "FAIL" "rc=$($updRes.Code) $($updRes.Err.Trim())"
        throw "Upgrade failed"
    }
    $updInfo = $updRes.Out.Trim() | ConvertFrom-Json
    Record "UPGRADE_APPLY" "PASS" "version=$($updInfo.version)"

    $HashSettingsUpd = (Get-FileHash -Path $SettingsFile -Algorithm SHA256).Hash
    $HashMemoryUpd = (Get-FileHash -Path $MemoryDb -Algorithm SHA256).Hash
    if ($HashSettingsUpd -eq $HashSettingsBefore -and $HashMemoryUpd -eq $HashMemoryBefore) {
        Record "DATA_HASH_AFTER_UPGRADE" "PASS" "settings+memory byte-for-byte identical"
    } else {
        Record "DATA_HASH_AFTER_UPGRADE" "FAIL" "user data hash changed after upgrade"
    }

    # 7. Rollback from backup + verify user data hashes still identical
    $rbScript = Join-Path $WorkDir "run_rollback.py"
    @"
import json, sys
from pathlib import Path
from akatikito.github_updater import restore_github_update_backup
res = restore_github_update_backup(Path(sys.argv[1]), Path(sys.argv[2]))
print(json.dumps(res))
"@ | Set-Content -Path $rbScript -Encoding UTF8

    $rbRes = Run-ZeroPath $BundledPy @($rbScript, $AppDir, $updInfo.backup_dir) $AppDir $ZeroTemp
    if ($rbRes.Code -ne 0) {
        Record "ROLLBACK_RESTORE" "FAIL" "rc=$($rbRes.Code) $($rbRes.Err.Trim())"
    } else {
        $rbInfo = $rbRes.Out.Trim() | ConvertFrom-Json
        Record "ROLLBACK_RESTORE" "PASS" "restored_files=$($rbInfo.restored_files)"
    }

    $HashSettingsRb = (Get-FileHash -Path $SettingsFile -Algorithm SHA256).Hash
    $HashMemoryRb = (Get-FileHash -Path $MemoryDb -Algorithm SHA256).Hash
    if ($HashSettingsRb -eq $HashSettingsBefore -and $HashMemoryRb -eq $HashMemoryBefore) {
        Record "DATA_HASH_AFTER_ROLLBACK" "PASS" "settings+memory byte-for-byte identical"
    } else {
        Record "DATA_HASH_AFTER_ROLLBACK" "FAIL" "user data hash changed after rollback"
    }
} catch {
    Record "EXCEPTION" "FAIL" $_.Exception.Message
}

$final = if ($Failed) { "RESULT: FAIL" } else { "RESULT: PASS" }
$Lines.Add($final)
Write-Output $final
if ($ReportPath) {
    $parent = Split-Path $ReportPath -Parent
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $Lines | Set-Content -Path $ReportPath -Encoding UTF8
}
if ($Failed) { exit 1 } else { exit 0 }
