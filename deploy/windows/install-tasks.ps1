<#
.SYNOPSIS
    Register the buibui jobs as Windows scheduled tasks — the Task Scheduler half of
    `deploy/systemd/user/`.

.DESCRIPTION
    One row per job, translated from the committed systemd unit beside it. Run from an
    ELEVATED PowerShell (registering a task with LogonType S4U needs it).

        pwsh -File deploy\windows\install-tasks.ps1
        pwsh -File deploy\windows\install-tasks.ps1 -WhatIf     # print, register nothing

    Verify afterwards:

        Get-ScheduledTask -TaskPath '\buibui\' | Format-Table TaskName, State
        Get-ScheduledTaskInfo -TaskPath '\buibui\' -TaskName 'buibui-signal-watch'

    THE FOUR DEFAULTS THAT WILL KILL THE BOT SILENTLY
    -------------------------------------------------
    Task Scheduler's defaults are written for desktop chores, not for a job whose
    missing runs cost permanent evidence. Each of these is set explicitly below, and
    each is the Windows counterpart of something a systemd unit gets for free:

      DisallowStartIfOnBatteries  defaults TRUE  -> the bot stops the moment the
                                                    charger is out, and reports nothing
      StopIfGoingOnBatteries      defaults TRUE  -> a running scan is KILLED mid-flight
      StopOnIdleEnd               defaults TRUE  -> tasks stop when you touch the laptop
      ExecutionTimeLimit          defaults 72h   -> a hung run blocks its own successor
                                                    for three days

    `StartWhenAvailable` is the one that maps cleanly: it is `Persistent=true`, and it
    runs a missed fire ONCE on resume rather than once per missed slot.

    ⚠ TIME ZONE. The systemd timers say `UTC` explicitly, and
    `New-ScheduledTaskTrigger` takes a LOCAL DateTime -- so each `-At` below is a UTC
    instant put through `.ToLocalTime()`.

    MEASURED, not assumed: the resulting trigger reads back as
    `StartBoundary = 2026-09-18T00:01:00Z`, i.e. PowerShell round-trips it to a
    UTC-tagged boundary, so the ANCHOR is genuinely UTC rather than a local wall-clock
    time that happens to line up today.

    What is NOT tested here is whether Task Scheduler keeps a daily RECURRENCE
    UTC-aligned across a DST transition, or re-fires on local wall-clock. It cannot
    matter on this operator's zone (MYT, UTC+8, never observes DST). Anywhere that does
    observe it, verify that before trusting the 09:10 UTC slot -- do not read the
    measurement above as having settled the recurrence question.
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$RepoRoot,
    [string]$TaskPath = '\buibui\',
    [string]$BashExe = 'C:\Program Files\Git\bin\bash.exe'
)

$ErrorActionPreference = 'Stop'

# ⚠ Resolved HERE, not as a `param()` default. `$PSScriptRoot` is EMPTY while the
# param block is being evaluated under `powershell.exe -File` on 5.1, so the default
# form `(Resolve-Path (Join-Path $PSScriptRoot '..\..'))` dies on "Cannot bind argument
# to parameter 'Path' because it is an empty string" BEFORE the script body ever runs.
# Measured 2026-09-18 on the first -WhatIf: not a subtle failure, but invisible until
# something executes it.
if (-not $RepoRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = (Resolve-Path (Join-Path $scriptDir '..\..')).Path
}

if (-not (Test-Path $BashExe)) {
    throw "Git Bash not found at '$BashExe'. Install Git for Windows, or pass -BashExe."
}
if (-not (Test-Path (Join-Path $RepoRoot 'deploy\windows\job.sh'))) {
    throw "'$RepoRoot' does not look like the repo root (no deploy/windows/job.sh)."
}

# `Unit` is the systemd unit this row was translated from and is the key
# `tools/task_probe.py` derives its task name from; `Task` is that derived name, stated
# rather than computed so `tests/test_task_probe.py` can assert the two agree. A probe
# asking about a name the installer never registered reports "never ran" forever,
# against a perfectly healthy task.
$Jobs = @(
    @{ Unit = 'buibui-signal-watch.service'; Task = 'buibui-signal-watch'; Label = 'signal-watch'; Hc = 'HEALTHCHECKS_URL_SIGNAL'; Env = 'DATA_SOURCE=binance'; Command = '.venv/Scripts/python.exe buibui.py signal watch --once --telegram --catch-up'; TimeLimitMinutes = 15; RandomDelaySeconds = 20; EveryMinutes = 15; AtUtc = @('00:01') }
    @{ Unit = 'buibui-xsmom-daily.service'; Task = 'buibui-xsmom-daily'; Label = 'xsmom'; Hc = 'HEALTHCHECKS_URL_XSMOM'; Env = 'DATA_SOURCE=binance'; Command = 'deploy/run-xsmom.sh'; TimeLimitMinutes = 30; RandomDelaySeconds = 60; EveryMinutes = 0; AtUtc = @('00:20', '02:20', '06:20') }
    @{ Unit = 'buibui-backup.service'; Task = 'buibui-backup'; Label = 'backup'; Hc = 'HEALTHCHECKS_URL_BACKUP'; Env = ''; Command = 'deploy/backup-analytics.sh --weekly-if-due'; TimeLimitMinutes = 15; RandomDelaySeconds = 120; EveryMinutes = 0; AtUtc = @('07:40', '12:40') }
    @{ Unit = 'buibui-backup-offsite.service'; Task = 'buibui-backup-offsite'; Label = 'backup-offsite'; Hc = 'HEALTHCHECKS_URL_BACKUP_OFFSITE'; Env = ''; Command = 'deploy/backup-offsite.sh'; TimeLimitMinutes = 180; RandomDelaySeconds = 900; EveryMinutes = 0; AtUtc = @('13:25') }
    @{ Unit = 'buibui-daily-check.service'; Task = 'buibui-daily-check'; Label = 'daily-check'; Hc = 'HEALTHCHECKS_URL_DAILY_CHECK'; Env = 'TELEGRAM_ALWAYS=1 SOFT_FAIL_RC=2 TG_TAIL_LINES=120'; Command = 'poetry run python docs/plans/daily_check.py --exit-on-tier2 --telegram'; TimeLimitMinutes = 10; RandomDelaySeconds = 300; EveryMinutes = 0; AtUtc = @('09:10') }
)

function New-JobTrigger {
    param([hashtable]$Job)

    $triggers = foreach ($hhmm in $Job.AtUtc) {
        # Parse as UTC, then hand Task Scheduler the LOCAL instant it corresponds to.
        # `Get-Date -Date "...Z"` already yields a local DateTime, but the conversion is
        # spelled out so the intent survives a reader who has not read the header.
        $utc = [datetime]::SpecifyKind(
            [datetime]::ParseExact($hhmm, 'HH\:mm', $null), [DateTimeKind]::Utc)
        # Anchor on TODAY's date in UTC so a trigger installed after its own time still
        # has a valid StartBoundary; StartWhenAvailable covers the run it just missed.
        $utc = (Get-Date).ToUniversalTime().Date.Add($utc.TimeOfDay)
        $at = [datetime]::SpecifyKind($utc, [DateTimeKind]::Utc).ToLocalTime()

        if ($Job.EveryMinutes -gt 0) {
            # `OnCalendar=*:01/15` -- fire at :01 and repeat every 15 minutes forever.
            # The duration is [TimeSpan]::Zero, which Task Scheduler reads as
            # INDEFINITE; a literal 1-day duration stops the repetition after a day and
            # leaves a task that looks scheduled and fires once.
            New-ScheduledTaskTrigger -Once -At $at `
                -RepetitionInterval (New-TimeSpan -Minutes $Job.EveryMinutes) `
                -RepetitionDuration ([TimeSpan]::Zero)
        }
        else {
            New-ScheduledTaskTrigger -Daily -At $at
        }
    }
    # RandomDelaySec -- spreads the quarter-hour so five jobs do not all contend for the
    # DuckDB lock at once, same reason the units carry it.
    #
    # ⚠ `RandomDelay` is a CIM string in ISO 8601 DURATION form (`PT20S`).
    # `(New-TimeSpan -Seconds 20).ToString()` yields `00:00:20`, which is a clock time,
    # not a duration -- Task Scheduler takes it without complaint and the delay is not
    # what you asked for. `XmlConvert` is the conversion that produces the real thing.
    foreach ($t in $triggers) {
        $t.RandomDelay = [System.Xml.XmlConvert]::ToString(
            (New-TimeSpan -Seconds $Job.RandomDelaySeconds))
    }
    $triggers
}

foreach ($job in $Jobs) {
    $envPrefix = if ($job.Env) { "$($job.Env) " } else { '' }
    $inner = "$envPrefix" + "deploy/windows/job.sh $($job.Label) $($job.Hc) -- $($job.Command)"
    # Single-quoted inside `-lc` so Windows' own quoting never reaches the shell.
    $action = New-ScheduledTaskAction -Execute $BashExe `
        -Argument "-lc '$inner'" -WorkingDirectory $RepoRoot

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes $job.TimeLimitMinutes)

    # S4U runs the task whether or not the operator is logged on, WITHOUT storing a
    # password -- the `loginctl enable-linger` equivalent. Outbound HTTPS (Binance,
    # Telegram, healthchecks) works under it; mapped network drives would not, and
    # nothing here uses one.
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType S4U -RunLevel Limited

    if ($PSCmdlet.ShouldProcess("$TaskPath$($job.Task)", 'Register-ScheduledTask')) {
        Register-ScheduledTask -TaskPath $TaskPath -TaskName $job.Task `
            -Action $action -Trigger (New-JobTrigger -Job $job) `
            -Settings $settings -Principal $principal -Force | Out-Null
        Write-Host "registered $TaskPath$($job.Task)  <- $($job.Unit)"
    }
    else {
        Write-Host "would register $TaskPath$($job.Task)  <- $($job.Unit)"
        Write-Host "    $BashExe -lc '$inner'"
    }
}

Write-Host ''
Write-Host 'Off-machine backup is registered but must NOT be trusted until'
Write-Host 'BUIBUI_BACKUP_REMOTE is set in .env -- backup-offsite.sh exits 1 while it is'
Write-Host 'unset, on purpose, so it complains daily rather than looking green while'
Write-Host 'protecting nothing.'
