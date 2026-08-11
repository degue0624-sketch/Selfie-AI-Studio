Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = currentDir & "\main.py"
logPath = currentDir & "\startup.log"
quote = Chr(34)
command = "cmd /c py -3 " & quote & scriptPath & quote & " > " & quote & logPath & quote & " 2>&1"
shell.Run command, 0, false
