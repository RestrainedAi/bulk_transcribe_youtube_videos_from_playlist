' Clearer-named launcher. Runs the original Open_Watcher_Control_Panel.vbs, which keeps working as before.
Set sh = CreateObject("WScript.Shell")
here = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "wscript.exe """ & here & "\Open_Watcher_Control_Panel.vbs""", 1, False
