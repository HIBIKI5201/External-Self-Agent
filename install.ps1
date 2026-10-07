# E.S. Agent (External Self Agent) installer:
# logon autostart, Start Menu shortcut (Ctrl+Alt+E), Claude Code MCP registration.
# Safe to run again; every step replaces what a previous run (or the old "EsAgent" install) created.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = (Get-Command python -ErrorAction Stop).Source
$Pythonw = Join-Path (Split-Path $Python) 'pythonw.exe'
if (-not (Test-Path $Pythonw)) { throw "pythonw.exe not found next to $Python" }
$User = "$env:USERDOMAIN\$env:USERNAME"
$TaskName = 'ESAgent'
$Programs = [Environment]::GetFolderPath('Programs')

Write-Host "[0/3] Removing the old EsAgent install (if any)"
Unregister-ScheduledTask -TaskName 'EsAgent' -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item (Join-Path $Programs 'EsAgent.lnk') -ErrorAction SilentlyContinue

Write-Host "[1/3] Registering logon task '$TaskName'"
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Root\esagent\server.py`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'E.S. Agent (External Self Agent) local server' -Force | Out-Null

Write-Host "[2/3] Creating Start Menu shortcut 'E.S. Agent' (Ctrl+Alt+E)"
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $Programs 'E.S. Agent.lnk'))
$lnk.TargetPath = $Pythonw
$lnk.Arguments = "`"$Root\esagent\launch.py`""
$lnk.WorkingDirectory = $Root
$lnk.Hotkey = 'CTRL+ALT+E'
$lnk.Description = 'Open E.S. Agent'
$lnk.Save()

Write-Host "[3/3] Registering MCP server 'esagent' for Claude Code (user scope)"
$prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
claude mcp remove esagent --scope user *> $null
claude mcp add --scope user esagent -- $Python "$Root\esagent\mcp_server.py"
$ErrorActionPreference = $prev

Write-Host "Starting E.S. Agent now"
Start-ScheduledTask -TaskName $TaskName
Write-Host "Done. Open it from the Start Menu 'E.S. Agent' or press Ctrl+Alt+E."
