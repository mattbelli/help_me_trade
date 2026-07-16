<#
ram_watchdog.ps1 — guard the local order-flow verify stack from freezing the PC.

Why: running `next dev` + Playwright + the parquet/orderflow backend against a
looping page has hard-frozen Windows ~4x (see memory: orderflow-local-verify-froze-pc).
The freeze is RAM exhaustion -> the box thrashes before you can react.

This polls system free RAM (the real freeze predictor) plus a per-process cap,
and kills the order-flow stack BEFORE the system locks up. Defensive only.

Usage (PowerShell, from repo root):
  pwsh -File scripts/ram_watchdog.ps1                       # defaults
  pwsh -File scripts/ram_watchdog.ps1 -FloorMB 3000 -ProcCapMB 4000
  Stop with Ctrl+C, or kill the background job.
#>
param(
  [int]$FloorMB     = 2500,   # kill if system free RAM drops below this
  [int]$ProcCapMB   = 4000,   # kill any single node/python over this
  [int]$IntervalSec = 3,      # poll cadence
  [string[]]$Names  = @('node','python','python3'),
  [string]$LogPath  = "$PSScriptRoot\..\ram_watchdog.log"
)

function Write-Log($msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $msg
  Write-Host $line
  Add-Content -Path $LogPath -Value $line -Encoding utf8
}

Write-Log ("watchdog START  floor=${FloorMB}MB  procCap=${ProcCapMB}MB  interval=${IntervalSec}s  targets=" + ($Names -join ','))

while ($true) {
  $os     = Get-CimInstance Win32_OperatingSystem
  $freeMB = [int]($os.FreePhysicalMemory / 1KB)   # FreePhysicalMemory is in KB
  $procs  = Get-Process -Name $Names -ErrorAction SilentlyContinue

  $reason = $null
  $victims = @()

  if ($freeMB -lt $FloorMB) {
    $reason  = "SYSTEM free RAM ${freeMB}MB < floor ${FloorMB}MB"
    $victims = $procs   # starving -> take down the whole stack
  } else {
    $hogs = $procs | Where-Object { ($_.WorkingSet64/1MB) -gt $ProcCapMB }
    if ($hogs) {
      $reason  = "per-process cap exceeded"
      $victims = $hogs
    }
  }

  if ($reason) {
    Write-Log "TRIGGER: $reason"
    foreach ($p in $victims) {
      $mb = [math]::Round($p.WorkingSet64/1MB)
      try {
        Stop-Process -Id $p.Id -Force -ErrorAction Stop
        Write-Log ("  killed {0} (pid {1}, {2}MB)" -f $p.ProcessName, $p.Id, $mb)
      } catch {
        Write-Log ("  FAILED to kill {0} (pid {1}): {2}" -f $p.ProcessName, $p.Id, $_.Exception.Message)
      }
    }
    Start-Sleep -Seconds 2
  }

  Start-Sleep -Seconds $IntervalSec
}
