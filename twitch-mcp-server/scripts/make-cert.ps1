# Windows twin of make-cert.sh (docs/specs/tls-oauth-redirect, T10, T-D4).
#
# It does one thing the bash script cannot: generating the material happens in WSL2 Ubuntu (where
# Docker Engine lives and where the folder is mountable), while trusting the certificate authority
# is a Windows operation. Importing the CA into the CURRENT USER's store is what stops the browser
# interstitialing the OAuth callback, and it needs no administrator rights and changes nothing
# machine-wide (T-D4).
#
# Unlike the other .ps1 twins this one does not dot-source lib/Invoke-WslScript.ps1: that helper
# exits with the bash script's status as soon as it returns, and the trust step has to run after it.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\make-cert.ps1 [--force]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs = @()
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine('error: wsl.exe not found. Install WSL2 with an Ubuntu distribution.')
    exit 1
}

$scriptsDir = $PSScriptRoot
$rootDir = Split-Path -Parent $scriptsDir
$windowsPath = Join-Path $scriptsDir 'make-cert.sh'
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
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$caPath = Join-Path $rootDir 'certs\ca.crt'
if (-not (Test-Path -LiteralPath $caPath)) {
    [Console]::Error.WriteLine("error: $caPath was not created, so there is nothing to trust.")
    exit 1
}

# certutil dumps the whole decoded certificate on a successful import. That is public certificate
# content rather than a key, but it is 60 lines nobody asked for, so it is swallowed and the
# fingerprint is printed instead (T10).
Write-Host '==> Trusting the local CA for the current user only'
& certutil.exe -addstore -user Root $caPath | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning ("certutil exited {0}. If the CA is already trusted that is harmless; otherwise " +
        'import {1} with certmgr.msc under Trusted Root Certification Authorities.' -f $LASTEXITCODE, $caPath)
} else {
    $thumb = (& certutil.exe -hashfile $caPath SHA1 | Select-Object -Index 1).Trim()
    Write-Host "    added to -user Root (no administrator rights, nothing machine-wide)"
    Write-Host "    CA SHA1: $thumb"
}

Write-Host ''
Write-Host 'Next: set TWITCH_TLS_CERT_FILE and TWITCH_TLS_KEY_FILE in .env, then run scripts\restart.ps1,'
Write-Host 'and register the OAuth Redirect URL the output above prints.'
