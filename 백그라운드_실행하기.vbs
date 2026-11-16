' SCHALE Tactical Tracking - Silent Background Launcher
Option Explicit
Dim WshShell, FSO, ScriptDir, LauncherPath, PyCmd, CmdToRun, Ret

Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
LauncherPath = ScriptDir & "\scripts\launcher.py"

PyCmd = "python"
If FSO.FileExists(ScriptDir & "\.venv\Scripts\python.exe") Then
    PyCmd = Chr(34) & ScriptDir & "\.venv\Scripts\python.exe" & Chr(34)
ElseIf FSO.FileExists(ScriptDir & "\venv\Scripts\python.exe") Then
    PyCmd = Chr(34) & ScriptDir & "\venv\Scripts\python.exe" & Chr(34)
End If

CmdToRun = PyCmd & " " & Chr(34) & LauncherPath & Chr(34) & " start"
Ret = WshShell.Run(CmdToRun, 0, True)

If Ret <> 0 Then
    MsgBox "SCHALE 서버 시작에 실패했습니다. (오류 코드: " & Ret & ")" & vbCrLf & _
           "상세한 원인은 logs\server.log 파일을 확인해주세요.", vbCritical, "SCHALE 전투 추적 시스템"
End If
