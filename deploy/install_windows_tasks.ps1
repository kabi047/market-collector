# Run ONCE in PowerShell opened as Administrator:
#   Set-ExecutionPolicy -Scope Process Bypass; & "E:\Claude Stocks\deploy\install_windows_tasks.ps1"
# Creates the weekday schedule so the collector runs by itself (login -> live -> eod -> news -> fundamentals).
# Remove everything later with:  -Remove
param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'venv\Scripts\python.exe'
$prefix = 'MarketCollector'

if ($Remove) {
    Get-ScheduledTask -TaskPath '\' | Where-Object TaskName -like "$prefix*" | Unregister-ScheduledTask -Confirm:$false
    'Removed.'; return
}
if (-not (Test-Path $py)) { throw "venv not found at $py" }

$weekdays = 'Monday','Tuesday','Wednesday','Thursday','Friday'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 9) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Add-Job($name, $time, $days, $cmdArgs) {
    $action = New-ScheduledTaskAction -Execute $py -Argument "collect.py $cmdArgs" -WorkingDirectory $root
    $trigger = if ($days -eq 'Daily') { New-ScheduledTaskTrigger -Daily -At $time } else { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At $time }
    Register-ScheduledTask -TaskName "$prefix-$name" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    "{0,-26} {1} {2}  ->  collect.py {3}" -f "$prefix-$name", ($days -join ','), $time, $cmdArgs
}

Add-Job 'Login'        '08:25' $weekdays 'login --auto'
Add-Job 'Universe'     '08:35' 'Monday'  'universe --auto'
Add-Job 'Live'         '08:45' $weekdays 'live --auto'
Add-Job 'EndOfDay'     '16:00' $weekdays 'eod --auto'
Add-Job 'NewsMorning'  '07:00' 'Daily'   'news'
Add-Job 'NewsEvening'  '19:00' 'Daily'   'news'
Add-Job 'Fundamentals' '10:00' 'Saturday' 'fundamentals'

# keep the PC awake while plugged in (screen may still turn off)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
"`nPower plan: no sleep/hibernate on AC power. Keep the laptop PLUGGED IN and connected to Wi-Fi/internet."
