# Lab 04 - Privacy-first face access control
# Windows PowerShell version of run.sh. Run it from this folder:
#   powershell -ExecutionPolicy Bypass -File .\run4.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker was not found. Install Docker Desktop and start it, then run this script again."
}

# PowerShell does not stop when a native command fails, so every docker call checks its exit code.
function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

try {
    Invoke-Docker compose build
    Invoke-Docker compose up -d bio-backend bio-device
    # The evaluation waits for both services (the first start downloads the face models).
    Invoke-Docker compose --profile jobs run --rm bio-eval
}
finally {
    # Always stop the services, even if a step failed. Cleanup errors must not hide the real one.
    $ErrorActionPreference = "Continue"
    & docker compose down | Out-Null
}

Write-Host ""
Write-Host "Done. Metrics file: results\biometrics\metrics.json"
