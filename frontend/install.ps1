# Refresh PATH for this session (needed if terminal was open before Bun install)
$env:Path = "$env:USERPROFILE\.bun\bin;" + $env:Path

Set-Location $PSScriptRoot
& bun install @args
