param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$AppArgs
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Write-Host "[env] loading $(Split-Path -Leaf $Path)"
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $clean = $line.Trim()
        if (-not $clean -or $clean.StartsWith("#")) { continue }
        if ($clean -match '^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            )) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

Import-DotEnv (Join-Path $RootDir ".env.weauto")
Import-DotEnv (Join-Path $RootDir ".env")

$VenvDir = if ($env:UV_PROJECT_ENVIRONMENT) { $env:UV_PROJECT_ENVIRONMENT } else { ".venv312" }
$UvPython = if ($env:UV_PYTHON) { $env:UV_PYTHON } else { "3.12.13" }
$UvExe = $null
if ($env:UV_BIN -and (Test-Path -LiteralPath $env:UV_BIN)) {
    $UvExe = $env:UV_BIN
} else {
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) { $UvExe = $uvCommand.Source }
}
if (-not $UvExe) {
    $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path -LiteralPath $candidate) { $UvExe = $candidate }
}
if (-not $UvExe) {
    throw "uv was not found. Install it first with: winget install --id=astral-sh.uv -e"
}

Write-Host "[setup] syncing dependencies with uv (Python $UvPython)"
$env:UV_PROJECT_ENVIRONMENT = $VenvDir
& $UvExe sync --locked --python $UvPython
if ($LASTEXITCODE -ne 0) { throw "uv sync failed with exit code $LASTEXITCODE" }

$VenvRoot = if ([IO.Path]::IsPathRooted($VenvDir)) { $VenvDir } else { Join-Path $RootDir $VenvDir }
$PythonExe = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "virtual environment Python not found: $PythonExe"
}

$Marker = Join-Path $VenvRoot ".playwright_chromium_installed"
$NeedsPlaywright = -not (Test-Path -LiteralPath $Marker)
if (-not $NeedsPlaywright) {
    $markerTime = (Get-Item -LiteralPath $Marker).LastWriteTimeUtc
    foreach ($dependency in @("requirements.txt", "pyproject.toml", "uv.lock")) {
        $dependencyPath = Join-Path $RootDir $dependency
        if ((Test-Path -LiteralPath $dependencyPath) -and
            (Get-Item -LiteralPath $dependencyPath).LastWriteTimeUtc -gt $markerTime) {
            $NeedsPlaywright = $true
            break
        }
    }
}
if ($NeedsPlaywright) {
    & $PythonExe -c "import playwright"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[setup] installing Playwright Chromium"
        & $PythonExe -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium installation failed" }
        Set-Content -LiteralPath $Marker -Value (Get-Date -Format o) -Encoding ASCII
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $RootDir "config.toml"))) {
    Write-Host "[setup] creating config.toml from example"
    Copy-Item -LiteralPath (Join-Path $RootDir "config.toml.example") -Destination (Join-Path $RootDir "config.toml")
}

if (-not $env:WEAUTO_SCREENSHOT_HIGH_RES) { $env:WEAUTO_SCREENSHOT_HIGH_RES = "0" }
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[run] python -u -m app.main $($AppArgs -join ' ')"
& $PythonExe -u -m app.main @AppArgs
exit $LASTEXITCODE
