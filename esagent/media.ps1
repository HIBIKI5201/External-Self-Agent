# Play / pause / query Microsoft Edge media sessions through Windows' Global System Media
# Transport Controls. Only Edge sessions are touched, so Spotify and other players are left alone.
# Usage: media.ps1 -Action status|play|pause [-Match regex]   Output: JSON list of matching sessions.
# Edge app windows have their own id (e.g. MSEdge.www.youtube.com_/), so -Match youtube targets only them.
param([ValidateSet('status', 'play', 'pause')][string]$Action = 'status', [string]$Match = 'Edge')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) `
    ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])

$out = @()
foreach ($s in $mgr.GetSessions()) {
    if ($s.SourceAppUserModelId -notmatch $Match) { continue }
    if ($Action -eq 'play') { Await ($s.TryPlayAsync()) ([bool]) | Out-Null }
    if ($Action -eq 'pause') { Await ($s.TryPauseAsync()) ([bool]) | Out-Null }
    $title = ''
    try {
        $props = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        $title = $props.Title
    } catch {}
    $out += [pscustomobject]@{
        app    = $s.SourceAppUserModelId
        title  = $title
        status = $s.GetPlaybackInfo().PlaybackStatus.ToString()
    }
}
ConvertTo-Json -InputObject @($out) -Compress
