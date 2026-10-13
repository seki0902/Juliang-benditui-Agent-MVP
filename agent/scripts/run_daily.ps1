param(
    [string]$BrowserConfig = "$PSScriptRoot\..\browser_download_config.json",
    [string]$InputDir = "",
    [string]$OutputRoot = "$PSScriptRoot\..\outputs\daily_reports",
    [string]$StateDb = $env:AGENT_STATE_DB,
    [switch]$Force,
    [ValidateSet('scheduled', 'manual')]
    [string]$RunSource = 'scheduled',
    [string]$PythonExe = $env:AGENT_PYTHON
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
if ([string]::IsNullOrWhiteSpace($StateDb)) {
    throw "Set AGENT_STATE_DB or pass -StateDb with the local database path."
}
if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    throw "Set AGENT_PYTHON or pass -PythonExe with the Python executable path."
}

$sourceRoot = Join-Path $projectRoot "src"
$env:PYTHONPATH = $sourceRoot
$env:PYTHONIOENCODING = 'utf-8'
$env:AGENT_RUN_SOURCE = $RunSource
Set-Location -LiteralPath $projectRoot

$pythonPath = $PythonExe
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $pythonCommand = Get-Command $PythonExe -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python executable not found: $PythonExe"
    }
    $pythonPath = $pythonCommand.Source
}

$arguments = @("$sourceRoot\daily_job.py", "--output-root", $OutputRoot, "--state-db", $StateDb)
if ([string]::IsNullOrWhiteSpace($InputDir)) {
    $arguments += @("--browser-config", $BrowserConfig)
} else {
    $arguments += @("--input-dir", $InputDir)
}
if ($Force) {
    $arguments += "--force"
}

$logDir = Join-Path $projectRoot 'outputs\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir ('daily-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
Start-Transcript -Path $logPath | Out-Null
try {
    & $pythonPath @arguments
    $resultCode = $LASTEXITCODE
} finally {
    Stop-Transcript | Out-Null
}
exit $resultCode