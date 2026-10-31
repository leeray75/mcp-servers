# Windows twin of start.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\start.ps1 [options]
 = 'Stop'
. (Join-Path  'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'start.sh' -ScriptArgs 
