#Requires -Version 5.1
<#
  Off-box backup pull: serv -> this PC. Runs nightly from Task Scheduler
  ("HMT backup pull"); Windows re-runs it on wake if the PC was off.

  Replaces the stateless `scp` of the whole directory. That copied all 14
  nightly snapshots every run -- measured 2026-08-11: 47.8 GB in 8m30s at a
  dead-flat 94 MB/s (gigabit line rate) to deliver the ~4.4 GB that was
  actually new, and growing ~2.5 GB per night as trading.db grows. This copies
  only what is missing or size-mismatched, verifies each copy, and stamps a
  marker back on serv so the box can age-check the leg (Fable 2026-07-06
  MEDIUM-5: nothing monitors whether the pull ever happened).

  Set -LocalDir to the EXISTING pull folder -- pointed anywhere else, the first
  run re-copies all 47.8 GB into the new location.
#>
param(
  [string]$RemoteHost = 'serv',
  [string]$RemoteDir  = '/home/mbelli/backups',
  [string]$LocalDir   = 'D:\hmt-backups'
)

$ErrorActionPreference = 'Stop'
$sshOpts = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=30')

New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
$logPath = Join-Path $LocalDir 'pull.log'
function Log($msg) {
  $line = "$([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss'))Z $msg"
  Write-Host $line
  Add-Content -Path $logPath -Value $line
}

# name|size for every backup artifact serv currently holds.
$listing = & ssh @sshOpts $RemoteHost "stat -c '%n|%s' $RemoteDir/trading-*.db $RemoteDir/hmt-config-*.tar.gz 2>/dev/null"
if ($LASTEXITCODE -ne 0 -or -not $listing) {
  Log '[PULL FAILED] could not list the remote backup directory'
  exit 1
}

$copied = 0
$bytes = 0
$failed = @()

foreach ($line in $listing) {
  if ($line -notmatch '^(.+)\|(\d+)$') { continue }
  $remotePath = $Matches[1]
  $size = [int64]$Matches[2]
  $name = Split-Path $remotePath -Leaf
  $localPath = Join-Path $LocalDir $name

  if ((Test-Path $localPath) -and ((Get-Item $localPath).Length -eq $size)) { continue }

  Log "copying $name ($([math]::Round($size / 1GB, 2)) GB)"
  & scp @sshOpts "${RemoteHost}:${remotePath}" $localPath
  if ($LASTEXITCODE -ne 0) {
    $failed += $name
    Log "  scp exited $LASTEXITCODE"
    continue
  }

  # scp reports success on a truncated file; a short backup is a silent bad backup.
  $got = (Get-Item $localPath).Length
  if ($got -ne $size) {
    $failed += $name
    Log "  SIZE MISMATCH got $got want $size"
    continue
  }
  $copied++
  $bytes += $size
}

if ($failed.Count) {
  Log "[PULL FAILED] $($failed -join ', ')"
  exit 1
}

& ssh @sshOpts $RemoteHost "date -u +%Y-%m-%dT%H:%M:%SZ > $RemoteDir/last_pull"
Log "pull OK -- $copied file(s), $([math]::Round($bytes / 1GB, 2)) GB"
