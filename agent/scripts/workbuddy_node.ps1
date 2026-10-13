param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('node', 'npm', 'npx')]
    [string]$Tool,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$runtime = Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\workbuddy-node'
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'node.exe'))) {
    throw "Project Node runtime not found: $runtime"
}

$toolFileName = switch ($Tool) {
    'node' { 'node.exe' }
    'npm' { 'npm.cmd' }
    'npx' { 'npx.cmd' }
}
$toolPath = Join-Path $runtime $toolFileName
if (-not (Test-Path -LiteralPath $toolPath)) {
    throw "WorkBuddy $Tool executable not found: $toolPath"
}
$env:PATH = "$runtime;$env:PATH"
& $toolPath @Arguments
exit $LASTEXITCODE
