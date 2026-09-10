# ===========================================================================
# install-uf.ps1 - install the Splunk Universal Forwarder on the Windows client
# (windows-server-2022-london) and forward Windows Event Logs + perfmon.
#
# Run in an ELEVATED PowerShell (Run as administrator):
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   iwr https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/windows/install-uf.ps1 -UseBasicParsing | iex
#
# Sends to the indexer:9997:
#   WinEventLog Security/System/Application -> london_windows
#   perfmon CPU + Memory                    -> london_metrics
# No Splunkbase add-on required (plain inputs.conf).
# ===========================================================================
$ErrorActionPreference = "Stop"

$Indexer  = $env:SPLUNK_INDEXER;      if (-not $Indexer)  { $Indexer  = "198.18.1.124" }
$RecvPort = $env:RECV_PORT;           if (-not $RecvPort) { $RecvPort = "9997" }
$AdminPw  = $env:SPLUNK_ADMIN_PASSWORD; if (-not $AdminPw) { $AdminPw = "C1sco12345" }
$MsiUrl   = $env:SPLUNK_UF_MSI_URL
if (-not $MsiUrl) {
  $MsiUrl = "https://download.splunk.com/products/universalforwarder/releases/9.2.1/windows/splunkforwarder-9.2.1-78803f08aabb-x64-release.msi"
}
$UF = "$env:ProgramFiles\SplunkUniversalForwarder"

Write-Host "== Splunk UF on $env:COMPUTERNAME -> $Indexer`:$RecvPort =="

# 1) install the UF (MSI) if not present
if (-not (Test-Path "$UF\bin\splunk.exe")) {
  $msi = "$env:TEMP\splunkuf.msi"
  Write-Host "Downloading UF MSI: $MsiUrl"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-WebRequest -Uri $MsiUrl -OutFile $msi -UseBasicParsing
  Write-Host "Installing (silent)..."
  $args = "/i `"$msi`" AGREETOLICENSE=Yes LAUNCHSPLUNK=1 SERVICESTARTTYPE=auto " +
          "RECEIVING_INDEXER=`"$Indexer`:$RecvPort`" SPLUNKUSERNAME=admin SPLUNKPASSWORD=`"$AdminPw`" /quiet"
  Start-Process msiexec.exe -ArgumentList $args -Wait
} else {
  Write-Host "UF already installed at $UF."
}

# 2) inputs: Windows event logs + perfmon (plain inputs.conf, no add-on)
$appRoot = "$UF\etc\apps\TA-dcloud-win"
$ta      = "$appRoot\local"
$def     = "$appRoot\default"
New-Item -ItemType Directory -Force -Path $ta  | Out-Null
New-Item -ItemType Directory -Force -Path $def | Out-Null

# app.conf is required for Splunk to treat this directory as an installed, enabled
# app; without it the UF can silently skip the app and never load these inputs.
@"
[install]
state = enabled

[package]
check_for_updates = false

[ui]
is_visible = false
label = dCloud Windows inputs
"@ | Set-Content -Encoding ASCII "$def\app.conf"

@"
[WinEventLog://Security]
index = london_windows
disabled = 0

[WinEventLog://System]
index = london_windows
disabled = 0

[WinEventLog://Application]
index = london_windows
disabled = 0

[perfmon://CPU]
object = Processor
counters = % Processor Time
instances = _Total
interval = 60
index = london_metrics
sourcetype = perfmon:cpu
disabled = 0

[perfmon://Memory]
object = Memory
counters = Available MBytes; % Committed Bytes In Use
interval = 60
index = london_metrics
sourcetype = perfmon:memory
disabled = 0
"@ | Set-Content -Encoding ASCII "$ta\inputs.conf"

# Native splunk.exe calls below: a benign message on stderr (e.g. the MSI already
# registered the indexer, so "forwarded-server already present") must NOT abort the
# script under $ErrorActionPreference=Stop, or the restart never runs and no inputs
# load. Drop to Continue for these, then restore.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"

# 3a) run the UF service as LocalSystem so it can read ALL Windows event logs,
#     including the Security channel. Recent MSIs default the service to the
#     low-privilege virtual account NT SERVICE\SplunkForwarder, which cannot read
#     the event logs - the forwarder runs and forwards its own _internal fine, but
#     no WinEventLog data is ever collected. This is the usual "forwarding works
#     but no Windows events" cause.
$svc = Get-CimInstance Win32_Service -Filter "Name='SplunkForwarder'" -ErrorAction SilentlyContinue
if ($svc -and $svc.StartName -ne "LocalSystem") {
  Write-Host "UF service runs as '$($svc.StartName)'; switching to LocalSystem for event-log access..."
  & sc.exe config SplunkForwarder obj= "LocalSystem" password= "" 2>&1 | Out-Null
} elseif ($svc) {
  Write-Host "UF service already runs as LocalSystem."
}

# 3b) ensure it forwards to the indexer (the MSI's RECEIVING_INDEXER usually already
#     did this; "already present" is expected and fine).
& "$UF\bin\splunk.exe" add forward-server "$Indexer`:$RecvPort" -auth "admin:$AdminPw" 2>&1 | Out-Null

# 4) restart to pick up inputs (this is what makes the WinEventLog/perfmon inputs live)
& "$UF\bin\splunk.exe" restart 2>&1 | Write-Host

# 5) verify: the WinEventLog/perfmon inputs should now be live, and the indexer reachable
Write-Host "--- Active event-log / perfmon inputs (should list Security/System/Application + CPU/Memory) ---"
& "$UF\bin\splunk.exe" list inputstatus -auth "admin:$AdminPw" 2>&1 | Select-String -Pattern "WinEventLog|perfmon|Security|System|Application|CPU|Memory" | Write-Host
Write-Host "--- Forward-server connection (should show ...:9997 active) ---"
& "$UF\bin\splunk.exe" list forward-server -auth "admin:$AdminPw" 2>&1 | Write-Host

$ErrorActionPreference = $prevEAP

Write-Host ""
Write-Host "Done. Forwarding Windows events -> london_windows and perfmon -> london_metrics."
Write-Host "Verify on the Splunk box (as gary/admin):"
Write-Host "    index=_internal host=$env:COMPUTERNAME | stats count      (proves forwarding works)"
Write-Host "    index=london_windows | stats count by sourcetype"
Write-Host "Note: WinEventLog Security only fills once there is logon/logoff activity;"
Write-Host "System and Application should appear within a minute or two."
