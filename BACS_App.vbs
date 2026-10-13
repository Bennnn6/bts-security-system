' ========================================================
' BACS Enterprise - Unified Desktop Application Launcher
' Menjalankan aplikasi desktop utuh tanpa jendela hitam CMD
' ========================================================

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")

strScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
strPythonExe = strScriptDir & "\venv\Scripts\pythonw.exe"
strDesktopScript = strScriptDir & "\desktop_app.py"

If Not FSO.FileExists(strPythonExe) Then
    strPythonExe = "pythonw.exe"
End If

WshShell.CurrentDirectory = strScriptDir
WshShell.Run """" & strPythonExe & """ """ & strDesktopScript & """", 0, False
