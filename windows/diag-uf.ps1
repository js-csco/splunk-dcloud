# ===========================================================================
# diag-uf.ps1 - diagnose why Windows UF data (WinEventLog/perfmon) isn't showing
# up in Splunk. Run in an ELEVATED PowerShell on the Windows client:
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   iwr https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/windows/diag-uf.ps1 -UseBasicParsing | iex
#
# Paste the whole output back. It answers three questions:
#   1) Is our inputs.conf actually loaded by splunkd?      (btool)
#   2) Are the WinEventLog inputs erroring in the logs?    (splunkd.log)
#   3) Is the UF running as an account that can read logs? (service identity)
# ===========================================================================
$ErrorActionPreference = "Continue"
$UF     = "$env:ProgramFiles\SplunkUniversalForwarder"
$AdminPw = $env:SPLUNK_ADMIN_PASSWORD; if (-not $AdminPw) { $AdminPw = "C1sco12345" }
$splunk = "$UF\bin\splunk.exe"

function H($t) { Write-Host ""; Write-Host "===== $t =====" }

H "UF version + host"
& $splunk version 2>&1 | Write-Host
Write-Host "host = $env:COMPUTERNAME"

H "1) inputs splunkd will actually use (btool) - look for [WinEventLog://...] + which file"
& $splunk cmd btool inputs list --debug 2>&1 |
  Select-String -Pattern "WinEventLog|perfmon|TA-dcloud-win|index =|disabled" | Write-Host

H "2) Is the TA-dcloud-win app present on disk?"
$app = "$UF\etc\apps\TA-dcloud-win"
if (Test-Path $app) {
  Get-ChildItem -Recurse $app | Select-Object FullName | Format-Table -AutoSize | Out-String | Write-Host
  Write-Host "--- local\inputs.conf ---"
  Get-Content "$app\local\inputs.conf" -ErrorAction SilentlyContinue | Write-Host
  Write-Host "--- default\app.conf ---"
  Get-Content "$app\default\app.conf" -ErrorAction SilentlyContinue | Write-Host
} else {
  Write-Host "MISSING: $app does not exist (app never written)."
}

H "3) inputstatus (WinEventLog section should list Security/System/Application)"
& $splunk list inputstatus -auth "admin:$AdminPw" 2>&1 | Write-Host

H "4) Which account runs the UF service? (LocalSystem can read the Security log)"
Get-CimInstance Win32_Service -Filter "Name='SplunkForwarder'" |
  Select-Object Name, State, StartName | Format-List | Out-String | Write-Host

H "5) Recent splunkd.log lines about WinEventLog / event log / TcpOutput"
$logf = "$UF\var\log\splunk\splunkd.log"
if (Test-Path $logf) {
  Get-Content $logf -Tail 400 |
    Select-String -Pattern "WinEventLog|EventLog|WEL|TcpOutputProc|Connected to idx|london_windows|cooked" |
    Select-Object -Last 40 | Write-Host
} else {
  Write-Host "no splunkd.log at $logf"
}

Write-Host ""
Write-Host "Now on the SPLUNK box (as gary/admin), run over Last 60 minutes:"
Write-Host "    index=_internal host=$env:COMPUTERNAME | stats count by sourcetype"
Write-Host "  -> events here = forwarding works, so the gap is purely the WinEventLog inputs above."
Write-Host "  -> nothing here = the UF isn't shipping at all (receiver/network), inputs are moot."
