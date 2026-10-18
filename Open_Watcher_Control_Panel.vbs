Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
repo = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = """" & repo & "\Open_Watcher_Control_Panel.cmd" & """"
shell.CurrentDirectory = repo
rc = shell.Run(cmd, 0, True)
If rc <> 0 Then
    detail = ""
    If rc = 3 And fso.FileExists(repo & "\logs\selfcheck.log") Then
        detail = vbCrLf & vbCrLf & fso.OpenTextFile(repo & "\logs\selfcheck.log", 1).ReadAll()
    End If
    MsgBox "Transcription Workbench failed to start (code " & rc & ")." & detail & vbCrLf & vbCrLf & _
        "Full log: " & repo & "\logs\launcher.log", vbCritical, "Transcription Workbench"
End If
