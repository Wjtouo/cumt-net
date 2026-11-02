' start cumt-net auto-login daemon, no console window
Option Explicit
Dim sh, env, fso, base, pythonExe, pythonwExe, preferred
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
Set env = sh.Environment("PROCESS")
base = fso.GetParentFolderName(WScript.ScriptFullName)
env("PYTHONPATH") = fso.BuildPath(base, "libs") & ";" & env("PYTHONPATH")
sh.CurrentDirectory = base
pythonExe = env("CUMT_PYTHON")
If Len(pythonExe) > 0 Then
    pythonwExe = fso.BuildPath(fso.GetParentFolderName(pythonExe), "pythonw.exe")
    If Not fso.FileExists(pythonwExe) Then
        MsgBox "pythonw.exe was not found beside CUMT_PYTHON. Check your Python installation.", 16, "CUMT Auto Login"
        WScript.Quit 1
    End If
Else
    preferred = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Programs\Python\Python310\pythonw.exe"
    If fso.FileExists(preferred) Then
        pythonwExe = preferred
    Else
        pythonwExe = "pythonw.exe"
    End If
End If
sh.Run """" & pythonwExe & """ """ & fso.BuildPath(base, "cumt_net.py") & """ --daemon", 0, False
