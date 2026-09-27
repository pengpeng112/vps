# ci-local.ps1 - local check suite for M08 (ASCII only; PS5.1 safe)
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci-local.ps1 [-WithDB]
# -WithDB requires a local isolated postgres on 127.0.0.1:55432 (docker run --name m08-testdb ...)
# NOTE: no remote CI exists yet; this is the local gate before any manual publish.
param([switch]$WithDB)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$fail = 0

Write-Output '== 1. agent shell scripts syntax (busybox sh -n) =='
$sh = @("$env:ProgramFiles\Git\bin\sh.exe", 'D:\Program Files\Git\bin\sh.exe', "$env:ProgramFiles\Git\usr\bin\sh.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $sh) { Write-Output 'SKIP: no git sh.exe found'; $sh = 'sh' }
Get-ChildItem "$root\mgmt\agent" -Filter *.sh -Recurse | ForEach-Object {
    & $sh -n $_.FullName
    if ($LASTEXITCODE -ne 0) { Write-Output "FAIL: $($_.FullName)"; $fail = 1 }
}
Write-Output '== 2. python scripts compile =='
$py = @('export_zcode_sessions.py','test_export_redact.py','webprobe.py','webprobe_report.py','mgmt_api_test.py') |
    ForEach-Object { Join-Path $PSScriptRoot $_ }
& python -m py_compile @py
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL: py_compile'; $fail = 1 }
Write-Output '== 3. redaction regression (fictional samples) =='
Push-Location $PSScriptRoot; & python test_export_redact.py; Pop-Location
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL: redact tests'; $fail = 2 }
Write-Output '== 4. backend go test =='
Push-Location "$root\mgmt\backend"
if ($WithDB) {
    $env:ROUTERMGMT_TEST_DSN = 'postgres://mgmt:m@127.0.0.1:55432/mgmt_test?sslmode=disable'
} else {
    Remove-Item Env:ROUTERMGMT_TEST_DSN -ErrorAction SilentlyContinue
}
& go test ./...
if ($LASTEXITCODE -ne 0) { Write-Output 'FAIL: go test'; $fail = 3 }
Pop-Location
if ($fail -ne 0) { Write-Output "RESULT: FAIL ($fail)"; exit 1 }
Write-Output 'RESULT: PASS'
