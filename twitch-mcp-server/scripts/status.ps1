# Windows twin of status.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\status.ps1 [options]
 = 'Stop'
. (Join-Path  'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'status.sh' -ScriptArgs 
