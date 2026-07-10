# PowerShell helpers for working with the Fly.io dep-promptlab-api app.
# Usage: . .\fly_helpers.ps1   (dot-source to load functions)
# All functions suppress the "Error: The handle is invalid" + exit-code-1 noise
# that flyctl produces on Windows when the SSH session closes.

$APP = "dep-promptlab-api"

function Invoke-FlySSH {
    <#
    .SYNOPSIS
    Runs a remote command via fly ssh console, suppressing the
    Windows SSH cleanup exit-code-1 noise.
    #>
    param([string]$Command)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    $result = fly ssh console --app $APP -C $Command 2>&1
    $ErrorActionPreference = $prev
    $result | Where-Object { $_ -notmatch "The handle is invalid" -and $_ -notmatch "^fly\s*:" -and $_ -notmatch "^Connecting to" }
    $global:LASTEXITCODE = 0
}

function Launch-Daemons {
    Write-Host "Launching supervisor + 4 workers..."
    Invoke-FlySSH "sh /data/launch_all_new.sh"
}

function Show-Procs {
    Invoke-FlySSH "sh /data/list_procs2.sh"
}

function Show-SupervisorLog {
    param([int]$Lines = 30)
    $date = Get-Date -Format 'yyyy-MM-dd'
    Invoke-FlySSH "grep '$date' /data/supervisor.log"
}

function Kill-Daemons {
    Write-Host "Stopping all daemons..."
    Invoke-FlySSH "sh /data/kill_all.sh"
}

Write-Host "fly_helpers.ps1 loaded. Functions: Launch-Daemons, Show-Procs, Show-SupervisorLog, Kill-Daemons"
