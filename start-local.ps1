[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'desktop-launcher.ps1')
exit $LASTEXITCODE
