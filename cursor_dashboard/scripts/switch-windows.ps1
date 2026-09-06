$ErrorActionPreference = 'Stop'
if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge __EXPIRES_AT__) {
    throw 'Token expired. Generate a new command.'
}
$running = @(Get-Process -Name Cursor -ErrorAction SilentlyContinue)
$candidates = @($running | ForEach-Object { $_.Path })
$candidates += @(
    "$env:LOCALAPPDATA\Programs\cursor\Cursor.exe",
    "$env:ProgramFiles\Cursor\Cursor.exe",
    "${env:ProgramFiles(x86)}\Cursor\Cursor.exe"
)
$cursorExe = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $cursorExe) { throw 'Cursor not found. Open Cursor once and try again.' }
$appRoot = Join-Path (Split-Path $cursorExe) 'resources\app'
$cursorDb = Join-Path $env:APPDATA 'Cursor\User\globalStorage\state.vscdb'
if (-not (Test-Path -LiteralPath $cursorDb -PathType Leaf)) { throw 'Cursor database not found. Open Cursor once first.' }
$previousRunAsNode = $env:ELECTRON_RUN_AS_NODE
$previousEncoding = $OutputEncoding
try {
    $env:ELECTRON_RUN_AS_NODE = '1'
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    & $cursorExe -e 'require(process.argv[1])' (Join-Path $appRoot 'node_modules\@vscode\sqlite3') | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'This Cursor installation does not expose its SQLite runtime.' }
    Write-Host 'Closing Cursor. Save your work and accept any save prompts.'
    $running | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { [void]$_.CloseMainWindow() }
    $deadline = (Get-Date).AddSeconds(30)
    while (Get-Process -Name Cursor -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $deadline) { throw 'Cursor is still running. Close it manually and run the command again.' }
        Start-Sleep -Milliseconds 500
    }
    $source = @'
__ENGINE__
'@
    $source | & $cursorExe - $appRoot $cursorDb | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Account switch failed. See the error above; Cursor was not restarted.' }
} finally {
    $env:ELECTRON_RUN_AS_NODE = $previousRunAsNode
    $OutputEncoding = $previousEncoding
}
Start-Process -FilePath $cursorExe
Write-Host 'Cursor reopened. Check the account shown in Cursor.'
