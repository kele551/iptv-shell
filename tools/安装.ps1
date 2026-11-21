# IPTV 外壳模板 · 装到本机（桌面图标）
# 用法：
#   powershell -File 安装.ps1                 # 桌面放一个「IPTV外壳」图标
#   powershell -File 安装.ps1 -Uninstall      # 撤掉图标
#   powershell -File 安装.ps1 -DesktopDir D:\测试桌面   # 演练用：装到指定目录
param([switch]$Uninstall, [string]$DesktopDir = '')

$here = Split-Path -Parent $PSScriptRoot          # 模板根目录
$vbs = Join-Path $here 'tools\外壳.vbs'           # 双击图标：起服务 + 开界面
if (-not (Test-Path -LiteralPath $vbs)) { Write-Error "找不到 $vbs"; exit 2 }

$desktop = if ($DesktopDir) { $DesktopDir } else { [Environment]::GetFolderPath('Desktop') }
$lnkPath = Join-Path $desktop 'IPTV外壳.lnk'
$wscript = Join-Path $env:WINDIR 'System32\wscript.exe'

function New-Lnk($path) {
  $sh = New-Object -ComObject WScript.Shell
  $lnk = $sh.CreateShortcut($path)
  $lnk.TargetPath = $wscript
  $lnk.Arguments = '"' + $vbs + '"'
  $lnk.WorkingDirectory = $here
  $lnk.Description = 'IPTV 外壳（双击开始运行）'
  $edgeIcon = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
  if (-not (Test-Path -LiteralPath $edgeIcon)) { $edgeIcon = 'C:\Program Files\Microsoft\Edge\Application\msedge.exe' }
  if (Test-Path -LiteralPath $edgeIcon) { $lnk.IconLocation = "$edgeIcon,0" }
  $lnk.Save()
}

if ($Uninstall) {
  if (Test-Path -LiteralPath $lnkPath) { Remove-Item -LiteralPath $lnkPath -Force; "已删：$lnkPath" }
  return
}

New-Lnk $lnkPath
"桌面图标：$lnkPath"
"双击图标才开始运行（不占开机自启）：约 3 秒弹出界面；关掉窗口服务还在，再点一次立刻回来"
"撤掉：powershell -File tools\安装.ps1 -Uninstall -DesktopDir <同目录>"
