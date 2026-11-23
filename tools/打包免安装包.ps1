$ErrorActionPreference = "Stop"
$root = "F:\Harness\projects\IPTV外壳模板"
# 打包一律用 toolchain\python\versions\3.15.2（只有它装了 PyInstaller，规则 §0 第 33 条）
$py   = "F:\Harness\toolchain\python\versions\3.15.2\python.exe"
Set-Location $root

$args = @(
  "-m","PyInstaller","--noconfirm","--clean","--onedir","--noconsole","--name","IPTV-Shell",
  "--icon","$root\assets\icon.ico",
  "--distpath","$root\builds\exe","--workpath","$root\builds\build","--specpath","$root\builds\build",
  "--add-data","$root\web;web",
  "--add-data","$root\providers;providers",
  "--add-data","$root\tools;tools",
  "--add-data","$root\data;data",
  "--add-data","$root\server.py;.",
  "--add-data","$root\README.md;.",
  "--add-data","$root\智能体接入指南.md;.",
  "$root\launch.py"
)
& $py @args | Out-Null
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 退出码 $LASTEXITCODE，打包失败" }

$pkg = "$root\builds\exe\IPTV-Shell"
if (-not (Test-Path "$pkg\IPTV-Shell.exe")) { throw "打包失败：exe 没生成" }
# 包里只放「示例配置」，不放 config.json：覆盖安装时才不会把用户的线路配置冲掉
# （历史事故：升级解压覆盖，真 config 被默认 mock 盖掉，点播/分类全空）
Copy-Item "$root\config.json" "$pkg\config.example.json" -Force
Remove-Item "$pkg\config.json" -Force -ErrorAction SilentlyContinue

# 桌面快捷方式的图标源：放一个独立 ico 在 exe 同级。
# launch.py 建 .lnk 时把 IconLocation 指向它，避免 Windows 缓存 exe 图标导致重启后变白板。
Copy-Item "$root\assets\icon.ico" "$pkg\IPTV外壳.ico" -Force
if (-not (Test-Path "$pkg\IPTV外壳.ico")) { throw "打包失败：图标 ico 没复制进包" }

# 先确认新代码真的在包里，再压 zip（防「拿旧包压出新 zip」的假成功）
$srv = Get-Content "$pkg\_internal\server.py" -Raw -Encoding utf8
$chk = Get-Content "$pkg\_internal\tools\模板快检.py" -Raw -Encoding utf8
if ($srv -notmatch "n.startswith\('yaoplayer'\)") { throw "包内 server.py 不是新版本" }
if ($srv -notmatch "def vod_check") { throw "包内 server.py 缺播放前检查（DRM/编码），不是新版本" }
if ($chk -notmatch "点播放真起播放器") { throw "包内 模板快检.py 不是新版本" }
# 2026-11-23 用户定：不许没验证就标注“这片源放不了”，包内界面与后段都必须没有拦截
$webTxt = Get-Content "$pkg\_internal\web\index.html" -Raw -Encoding utf8
if ($webTxt -match "这片源放不了") { throw "包内界面仍带「这片源放不了」预标注" }
if ($srv -notmatch "不预判") { throw "包内 server.py 仍在播放前拦截（未按 2026-11-23 口径放开）" }
$fwd = Get-Content "$pkg\_internal\providers\转发示例.py" -Raw -Encoding utf8
if ($fwd -notmatch "copyrightDRM" -or $fwd -notmatch "def vod_check") { throw "包内 转发示例.py 缺 DRM/编码识别" }
# 界面闸门（2026-11-21）：包内 web/index.html 必须与仓库正本逐字节一致，
# 防止「代码改了、包里还是旧样式」——界面改动曾因部署副本不同步而看不出效果。
$webSrc = (Get-FileHash "$root\web\index.html" -Algorithm SHA256).Hash
$webPkg = (Get-FileHash "$pkg\_internal\web\index.html" -Algorithm SHA256).Hash
if ($webSrc -ne $webPkg) { throw "包内 web/index.html 与仓库正本哈希不一致，禁止打包" }

# 维护脚本不进对外包：发版前快检.py / 打包免安装包.ps1 只在本机维护时用，
# 含 F:\Harness 绝对路径，打进用户包既无用处又泄露维护结构。
# 用户/智能体用的 probe=探路.py、check=模板快检.py，都保留。
Remove-Item "$pkg\_internal\tools\发版前快检.py" -Force -ErrorAction SilentlyContinue
Remove-Item "$pkg\_internal\tools\打包免安装包.ps1" -Force -ErrorAction SilentlyContinue

# 隐私闸门（2026-11-21）：包里不许夹带本机专属适配器 / 本机 Harness 路径 / 运营商私密字样。
# 探路.py 里的 F:\桌面 / F:\文档 / F:\Download 是「找片源」的默认搜索位置，属功能设计，不在此拦。
if (Test-Path "$pkg\_internal\providers\yunnan_dx.py") { throw "包内出现本机专属适配器 yunnan_dx.py，禁止打包" }
$leak = Get-ChildItem "$pkg" -Recurse -File -Include *.py,*.md,*.json,*.html,*.txt |
  Select-String -Pattern 'yunnan_dx|yunnan_svc|曲靖天邑|云南电信|云南IPTV|F:\\Harness' -ErrorAction SilentlyContinue
if ($leak) {
  $leak | ForEach-Object { Write-Host ("隐私命中: " + $_.Path + " :: " + $_.Line.Trim()) }
  throw "包内发现本机路径 / 运营商私密字样，禁止打包"
}

$zip = "$root\builds\exe\iptv-shell-Windows.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path "$pkg", "$root\builds\exe\使用说明.txt", "$root\builds\exe\IPTV外壳.cmd" -DestinationPath $zip -CompressionLevel Optimal
Copy-Item $zip "$root\docs\download\iptv-shell-Windows.zip" -Force

"zip 字节: " + (Get-Item $zip).Length
"包内示例配置: " + (Test-Path "$pkg\config.example.json")
"包内不带 config: " + (-not (Test-Path "$pkg\config.json"))
"包内 _meta.py: " + (Test-Path "$pkg\_internal\providers\_meta.py")
"包内 server.py 含修复: True"
"包内 快检含新项: True"
"包内 播放前检查（DRM/编码）: True"
"包内 界面与正本一致: True (SHA256 $webSrc)"
