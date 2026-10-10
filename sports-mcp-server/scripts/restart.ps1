# Windows twin of restart.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\restart.ps1 [options]
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'restart.sh' -ScriptArgs $args
