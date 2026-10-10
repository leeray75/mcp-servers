# Windows twin of setup.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\setup.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'setup.sh' -ScriptArgs $args
