param(
    [string]$ConfigPath = "config.toml",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RunArgs
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

$VenvDir = if ($env:UV_PROJECT_ENVIRONMENT) { $env:UV_PROJECT_ENVIRONMENT } else { ".venv312" }
$VenvRoot = if ([IO.Path]::IsPathRooted($VenvDir)) { $VenvDir } else { Join-Path $RootDir $VenvDir }
$PythonExe = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Run start_app.ps1 once to create the Windows environment."
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $RootDir "config.toml.example") -Destination $ConfigPath
}

$LogDir = Join-Path $RootDir "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "rpa_$stamp.log"
$env:WEAUTO_LOG_FILE = $LogFile
if (-not $env:WEAUTO_SCREENSHOT_HIGH_RES) { $env:WEAUTO_SCREENSHOT_HIGH_RES = "0" }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "[run] python -u run.py --config $ConfigPath $($RunArgs -join ' ')"
Write-Host "[log] $LogFile"
& $PythonExe -u run.py --config $ConfigPath @RunArgs 2>&1 | Tee-Object -FilePath $LogFile
exit $LASTEXITCODE
