# Windows twin of rebuild.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\rebuild.ps1 [options]
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'rebuild.sh' -ScriptArgs $args
