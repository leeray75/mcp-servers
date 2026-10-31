# Windows twin of rebuild.sh: runs it inside WSL2 Ubuntu, forwarding arguments and the exit code.
# Usage: .\scripts\rebuild.ps1 [options]
 = 'Stop'
. (Join-Path  'lib\Invoke-WslScript.ps1')
Invoke-WslScript -Name 'rebuild.sh' -ScriptArgs 
