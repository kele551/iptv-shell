# IPTV 外壳模板 · 启动脚本（静默起服务，可选打开界面窗口）
# 用法：
#   powershell -File 启动外壳.ps1            # 只起服务，不开窗口
#   powershell -File 启动外壳.ps1 -Open      # 起服务并打开 Edge 应用窗口
#   powershell -File 启动外壳.ps1 -Stop      # 停服务
param([int]$Port = 0, [switch]$Open, [switch]$Stop)

$here = Split-Path -Parent $PSScriptRoot

# 读配置
$cfgPath = Join-Path $here 'config.json'
$cfg = @{}
if (Test-Path -LiteralPath $cfgPath) {
  $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
if ($Port -eq 0) { $Port = if ($cfg.port) { [int]$cfg.port } else { 8905 } }

# 找 python（任何机器都能用：config 指定 → PATH 里的 python → py 启动器 → 常见安装位置）
$py = $null
$pre = @()
if ($cfg.python) { $py = $cfg.python; $pre = @() }
if (-not $py -and (Get-Command python -ErrorAction SilentlyContinue)) { $py = 'python'; $pre = @() }
if (-not $py -and (Get-Command py -ErrorAction SilentlyContinue)) { $py = 'py'; $pre = @('-3') }
if (-not $py) {
  $probe = @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
             "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
             "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
             'C:\Python313\python.exe', 'C:\Python312\python.exe',
             'F:\Harness\toolchain\python\envs\default\Scripts\python.exe')
  foreach ($p in $probe) { if (Test-Path -LiteralPath $p) { $py = $p; $pre = @(); break } }
}
if (-not $py) {
  Write-Error "这台电脑没找到 Python。请先装 Python 3.9 以上（安装时勾选 Add to PATH），或把 python.exe 路径写进 config.json 的 python 字段。"
  exit 3
}

function Test-Port($p) {
  $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  return [bool]$c
}

if ($Stop) {
  $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($c) { Stop-Process -Id $c.OwningProcess -Force; "已停端口 $Port（pid=$($c.OwningProcess)）" }
  else { "端口 $Port 本来就没在跑" }
  return
}

if (-not (Test-Port $Port)) {
  $argList = @()
  $argList += $pre
  $argList += @((Join-Path $here 'server.py'), '--port', $Port)
  Start-Process -FilePath $py -ArgumentList $argList `
    -WorkingDirectory $here -WindowStyle Hidden
  for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 400
    if (Test-Port $Port) { break }
  }
}
if (-not (Test-Port $Port)) {
  Write-Error "服务没起来，看日志：$env:LOCALAPPDATA\IPTV外壳\外壳.log"
  exit 4
}
"服务在跑：http://127.0.0.1:$Port/"

if ($Open) {
  $edge = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
  if (-not (Test-Path -LiteralPath $edge)) { $edge = 'C:\Program Files\Microsoft\Edge\Application\msedge.exe' }
  $r = Get-Random -Maximum 1000000
  if (Test-Path -LiteralPath $edge) {
    Start-Process -FilePath $edge -ArgumentList @(
      '--force-dark-mode', "--app=http://127.0.0.1:$Port/?r=$r", '--window-size=1360,860')
  } else {
    Start-Process "http://127.0.0.1:$Port/?r=$r"
  }
}
