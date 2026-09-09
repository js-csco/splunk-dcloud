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
$ta = "$UF\etc\apps\TA-dcloud-win\local"
New-Item -ItemType Directory -Force -Path $ta | Out-Null
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

# 3) ensure it forwards to the indexer (in case UF was pre-installed)
& "$UF\bin\splunk.exe" add forward-server "$Indexer`:$RecvPort" -auth "admin:$AdminPw" 2>$null | Out-Null

# 4) restart to pick up inputs
& "$UF\bin\splunk.exe" restart

Write-Host "Done. Forwarding Windows events -> london_windows and perfmon -> london_metrics."
Write-Host "Log in to Windows and open http://<container-ip>:8080/ to generate correlated activity."
