# Pre-push check: does anything about a real household appear in what is about
# to be pushed? Run it before every push and read the result.
#
#   .\scripts\check-household.ps1                 # compares HEAD against origin/main
#   .\scripts\check-household.ps1 -Range HEAD~3   # compares HEAD against another base
#   .\scripts\check-household.ps1 -Denylist C:\somewhere\private\denylist.txt
#
# Two kinds of pattern are checked:
#   1. Built-in shapes that never belong in this repo whoever the household is:
#      IPv4 addresses, climate/sensor entity ids that are not from the invented
#      test household, long-lived-token shapes.
#   2. A DENYLIST FILE of real words (names, rooms, hostnames) that lives OUTSIDE
#      this repo. It is never committed. Default location: the HEATING_PRIVATE
#      environment variable, file denylist.txt. One pattern per line, regex,
#      case-insensitive. Lines starting with # are ignored.
#
# Exit code 0 = clean, 1 = something matched, 2 = could not run.

param(
    [string]$Range = 'origin/main',
    [string]$Denylist = ''
)

# Not 'Stop': under PowerShell 5.1 a native command's stderr (a git line-ending
# warning, say) would then become a terminating error and abort the check.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $root

if (-not $Denylist) {
    if ($env:HEATING_PRIVATE) { $Denylist = Join-Path $env:HEATING_PRIVATE 'denylist.txt' }
}

$builtin = @(
    '\b(?:\d{1,3}\.){3}\d{1,3}\b',                       # IPv4 address
    'eyJ[A-Za-z0-9_-]{20,}',                              # JWT / long-lived token shape
    '(?<![\w.])climate\.(?!set_|turn_|blue_room|green_room|attic_|big_bedroom|kitchen_|studio_|hall_|r\b)[a-z0-9_]+',   # climate ids outside the invented household; not service names or code like r.climate
    'sensor\.(?!x\b)[a-z0-9_]+_(temperature|humidity|battery)\b'
)

$patterns = @()
foreach ($p in $builtin) { $patterns += [pscustomobject]@{ Source = 'built-in'; Pattern = $p } }

if ($Denylist) {
    if (Test-Path $Denylist) {
        foreach ($line in Get-Content $Denylist) {
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith('#')) { $patterns += [pscustomobject]@{ Source = 'denylist'; Pattern = $t } }
        }
        Write-Output ("denylist: " + $Denylist + " (" + (($patterns | Where-Object { $_.Source -eq 'denylist' }).Count) + " patterns)")
    } else {
        Write-Output ("WARNING: denylist file not found at " + $Denylist + " - only built-in shapes are checked")
    }
} else {
    Write-Output "WARNING: no denylist given and HEATING_PRIVATE is not set - only built-in shapes are checked"
}

# What is about to leave the machine: committed changes against the base, plus
# anything staged or unstaged, plus untracked files.
$diff = @()
$diff += git -c core.safecrlf=false diff "$Range...HEAD"
$diff += git -c core.safecrlf=false diff HEAD
$untracked = git ls-files --others --exclude-standard
foreach ($f in $untracked) { $diff += Get-Content $f -ErrorAction SilentlyContinue | ForEach-Object { "+" + $_ } }
$log = git log --format='%s%n%b' "$Range..HEAD"

$added = $diff | Where-Object { $_ -match '^\+' -and $_ -notmatch '^\+\+\+' }
Write-Output ("checking " + $added.Count + " added lines and " + ($log | Measure-Object -Line).Lines + " commit-message lines")

$hits = 0
foreach ($p in $patterns) {
    $rx = [regex]::new($p.Pattern, 'IgnoreCase')
    foreach ($line in @($added) + @($log | ForEach-Object { "msg: " + $_ })) {
        if ($line -and $rx.IsMatch($line)) {
            $hits++
            Write-Output ("HIT [" + $p.Source + "] " + $p.Pattern)
            Write-Output ("     " + $line.Substring(0, [Math]::Min(160, $line.Length)))
        }
    }
}

if ($hits -gt 0) {
    Write-Output ("RESULT: " + $hits + " hit(s). Do not push until each one is explained or removed.")
    exit 1
}
Write-Output "RESULT: clean"
exit 0
