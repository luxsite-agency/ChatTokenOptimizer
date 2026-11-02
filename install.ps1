# ChatTokenOptimizer installer for Windows (PowerShell 7 or Windows PowerShell).
# Usage: .\install.ps1 [--dry-run] [--uninstall] [--keep-settings] [--force-skills]
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = $null
foreach ($c in @('py', 'python', 'python3')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $py = $cmd.Source; break }
}
if (-not $py) { Write-Error 'Python 3.9+ not found. Install it from python.org (tick "Add to PATH") and run again.' }
if ((Split-Path -Leaf $py) -eq 'py.exe') { & $py -3 "$here\install.py" @args } else { & $py "$here\install.py" @args }
