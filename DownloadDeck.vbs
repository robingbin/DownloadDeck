' Starts DownloadDeck without a console window.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = here
sh.Run """" & here & "\.venv\Scripts\pythonw.exe"" """ & here & "\app.py""", 0, False
