@echo off
rem Stops the background bot (e.g. before running "tracker bot" yourself or updating the code).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'tracker\.exe.{0,2}bot' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Bot stopped.
