@echo off
rem Turns auto-start off and stops the bot.
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\expense-tracker-bot.vbs" 2>nul
call "%~dp0stop-bot.bat"
echo Auto-start removed.
