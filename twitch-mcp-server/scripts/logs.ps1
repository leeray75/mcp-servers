# Windows twin of logs.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\logs.ps1 [options]
 = 'Stop'
. (Join-Path  'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'logs.sh' -ScriptArgs 
