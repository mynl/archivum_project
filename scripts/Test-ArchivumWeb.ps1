param(
    [ValidateSet("Changed", "Fast", "Slow", "All")]
    [string]$Mode = "Changed",

    [switch]$Browser,

    [string]$Python = "",

    [string]$PytestArgs = ""
)

$ErrorActionPreference = "Stop"
$env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY = "1"

function Write-Step {
    param([string]$Message)
    $stamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    Write-Host "[$stamp] $Message"
}

function Get-ChangedPath {
    $tracked = @(git diff --name-only HEAD)
    $untracked = @(git ls-files --others --exclude-standard)
    @($tracked + $untracked) | Where-Object { $_ -and $_.Trim() -ne "" } | Sort-Object -Unique
}

function Test-RelevantSlowChange {
    param([string[]]$ChangedPath)

    $patterns = @(
        "^src/archivum/web/",
        "^src/archivum/analytics/",
        "^src/archivum/search/",
        "^tests/",
        "^pyproject\.toml$"
    )

    foreach ($path in $ChangedPath) {
        $normalized = $path -replace "\\", "/"
        foreach ($pattern in $patterns) {
            if ($normalized -match $pattern) {
                return $true
            }
        }
    }
    return $false
}

$repoRoot = git rev-parse --show-toplevel
Set-Location -LiteralPath $repoRoot

Write-Step "Repository: $repoRoot"
Write-Step "Mode: $Mode"

$changedPath = @(Get-ChangedPath)
if ($changedPath.Count -gt 0) {
    Write-Step "Changed paths detected:"
    $changedPath | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Step "No changed paths detected."
}

$slowRelevant = Test-RelevantSlowChange -ChangedPath $changedPath
$includeSlow = $false
$includeBrowser = $false

switch ($Mode) {
    "Fast" {
        $includeSlow = $false
        $includeBrowser = [bool]$Browser
    }
    "Slow" {
        $includeSlow = $true
        $includeBrowser = [bool]$Browser
    }
    "All" {
        $includeSlow = $true
        $includeBrowser = $true
    }
    "Changed" {
        $includeSlow = $slowRelevant
        $includeBrowser = [bool]$Browser
    }
}

if ($Mode -eq "Changed" -and -not $slowRelevant) {
    Write-Step "Slow web/network tests skipped: no relevant source or test changes."
}
if ($includeSlow) {
    Write-Step "Slow web/network tests enabled."
}
if ($includeBrowser) {
    Write-Step "Browser smoke tests enabled."
} else {
    Write-Step "Browser smoke tests skipped. Pass -Browser or use -Mode All to include them."
}

$runner = if ($Python.Trim()) { @($Python, "-m", "pytest") } else { @("uv", "run", "--extra", "test", "pytest") }
$pytest = @("-m")
$marker = if ($includeSlow) { "web" } else { "web and not slow" }
if (-not $includeBrowser) {
    $marker = "($marker) and not browser"
}
$pytest += $marker

if ($includeSlow) {
    $pytest += "--run-slow-web"
}
if ($includeBrowser) {
    $pytest += "--run-browser"
}
if ($PytestArgs.Trim()) {
    $pytest += $PytestArgs.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries)
}

Write-Step "Running: $($runner -join ' ') $($pytest -join ' ')"
& $runner[0] @($runner[1..($runner.Count - 1)] + $pytest)
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Step "Archivum web tests passed."
} else {
    Write-Step "Archivum web tests failed with exit code $exitCode."
}

exit $exitCode
