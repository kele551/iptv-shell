Option Explicit
' IPTV 外壳模板 · 静默常驻（开机自启用这条：只起服务，不弹界面）
Dim sh, fso, here, ps1, cmd, action
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\常驻.ps1"
If Not fso.FileExists(ps1) Then WScript.Quit 1
action = "start"
If WScript.Arguments.Count > 0 Then action = WScript.Arguments(0)
cmd = """C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1 & """ " & action
sh.Run cmd, 0, False
