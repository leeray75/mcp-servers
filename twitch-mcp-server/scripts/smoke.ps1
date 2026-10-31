# Windows twin of smoke.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\smoke.ps1 [options]
 = 'Stop'
. (Join-Path  'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'smoke.sh' -ScriptArgs 
