<#
.SYNOPSIS
    Build the archivum app home as a set of symlinks into the data and settings trees.

.DESCRIPTION
    Archivum resolves its app home to ~\.archivum (or $ARCHIVUM_HOME). Nothing precious
    lives there: the library data sits under $DataRoot and the two YAML settings files
    under $Settings, and the home reaches them by symlink. This script creates that
    layout, once, on a new machine:

        <AppHome>\
          global-config.yaml         -> <Settings>\global-config.yaml       (file link)
          libraries\<Library>\       -> <DataRoot>\libraries\<Library>\     (directory link)
              config.yaml            -> <Settings>\config.yaml              (file link)
          docs\                      -> <DataRoot>\ShardedDocLibrary        (directory link)
          full-text\                 -> <DataRoot>\ShardedFullText          (directory link)
          models\                    real directory, derived cache

    The library directory is a *directory* link, not a directory of file links: the
    watchdog auto-reload watches that directory and only sees writes through a
    directory link. See dev/plan-2.5.0-app-home.md, section 3.

    Any feather files still sitting in $Settings are moved to the library data
    directory first (copy, verify size, then remove the source).

    Dry run by default. Idempotent: links already pointing at the right target are
    skipped. Refuses to touch a link site occupied by a real file, a non-empty real
    directory, or a link with a different target.

    Windows symlink creation needs Developer Mode or an elevated shell.

.PARAMETER AppHome
    The app home to build. Default ~\.archivum.
.PARAMETER DataRoot
    Where the library data lives (feathers, document store, full text).
.PARAMETER Settings
    Where the two YAML settings files live.
.PARAMETER Library
    Library directory name under libraries\.
.PARAMETER Execute
    Actually make changes. Without it, report only.
#>
[CmdletBinding()]
param(
    [string]$AppHome  = (Join-Path $HOME '.archivum'),
    [string]$DataRoot = 'D:\archivum',
    [string]$Settings = 'D:\Settings\archivum',
    [string]$Library  = 'uber-library',
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$mode = if ($Execute) { 'EXECUTE' } else { 'DRY RUN' }
Write-Host "New-ArchivumHome [$mode]" -ForegroundColor Cyan
Write-Host "  AppHome  : $AppHome"
Write-Host "  DataRoot : $DataRoot"
Write-Host "  Settings : $Settings"
Write-Host "  Library  : $Library"

$libData = Join-Path $DataRoot "libraries\$Library"
$docStore = Join-Path $DataRoot 'ShardedDocLibrary'
$fullText = Join-Path $DataRoot 'ShardedFullText'

# ---------------------------------------------------------------- preflight
foreach ($required in @($DataRoot, $Settings, $docStore, $fullText,
                        (Join-Path $Settings 'global-config.yaml'),
                        (Join-Path $Settings 'config.yaml'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required path missing: $required"
    }
}

function Test-IsLink([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    return ($null -ne $item) -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Get-LinkTarget([string]$Path) {
    (Get-Item -LiteralPath $Path -Force).LinkTarget
}

# ---------------------------------------------------------------- step 1: feathers
$feathers = Get-ChildItem -LiteralPath $Settings -Filter '*.feather' -File -ErrorAction SilentlyContinue
if ($feathers) {
    Write-Host "`nMove feathers $Settings -> $libData" -ForegroundColor Yellow
    foreach ($f in $feathers) {
        $dest = Join-Path $libData $f.Name
        if (Test-Path -LiteralPath $dest) {
            throw "Refusing: $dest already exists; resolve by hand."
        }
        Write-Host "  $($f.Name)  ($([math]::Round($f.Length / 1MB, 2)) MB)"
        if ($Execute) {
            New-Item -ItemType Directory -Force -Path $libData | Out-Null
            Copy-Item -LiteralPath $f.FullName -Destination $dest
            $src = Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256
            $dst = Get-FileHash -LiteralPath $dest -Algorithm SHA256
            if ($src.Hash -ne $dst.Hash) { throw "Copy verification failed for $($f.Name)" }
            Remove-Item -LiteralPath $f.FullName
        }
    }
} else {
    Write-Host "`nNo feathers in $Settings; nothing to move." -ForegroundColor DarkGray
}
if (-not (Test-Path -LiteralPath $libData) -and -not $Execute) {
    Write-Host "  (would create $libData)"
}

# ---------------------------------------------------------------- step 2: real directories
Write-Host "`nReal directories" -ForegroundColor Yellow
foreach ($dir in @($AppHome, (Join-Path $AppHome 'libraries'), (Join-Path $AppHome 'models'), $libData)) {
    if (Test-Path -LiteralPath $dir) {
        if (Test-IsLink $dir) { throw "Refusing: $dir is a link, expected a real directory." }
        Write-Host "  exists   $dir" -ForegroundColor DarkGray
    } else {
        Write-Host "  create   $dir"
        if ($Execute) { New-Item -ItemType Directory -Path $dir | Out-Null }
    }
}

# ---------------------------------------------------------------- step 3: links
function New-ArchivumLink([string]$Link, [string]$Target, [switch]$Directory) {
    $kind = if ($Directory) { 'dirlink ' } else { 'filelink' }
    # On a dry run the library data directory may not exist yet; step 2 creates it.
    if ($Execute -and -not (Test-Path -LiteralPath $Target)) { throw "Link target missing: $Target" }
    if (Test-Path -LiteralPath $Link) {
        if (Test-IsLink $Link) {
            $have = Get-LinkTarget $Link
            if ($have -ieq $Target) {
                Write-Host "  ok       $kind $Link -> $Target" -ForegroundColor DarkGray
                return
            }
            throw "Refusing: $Link is a link to '$have', expected '$Target'."
        }
        $item = Get-Item -LiteralPath $Link -Force
        if ($item.PSIsContainer) {
            if (@(Get-ChildItem -LiteralPath $Link -Force).Count -gt 0) {
                throw "Refusing: $Link is a non-empty real directory."
            }
            Write-Host "  remove   empty real directory $Link"
            if ($Execute) { Remove-Item -LiteralPath $Link }
        } else {
            throw "Refusing: $Link is a real file. Move it aside first."
        }
    }
    Write-Host "  link     $kind $Link -> $Target"
    if ($Execute) {
        New-Item -ItemType SymbolicLink -Path $Link -Target $Target | Out-Null
    }
}

Write-Host "`nSymbolic links" -ForegroundColor Yellow
New-ArchivumLink -Link (Join-Path $AppHome 'global-config.yaml') -Target (Join-Path $Settings 'global-config.yaml')
New-ArchivumLink -Link (Join-Path $AppHome 'docs')      -Target $docStore -Directory
New-ArchivumLink -Link (Join-Path $AppHome 'full-text') -Target $fullText -Directory
New-ArchivumLink -Link (Join-Path $AppHome "libraries\$Library") -Target $libData -Directory
# The nested config.yaml lives physically inside $libData (reached through the
# directory link above), so it can be created even on a dry run's target path.
New-ArchivumLink -Link (Join-Path $libData 'config.yaml') -Target (Join-Path $Settings 'config.yaml')

# ---------------------------------------------------------------- step 4: report
if ($Execute) {
    Write-Host "`nResult" -ForegroundColor Yellow
    Get-ChildItem -LiteralPath $AppHome -Force |
        Select-Object Name, @{n = 'Target'; e = { $_.LinkTarget } } |
        Format-Table -AutoSize | Out-String | Write-Host
    Get-ChildItem -LiteralPath (Join-Path $AppHome 'libraries') -Force |
        Select-Object Name, @{n = 'Target'; e = { $_.LinkTarget } } |
        Format-Table -AutoSize | Out-String | Write-Host
    Get-ChildItem -LiteralPath $libData -Force |
        Select-Object Name, Length, @{n = 'Target'; e = { $_.LinkTarget } } |
        Format-Table -AutoSize | Out-String | Write-Host
} else {
    Write-Host "`nDry run complete. Re-run with -Execute to apply." -ForegroundColor Cyan
}
