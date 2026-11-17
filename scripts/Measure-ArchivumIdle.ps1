<#
.SYNOPSIS
Idle check: CPU time and child processes of `archivum serve --prod` left alone for a minute.

.DESCRIPTION
Starts the server on a spare local port against the default library, visits the Audit page once,
waits for the startup warmups to finish, then samples the server's CPU time over the sample
period with no page open. Run it before handing over any change that touches background work: a
watcher, a timer, polling, a thread or a subprocess. The server is stopped at the end.

To measure with a DOI audit run active, start the run from another terminal
(`archivum doi-audit-run --counts -x`) during the settle period.

.EXAMPLE
.\scripts\Measure-ArchivumIdle.ps1
.\scripts\Measure-ArchivumIdle.ps1 -Port 9198 -SampleSeconds 120
#>
param(
    [int]$Port = 9199,
    [int]$SettleSeconds = 60,
    [int]$SampleSeconds = 60
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$base = "http://127.0.0.1:$Port"

function Get-ServerTree {
    param([int]$RootId)
    # the venv python is a launcher, so the server is a child of the process started here
    $all = Get-CimInstance Win32_Process
    $ids = @($RootId)
    do {
        $more = @($all | Where-Object { $_.ParentProcessId -in $ids -and $_.ProcessId -notin $ids } | ForEach-Object { $_.ProcessId })
        $ids += $more
    } while ($more.Count -gt 0)
    Get-Process -Id $ids -ErrorAction SilentlyContinue
}

$launcher = Start-Process -FilePath $python -PassThru -WindowStyle Hidden `
    -ArgumentList "-c", "`"from archivum.cli import entry; entry()`"", "serve", "--prod", "-p", $Port
try {
    $up = $false
    foreach ($i in 1..120) {
        try { Invoke-WebRequest "$base/about" -TimeoutSec 2 -UseBasicParsing | Out-Null; $up = $true; break }
        catch { Start-Sleep -Seconds 1 }
    }
    if (-not $up) { throw "The server did not start on port $Port." }
    Invoke-WebRequest "$base/audit" -TimeoutSec 60 -UseBasicParsing | Out-Null
    Invoke-WebRequest "$base/audit/subset" -TimeoutSec 120 -UseBasicParsing | Out-Null

    Write-Host "Settling for $SettleSeconds s (startup warmups) ..."
    Start-Sleep -Seconds $SettleSeconds
    $before = (Get-ServerTree $launcher.Id | Measure-Object -Property CPU -Sum).Sum
    Write-Host "Sampling for $SampleSeconds s ..."
    Start-Sleep -Seconds $SampleSeconds
    $tree = @(Get-ServerTree $launcher.Id)
    $cpu = ($tree | Measure-Object -Property CPU -Sum).Sum - $before
    $server = $tree | Sort-Object { $_.Threads.Count } -Descending | Select-Object -First 1

    "{0:N3} CPU s in {1} s ({2:N2}% of one core)" -f $cpu, $SampleSeconds, ($cpu / $SampleSeconds * 100)
    # conhost is the hidden console Windows attaches, not something archivum started
    $own = @($tree | Where-Object { $_.ProcessName -ne "conhost" })
    "processes: {0} ({1}); expected 2, the launcher and the server; server threads: {2}" -f $own.Count, (($own | ForEach-Object { $_.ProcessName }) -join ", "), $server.Threads.Count
}
finally {
    Get-ServerTree $launcher.Id | Stop-Process -Force -ErrorAction SilentlyContinue
}
