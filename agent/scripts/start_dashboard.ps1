param([int]$Port = 8898, [switch]$OpenBrowser)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path "$PSScriptRoot\..").Path
$python = 'python'
$url = "http://127.0.0.1:$Port"
$ready = $false
try {
    $check = Invoke-RestMethod -Uri "$url/api/status" -TimeoutSec 3
    if ($check.project -eq $project) { $ready = $true }
} catch { }
if (-not $ready) {
    $logs = Join-Path $project 'outputs\logs'
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $arguments = '-u "' + (Join-Path $project 'src\dashboard_server.py') + '" --port ' + $Port
    $stdoutLog = Join-Path $logs 'dashboard.stdout.log'
    $stderrLog = Join-Path $logs 'dashboard.stderr.log'
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog | Out-Null
    for ($i=0; $i -lt 15; $i++) {
        Start-Sleep -Milliseconds 400
        try {
            $check = Invoke-RestMethod -Uri "$url/api/status" -TimeoutSec 3
            if ($check.project -eq $project) { $ready=$true; break }
        } catch { }
    }
    if (-not $ready) { throw 'Dashboard failed to start; see outputs/logs/dashboard.stderr.log' }
}
Write-Output $url
if ($OpenBrowser) { & cmd.exe /d /c ('start "" "' + $url + '"') | Out-Null }
