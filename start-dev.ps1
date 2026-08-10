$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root 'back_end'
$frontend = Join-Path $root 'front_end'
$python = Join-Path $root '.venv\Scripts\python.exe'

function Test-PortInUse([int]$port) {
  return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

if (-not (Test-PortInUse 8000)) {
  Start-Process -FilePath $python `
    -ArgumentList @('-m', 'uvicorn', 'src.api:app', '--host', '127.0.0.1', '--port', '8000') `
    -WorkingDirectory $backend `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $backend 'backend.log') `
    -RedirectStandardError (Join-Path $backend 'backend.err.log')
  Write-Host 'Backend started: http://127.0.0.1:8000'
} else {
  Write-Host 'Backend already running on port 8000'
}

if (-not (Test-PortInUse 5173)) {
  Start-Process -FilePath 'C:\Program Files\nodejs\npm.cmd' `
    -ArgumentList @('run', 'dev') `
    -WorkingDirectory $frontend `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $frontend 'frontend.log') `
    -RedirectStandardError (Join-Path $frontend 'frontend.err.log')
  Write-Host 'Frontend started: http://127.0.0.1:5173'
} else {
  Write-Host 'Frontend already running on port 5173'
}

Write-Host 'Open http://127.0.0.1:5173 in your browser.'