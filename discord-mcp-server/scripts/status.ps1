# Windows twin of status.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\status.ps1 [options]
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'status.sh' -ScriptArgs $args
