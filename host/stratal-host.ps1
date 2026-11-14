# Stratal host script v1 (ADR-0008, BUG4). Run it on Windows, beside the stack:
#
#   .\host\stratal-host.ps1                # until Ctrl+C
#   .\host\stratal-host.ps1 -Once          # one check, then exit (for testing)
#
# It does two things containers cannot do:
#   1. Prevent: while the API reports active jobs, ask Windows not to go to sleep
#      from idleness. The request belongs to this process and ends with it. It does
#      not change any Windows power setting, and it cannot stop a lid-close sleep.
#   2. Observe: when this script itself was suspended (its clock jumps), report the
#      suspend and the resume to the API, so the gap shows on the job timeline.

param(
    [string]$Api = "http://127.0.0.1:8000/api/v1",
    [int]$IntervalSeconds = 15,
    [switch]$Once
)

$ErrorActionPreference = "Stop"

Add-Type -Namespace Stratal -Name Power -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("kernel32.dll")]
public static extern uint SetThreadExecutionState(uint esFlags);
"@

$ES_CONTINUOUS = [uint32]"0x80000000"
$ES_SYSTEM_REQUIRED = [uint32]"0x00000001"

function Set-KeepAwake([bool]$on) {
    $flags = if ($on) { $ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED } else { $ES_CONTINUOUS }
    [void][Stratal.Power]::SetThreadExecutionState($flags)
}

function Send-PowerEvent([string]$type, [datetime]$at) {
    $body = @{ type = $type; at = $at.ToUniversalTime().ToString("o") } | ConvertTo-Json
    try {
        Invoke-RestMethod -Method Post -Uri "$Api/host/power-events" -Body $body `
            -ContentType "application/json" -TimeoutSec 5 | Out-Null
    } catch {
        Write-Host "$(Get-Date -Format T)  could not report '$type': $($_.Exception.Message)"
    }
}

$awake = $false
$last = Get-Date
Write-Host "Stratal host script: polling $Api every $IntervalSeconds s. Ctrl+C to stop."
try {
    while ($true) {
        $now = Get-Date
        # The loop sleeps $IntervalSeconds. A much longer gap means Windows was suspended.
        if (($now - $last).TotalSeconds -gt ($IntervalSeconds + 45)) {
            Write-Host "$(Get-Date -Format T)  suspended from $($last.ToString('T')) to $($now.ToString('T'))"
            Send-PowerEvent "suspend" $last
            Send-PowerEvent "resume" $now
        }
        $last = $now

        try {
            $state = Invoke-RestMethod -Uri "$Api/host/keep-awake" -TimeoutSec 5
            $want = [bool]$state.keep_awake
            if ($want -ne $awake) {
                Set-KeepAwake $want
                $awake = $want
                $jobs = ($state.active_jobs -join ", ")
                if ($want) { Write-Host "$(Get-Date -Format T)  keeping Windows awake: $jobs" }
                else { Write-Host "$(Get-Date -Format T)  no active jobs: normal sleep allowed" }
            }
        } catch {
            # The API is down: never hold the laptop awake for something we cannot see.
            if ($awake) { Set-KeepAwake $false; $awake = $false }
        }

        if ($Once) { Write-Host "keep_awake=$awake"; break }
        Start-Sleep -Seconds $IntervalSeconds
    }
} finally {
    Set-KeepAwake $false
}
