@echo off
rem Makes the bot start by itself every time you log in to Windows, and starts it now.
set "SCRIPT=%~dp0start-bot.vbs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\expense-tracker-bot.vbs" echo CreateObject("WScript.Shell").Run """%SCRIPT%""", 0, False
echo Auto-start installed: the bot starts every time you log in to Windows.
wscript "%SCRIPT%"
echo Bot started in the background. Log: data\bot.log
