# Shared helper for the PowerShell twins. Dot-source it; do not execute it directly.
# Runs scripts/<Name> inside WSL2 Ubuntu (Docker Engine lives there, not in Docker Desktop),
# forwarding arguments and the exit code.

function Invoke-WslScript {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$ScriptArgs = @()
    )

    $ErrorActionPreference = 'Stop'

    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        [Console]::Error.WriteLine('error: wsl.exe not found. Install WSL2 with an Ubuntu distribution.')
        exit 1
    }

    $scriptsDir = Split-Path -Parent $PSScriptRoot
    $windowsPath = Join-Path $scriptsDir $Name
    if (-not (Test-Path -LiteralPath $windowsPath)) {
        [Console]::Error.WriteLine("error: $windowsPath not found.")
        exit 1
    }

    $linuxPath = (& wsl.exe -d Ubuntu -- wslpath -a -u $windowsPath 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($linuxPath)) {
        [Console]::Error.WriteLine('error: could not reach WSL distribution "Ubuntu" (try: wsl.exe -l -v).')
        exit 1
    }

    & wsl.exe -d Ubuntu -- bash $linuxPath @ScriptArgs
    exit $LASTEXITCODE
}
