$ErrorActionPreference = "Stop"
$root = "F:\Harness\projects\IPTV外壳模板"
$py   = "F:\Harness\toolchain\python\envs\default\Scripts\python.exe"
Set-Location $root

$args = @(
  "-m","PyInstaller","--noconfirm","--clean","--onedir","--noconsole","--name","IPTV-Shell",
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

# 先确认新代码真的在包里，再压 zip（防「拿旧包压出新 zip」的假成功）
$srv = Get-Content "$pkg\_internal\server.py" -Raw -Encoding utf8
$chk = Get-Content "$pkg\_internal\tools\模板快检.py" -Raw -Encoding utf8
if ($srv -notmatch "n.startswith\('yaoplayer'\)") { throw "包内 server.py 不是新版本" }
if ($chk -notmatch "点播放真起播放器") { throw "包内 模板快检.py 不是新版本" }

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
