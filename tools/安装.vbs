Option Explicit
' IPTV 外壳模板 · 一键装到本机（桌面图标；想开机自动起就用 安装-开机自启.vbs）
Dim sh, fso, here, ps1, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
ps1 = here & "\安装.ps1"
If Not fso.FileExists(ps1) Then
  MsgBox "找不到安装脚本：" & ps1, 48, "IPTV 外壳"
  WScript.Quit 1
End If
cmd = """C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"" -NoProfile -ExecutionPolicy Bypass -File """ & ps1 & """"
sh.Run cmd, 1, True
MsgBox "装好了，去桌面双击「IPTV外壳」就能看。", 64, "IPTV 外壳"
