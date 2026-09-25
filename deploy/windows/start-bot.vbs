' Starts the Telegram bot hidden in the background (no terminal window).
' Output goes to data\bot.log. Does nothing if the bot is already running.
Option Explicit
Dim fso, sh, wmi, procs, root, tracker

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

' Already running? Two copies would fight over Telegram updates.
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name <> 'cmd.exe' AND CommandLine LIKE '%tracker.exe%bot%'")
If procs.Count > 0 Then WScript.Quit 0

' This file lives in expense-tracker\deploy\windows
root = fso.GetParentFolderName(fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName)))

' The venv can be inside the project or one folder up.
tracker = root & "\.venv\Scripts\tracker.exe"
If Not fso.FileExists(tracker) Then tracker = fso.GetParentFolderName(root) & "\.venv\Scripts\tracker.exe"
If Not fso.FileExists(tracker) Then
  MsgBox "tracker.exe not found. Run 'pip install -e .' in the project's .venv first.", 16, "Expense tracker"
  WScript.Quit 1
End If

If Not fso.FolderExists(root & "\data") Then fso.CreateFolder(root & "\data")
sh.CurrentDirectory = root
sh.Run "cmd /c """"" & tracker & """ bot >> data\bot.log 2>&1""", 0, False
