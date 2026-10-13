param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = 'Stop'
$nodeWrapper = Join-Path $PSScriptRoot 'workbuddy_node.ps1'
if (-not (Test-Path -LiteralPath $nodeWrapper)) {
    throw "Missing WorkBuddy Node wrapper: $nodeWrapper"
}

# Uses WorkBuddy's npx. The package is cached by npm after its first download;
# no system Node/npm installation or global PATH entry is required.
& $nodeWrapper npx --yes --package @playwright/cli playwright-cli @Arguments
exit $LASTEXITCODE
