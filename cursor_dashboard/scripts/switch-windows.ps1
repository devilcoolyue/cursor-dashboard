$ErrorActionPreference = 'Stop'
$switchProgress = @{ Number = 0; Title = ''; Active = $false; Frame = 0; Started = (Get-Date) }
$animate = -not [Console]::IsOutputRedirected -and $Host.Name -eq 'ConsoleHost'
$previousProgress = $ProgressPreference
$previousRunAsNode = $env:ELECTRON_RUN_AS_NODE
$previousEncoding = $OutputEncoding

function Update-SwitchProgress {
    if ($animate) {
        $frames = @('|', '/', '-', '\')
        $elapsed = [int]((Get-Date) - $switchProgress.Started).TotalSeconds
        Write-Progress -Id 71 -Activity 'Cursor 账号切换' -Status "$($frames[$switchProgress.Frame % 4]) [$($switchProgress.Number)/5] $($switchProgress.Title) · 已等待 $elapsed 秒" -PercentComplete (($switchProgress.Number - 1) * 20)
        $switchProgress.Frame++
    }
}
function Start-SwitchStep([int]$Number, [string]$Title) {
    $switchProgress.Number = $Number
    $switchProgress.Title = $Title
    $switchProgress.Active = $true
    $switchProgress.Started = Get-Date
    Write-Host "  … [$Number/5] $Title"
    Update-SwitchProgress
}
function Complete-SwitchStep {
    if ($animate) { Write-Progress -Id 71 -Activity 'Cursor 账号切换' -Completed }
    Write-Host "  ✓ [$($switchProgress.Number)/5] $($switchProgress.Title)"
    $switchProgress.Active = $false
}
function Resolve-CursorExecutable([string]$Candidate) {
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return }
    try {
        $path = [Environment]::ExpandEnvironmentVariables($Candidate.Trim().Trim('"').Trim("'"))
        if (Test-Path -LiteralPath $path -PathType Container) { $path = Join-Path $path 'Cursor.exe' }
        if ((Split-Path $path -Leaf) -ine 'Cursor.exe') { return }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return }
        $path = (Get-Item -LiteralPath $path).FullName
        # A shortcut, uninstaller, or stale PATH entry is not an editor installation.
        $manifest = Join-Path (Split-Path $path) 'resources\app\package.json'
        if (Test-Path -LiteralPath $manifest -PathType Leaf) { return $path }
    } catch {
        # A stale or inaccessible discovery source must not block other sources.
    }
}
function Get-CursorPathCandidates {
    $commands = @(Get-Command -Name Cursor.exe, cursor, cursor.cmd -CommandType Application -All -ErrorAction SilentlyContinue)
    foreach ($command in $commands) {
        $command.Source
        # The Windows CLI is usually <install>\resources\app\bin\cursor.cmd.
        Join-Path (Split-Path $command.Source) '..\..\..\Cursor.exe'
    }
}
function Get-CursorRegistryCandidates {
    foreach ($hive in @('HKCU:', 'HKLM:')) {
        foreach ($software in @('Software', 'Software\WOW6432Node')) {
            $base = "$hive\$software\Microsoft\Windows\CurrentVersion"
            try {
                $appPath = Get-Item -LiteralPath "$base\App Paths\Cursor.exe" -ErrorAction Stop
                $appPath.GetValue('')
            } catch {}
            $entries = @(Get-ItemProperty -Path "$base\Uninstall\*" -ErrorAction SilentlyContinue)
            foreach ($entry in $entries) {
                if ($entry.DisplayName -notmatch '^Cursor(?:\s|$)') { continue }
                if ($entry.InstallLocation) { $entry.InstallLocation }
                if ($entry.DisplayIcon) {
                    # DisplayIcon is a file path with an optional icon index, not a command.
                    $entry.DisplayIcon -replace ',\s*-?\d+\s*$', ''
                }
            }
        }
    }
}
function Get-CursorShortcutCandidates {
    $shell = $null
    try {
        $shell = New-Object -ComObject WScript.Shell -ErrorAction Stop
        # Special folders also cover redirected/OneDrive desktops and the public desktop.
        foreach ($folderName in @('DesktopDirectory', 'CommonDesktopDirectory', 'Programs', 'CommonPrograms')) {
            $folder = [Environment]::GetFolderPath($folderName)
            if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
            $recursive = $folderName -in @('Programs', 'CommonPrograms')
            $links = @(Get-ChildItem -LiteralPath $folder -Filter '*Cursor*.lnk' -File -Recurse:$recursive -ErrorAction SilentlyContinue)
            foreach ($link in $links) {
                $shortcut = $null
                try {
                    # Read the target without opening or modifying the shortcut.
                    $shortcut = $shell.CreateShortcut($link.FullName)
                    $shortcut.TargetPath
                } catch {} finally {
                    if ($shortcut -and [Runtime.InteropServices.Marshal]::IsComObject($shortcut)) {
                        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut)
                    }
                }
            }
        }
    } catch {} finally {
        if ($shell -and [Runtime.InteropServices.Marshal]::IsComObject($shell)) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell)
        }
    }
}
function Find-CursorExecutable([object[]]$Running) {
    if (-not [string]::IsNullOrWhiteSpace($env:CURSOR_EXE)) {
        $resolved = Resolve-CursorExecutable $env:CURSOR_EXE
        if ($resolved) { return $resolved }
        throw 'CURSOR_EXE 指定的路径无效或不是完整的 Cursor 安装，请填写快捷方式属性中的 Cursor.exe 目标路径。'
    }
    foreach ($process in $Running) {
        try {
            $resolved = Resolve-CursorExecutable $process.Path
            if ($resolved) { return $resolved }
        } catch {}
    }
    foreach ($candidate in @(
        "$env:LOCALAPPDATA\Programs\cursor\Cursor.exe",
        "$env:ProgramFiles\Cursor\Cursor.exe",
        "${env:ProgramFiles(x86)}\Cursor\Cursor.exe"
    )) {
        $resolved = Resolve-CursorExecutable $candidate
        if ($resolved) { return $resolved }
    }
    foreach ($source in @('Get-CursorPathCandidates', 'Get-CursorRegistryCandidates', 'Get-CursorShortcutCandidates')) {
        Update-SwitchProgress
        foreach ($candidate in (& $source)) {
            $resolved = Resolve-CursorExecutable $candidate
            if ($resolved) { return $resolved }
        }
    }
    Write-Host '自动查找未成功。请先打开桌面的 Cursor 再重试，或右键快捷方式 → 属性 → 复制「目标」中的 Cursor.exe 路径。'
    Write-Host '手动指定示例（请替换为实际路径）：$env:CURSOR_EXE = ''D:\软件\Cursor\Cursor.exe''；设置后重新执行切换命令。'
    throw '未找到完整的 Cursor 安装，尚未修改账号。'
}
function Invoke-CursorRuntime([string[]]$RuntimeArguments, [string]$Source = '') {
    # Redirect both streams asynchronously, keeping native errors and animation separate.
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $cursorExe
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $info.Arguments = ($RuntimeArguments | ForEach-Object {
        # Windows argv quoting: double backslashes before quotes and at the end.
        '"' + [regex]::Replace([regex]::Replace($_, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
    }) -join ' '
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $started = $false
    try {
        [void]$process.Start()
        $started = $true
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        # Write UTF-8 bytes directly, independent of the Windows console code page.
        $sourceBytes = [Text.Encoding]::UTF8.GetBytes($Source)
        $inputWrite = $process.StandardInput.BaseStream.WriteAsync($sourceBytes, 0, $sourceBytes.Length)
        while (-not $inputWrite.IsCompleted) {
            Update-SwitchProgress
            Start-Sleep -Milliseconds 120
        }
        $inputWrite.GetAwaiter().GetResult()
        $process.StandardInput.Close()
        while (-not $process.HasExited) {
            Update-SwitchProgress
            Start-Sleep -Milliseconds 120
        }
        $process.WaitForExit()
        if ($animate) { Write-Progress -Id 71 -Activity 'Cursor 账号切换' -Completed }
        $outputText = $stdout.GetAwaiter().GetResult()
        $errorText = $stderr.GetAwaiter().GetResult()
        if ($outputText) { Write-Host $outputText.TrimEnd() }
        if ($errorText) { Write-Host $errorText.TrimEnd() }
        if ($process.ExitCode -ne 0) { throw 'Cursor 运行命令失败，请查看上方错误详情。' }
    } finally {
        if ($started -and -not $process.HasExited) { $process.Kill() }
        $process.Dispose()
    }
}

try {
    $ProgressPreference = 'Continue'
    Write-Host "`nCursor 账号切换"
    Write-Host "检查安装 → 检查运行环境 → 退出 Cursor → 备份并写入 → 重新打开`n"
    Write-Host "请先保存工作，并在独立的 PowerShell 窗口中运行此命令。`n"
    Start-SwitchStep 1 '检查凭证与 Cursor 安装'
    if ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() -ge __EXPIRES_AT__) {
        throw '登录凭证已过期，请回到面板重新生成命令。'
    }
    $running = @(Get-Process -Name Cursor -ErrorAction SilentlyContinue)
    $cursorExe = Find-CursorExecutable -Running $running
    $appRoot = Join-Path (Split-Path $cursorExe) 'resources\app'
    $cursorDb = Join-Path $env:APPDATA 'Cursor\User\globalStorage\state.vscdb'
    if (-not (Test-Path -LiteralPath $cursorDb -PathType Leaf)) { throw '未找到用户数据库，请先打开一次 Cursor。' }
    Complete-SwitchStep
    Write-Host "安装位置：$cursorExe"
    $env:ELECTRON_RUN_AS_NODE = '1'
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    Start-SwitchStep 2 '检查 Cursor 运行环境与 SQLite'
    Invoke-CursorRuntime -RuntimeArguments @('-e', 'require(process.argv[1])', (Join-Path $appRoot 'node_modules\@vscode\sqlite3'))
    Complete-SwitchStep
    Write-Host "`n退出最多等待约 30 秒；请处理 Cursor 的保存确认弹窗。"
    Start-SwitchStep 3 '等待 Cursor 安全退出'
    $deadline = (Get-Date).AddSeconds(30)
    $running | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { [void]$_.CloseMainWindow() }
    while (Get-Process -Name Cursor -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $deadline) { throw '等待退出超过 30 秒，尚未修改账号。请保存文件并完全退出 Cursor，再到独立 PowerShell 窗口重新执行。' }
        Update-SwitchProgress
        Start-Sleep -Milliseconds 120
    }
    Complete-SwitchStep
    Start-SwitchStep 4 '备份本地数据并写入账号'
    $source = @'
__ENGINE__
'@
    Invoke-CursorRuntime -RuntimeArguments @('-', $appRoot, $cursorDb) -Source $source
    Complete-SwitchStep
    $env:ELECTRON_RUN_AS_NODE = $null
    Start-SwitchStep 5 '重新打开 Cursor'
    Start-Process -FilePath $cursorExe
    Complete-SwitchStep
    Write-Host "`n切换步骤已完成，请在 Cursor 中核对当前账号。"
} catch {
    if ($animate) { Write-Progress -Id 71 -Activity 'Cursor 账号切换' -Completed }
    if ($switchProgress.Active) { Write-Host "  ✗ [$($switchProgress.Number)/5] $($switchProgress.Title)：未完成，操作已停止。" }
    if ($switchProgress.Number -eq 5) { Write-Host '账号已写入，请手动打开 Cursor 并核对当前账号。' }
    throw
} finally {
    if ($animate) { Write-Progress -Id 71 -Activity 'Cursor 账号切换' -Completed }
    $env:ELECTRON_RUN_AS_NODE = $previousRunAsNode
    $OutputEncoding = $previousEncoding
    $ProgressPreference = $previousProgress
}
