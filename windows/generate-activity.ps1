# ===========================================================================
# generate-activity.ps1 - create fresh Windows events ON DEMAND so they show up
# in a "Last 60 minutes" search (current timestamps, unlike the backfilled logs).
#
# Run in an ELEVATED PowerShell on the Windows client:
#   Set-ExecutionPolicy Bypass -Scope Process -Force
#   iwr https://raw.githubusercontent.com/js-csco/splunk-dcloud/main/windows/generate-activity.ps1 -UseBasicParsing | iex
#
# What it makes (all -> london_windows via the UF):
#   * N custom Application events (EventCode 555, source dCloudDemo) - always works
#   * N failed logons  (Security EventCode 4625) - via a bad-credential logon
#   * optional success logons (Security EventCode 4624) if you pass -LogonPassword
#
# Params (when run as a file, e.g.  .\generate-activity.ps1 -Count 10 -LogonPassword 'C1sco12345'):
#   -Count           how many of each to emit (default 5)
#   -LogonPassword   real password for -LogonUser; enables 4624 success logons
#   -LogonUser       account for the success logon (default: Administrator)
# When piped via iex you can't pass params; set env vars instead:
#   $env:GEN_COUNT=10; $env:GEN_LOGON_PW='C1sco12345'; iwr ... | iex
# ===========================================================================
param(
  [int]$Count          = $(if ($env:GEN_COUNT)    { [int]$env:GEN_COUNT }    else { 5 }),
  [string]$LogonUser   = $(if ($env:GEN_LOGON_USER){ $env:GEN_LOGON_USER }   else { "Administrator" }),
  [string]$LogonPassword = $(if ($env:GEN_LOGON_PW){ $env:GEN_LOGON_PW }     else { "" })
)
$ErrorActionPreference = "Continue"
$actions = @("opened web-app dashboard","clicked Do something","viewed report",
             "logged in to portal","refreshed inventory")

Write-Host "== Generating $Count x Windows events on $env:COMPUTERNAME =="

# 1) Custom Application-log events (guaranteed; current timestamp). EventCode 555,
#    source dCloudDemo -> searchable as index=london_windows EventCode=555.
Write-Host "1) Application events (EventCode 555, source dCloudDemo)..."
for ($i = 1; $i -le $Count; $i++) {
  $msg = $actions[(Get-Random -Maximum $actions.Count)]
  & eventcreate /T INFORMATION /ID 555 /L APPLICATION /SO "dCloudDemo" /D "User activity: $msg (#$i)" 2>&1 | Out-Null
}

# 2) Failed logons (Security 4625) with a random non-existent user so no real
#    account is ever locked out. A bad-credential Start-Process makes LSA log 4625.
Write-Host "2) Failed logons (Security EventCode 4625)..."
for ($i = 1; $i -le $Count; $i++) {
  $u  = "demo_$([guid]::NewGuid().ToString('N').Substring(0,8))"
  $pw = ConvertTo-SecureString "WrongPass!$i" -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$u", $pw)
  try { Start-Process cmd.exe -Credential $cred -ArgumentList "/c exit" -WindowStyle Hidden -ErrorAction Stop }
  catch { }   # expected: the logon fails, which is the 4625 we want
}

# 3) Optional: real success logons (Security 4624) - only if a valid password is given.
if ($LogonPassword) {
  Write-Host "3) Success logons (Security EventCode 4624) as $LogonUser..."
  $pw   = ConvertTo-SecureString $LogonPassword -AsPlainText -Force
  $cred = New-Object System.Management.Automation.PSCredential("$env:COMPUTERNAME\$LogonUser", $pw)
  for ($i = 1; $i -le $Count; $i++) {
    try { Start-Process cmd.exe -Credential $cred -ArgumentList "/c exit" -WindowStyle Hidden -ErrorAction Stop }
    catch { Write-Host "   (logon failed - check -LogonPassword; this became a 4625 instead)" }
  }
} else {
  Write-Host "3) Skipping success logons (pass -LogonPassword 'C1sco12345' or `$env:GEN_LOGON_PW to enable 4624)."
}

Write-Host ""
Write-Host "Done. Within ~30-60s, on the Splunk box over Last 60 minutes:"
Write-Host "    index=london_windows | timechart span=1m count by sourcetype"
Write-Host "    index=london_windows EventCode=555            (the custom activity events)"
Write-Host "    index=london_windows EventCode=4625           (failed logons)"
Write-Host "    index=london_windows EventCode=4624           (success logons, if enabled)"
