# One-command setup for the laptop half of Gmail Alarm (Windows).
#
#   irm https://raw.githubusercontent.com/jrhosting067-stack/Yuval-script/HEAD/laptop/install.ps1 | iex
#
# Downloads the listener, invents a random topic, plays a test siren, optionally
# starts it at logon, and prints the line to paste into Apps Script.
#
# Set $env:GMAIL_ALARM_TOPIC beforehand to choose your own topic.

$ErrorActionPreference = 'Stop'

$RawUrl     = 'https://raw.githubusercontent.com/jrhosting067-stack/Yuval-script/HEAD/laptop/alarm_listener.py'
$InstallDir = Join-Path $env:USERPROFILE '.gmail-alarm'
$ScriptPath = Join-Path $InstallDir 'alarm_listener.py'

function Write-Step($text) { Write-Host "`n$text" -ForegroundColor Cyan }

# --- Python ----------------------------------------------------------------
# The py launcher is the reliable way in on Windows; plain python.exe may be the
# Microsoft Store stub that does nothing but open the Store.
$python = $null
foreach ($candidate in @('py', 'python3', 'python')) {
    $found = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $found) { continue }
    try {
        # Not $args - that is an automatic variable in PowerShell.
        $probe = if ($candidate -eq 'py') { @('-3', '--version') } else { @('--version') }
        $version = & $found.Source @probe 2>&1
        if ($LASTEXITCODE -eq 0 -and "$version" -match 'Python 3\.(\d+)' -and [int]$Matches[1] -ge 7) {
            $python = $found.Source
            $pythonArgs = if ($candidate -eq 'py') { @('-3') } else { @() }
            break
        }
    } catch { }
}

if (-not $python) {
    Write-Host "Python 3.7+ is required but wasn't found." -ForegroundColor Red
    Write-Host "Install it with:  winget install Python.Python.3.12"
    Write-Host "Then close this window, open a new one, and run this again."
    return
}

# --- Topic -----------------------------------------------------------------
# The topic is the only secret in the system, so a generated one beats whatever
# a human would type. Public ntfy topics are unauthenticated: anyone who knows
# or guesses the string can ring this laptop.
$topic = $env:GMAIL_ALARM_TOPIC
$generated = $false
if (-not $topic) {
    $bytes = [byte[]]::new(6)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $topic = 'gmail-alarm-' + (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
    $generated = $true
}

# --- Download --------------------------------------------------------------
Write-Step "Installing to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
try {
    Invoke-WebRequest -Uri $RawUrl -OutFile $ScriptPath -UseBasicParsing
} catch {
    Write-Host "Download failed: $_" -ForegroundColor Red
    return
}
Set-Content -Path (Join-Path $InstallDir 'topic') -Value $topic

# --- Test siren ------------------------------------------------------------
Write-Step 'Testing the siren - turn the volume up. Press Enter to silence it.'
& $python @pythonArgs $ScriptPath $topic --test --seconds 6

# --- Autostart -------------------------------------------------------------
# A .cmd in the Startup folder rather than a scheduled task: it needs no admin
# rights, and it opens a visible console at logon so you can see that it is
# listening and press Enter to silence a siren.
$autostarted = $false
$reply = Read-Host "`nStart listening automatically at logon? [Y/n]"
if ($reply -notmatch '^[Nn]') {
    $startup = [Environment]::GetFolderPath('Startup')
    $cmdPath = Join-Path $startup 'gmail-alarm.cmd'
    $pythonInvocation = if ($pythonArgs.Count) { "`"$python`" $($pythonArgs -join ' ')" } else { "`"$python`"" }
    @"
@echo off
title Gmail Alarm - listening
$pythonInvocation "$ScriptPath" $topic
"@ | Set-Content -Path $cmdPath -Encoding ASCII
    Write-Host "Autostart installed. Remove it by deleting:`n  $cmdPath"
    $autostarted = $true
}

# --- What's left -----------------------------------------------------------
Write-Step 'Laptop side done.'
if ($generated) { Write-Host 'Your topic (generated, keep it private):' }
else            { Write-Host 'Your topic:' }
Write-Host "`n    $topic`n"

Write-Host 'Now do the Gmail side at https://script.google.com:'
Write-Host '  1. New project, paste in apps-script/Code.gs from the repo.'
Write-Host '  2. Set senders / subjectContains to what should wake you, and set:'
Write-Host "`n         topic: '$topic'`n"
Write-Host '  3. Run setup(), approve the permission prompt, then run testAlarm().'

Write-Host "`nOne more thing - stop Windows sleeping, or it cannot ring:" -ForegroundColor Yellow
Write-Host '  Settings > System > Power & battery > Screen and sleep'
Write-Host '  Set "When plugged in, put my device to sleep after" to Never.'
Write-Host '  The listener blocks idle sleep by itself, but closing the lid still sleeps the machine.'

if (-not $autostarted) {
    Write-Host "`nStart listening (leave the window open):"
    $shown = if ($pythonArgs.Count) { "$python $($pythonArgs -join ' ')" } else { $python }
    Write-Host "`n    $shown `"$ScriptPath`" $topic`n"
}
