# IPTV 外壳模板 · 常驻管理（start / stop / restart / status / watch）
# 用法：
#   powershell -File 常驻.ps1 start      # 静默起服务，不起界面
#   powershell -File 常驻.ps1 restart    # 重启
#   powershell -File 常驻.ps1 status     # 在跑吗 / 端口 / 频道数
#   powershell -File 常驻.ps1 stop       # 停
#   powershell -File 常驻.ps1 watch      # 前台守护：每 30 秒看一次，掉了就拉起来（Ctrl+C 退出）
param([string]$Action = 'status', [int]$Port = 0)

$here = Split-Path -Parent $PSScriptRoot
$cfgPath = Join-Path $here 'config.json'
$cfg = @{}
if (Test-Path -LiteralPath $cfgPath) {
  $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
if ($Port -eq 0) { $Port = if ($cfg.port) { [int]$cfg.port } else { 8905 } }

# 找 python（config 指定 → PATH → py 启动器 → 常见位置）
$py = $null; $pre = @()
if ($cfg.python) { $py = $cfg.python }
if (-not $py -and (Get-Command python -ErrorAction SilentlyContinue)) { $py = 'python' }
if (-not $py -and (Get-Command py -ErrorAction SilentlyContinue)) { $py = 'py'; $pre = @('-3') }
if (-not $py) {
  foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
                   "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                   "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
                   'C:\Python313\python.exe', 'C:\Python312\python.exe',
                   'F:\Harness\toolchain\python\envs\default\Scripts\python.exe')) {
    if (Test-Path -LiteralPath $p) { $py = $p; $pre = @(); break }
  }
}
if (-not $py) { Write-Error '没找到 Python（装 Python 3.9+ 或把路径写进 config.json 的 python）'; exit 3 }

function Get-PortPid($p) {
  $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
  if ($c) { return $c.OwningProcess } else { return 0 }
}

function Start-Svc {
  if (Get-PortPid $Port) { return }
  $argList = @(); $argList += $pre
  $argList += @((Join-Path $here 'server.py'), '--port', $Port)
  Start-Process -FilePath $py -ArgumentList $argList -WorkingDirectory $here -WindowStyle Hidden
  for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 400; if (Get-PortPid $Port) { return } }
}

function Stop-Svc {
  $procId = Get-PortPid $Port
  if ($procId) { Stop-Process -Id $procId -Force; "已停（pid=$procId）" } else { "端口 $Port 本来就没在跑" }
}

function Show-Status {
  $procId = Get-PortPid $Port
  if (-not $procId) { "端口 $Port 没在跑"; return }
  try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/status" -TimeoutSec 5
    "在跑：端口 $Port pid=$procId 频道=$($r.channels) 刷新=$($r.refreshed) 错误=$($r.error)"
  } catch { "在跑：端口 $Port pid=$procId（/status 打不通，看日志 $env:LOCALAPPDATA\IPTV外壳\外壳.log）" }
}

switch ($Action) {
  'start'   { Start-Svc; if (-not (Get-PortPid $Port)) { Write-Error '服务没起来，看日志'; exit 4 }; Show-Status }
  'stop'    { Stop-Svc }
  'restart' { Stop-Svc; Start-Sleep -Milliseconds 600; Start-Svc; if (-not (Get-PortPid $Port)) { Write-Error '重启失败'; exit 4 }; Show-Status }
  'status'  { Show-Status }
  'watch'   {
    while ($true) {
      if (-not (Get-PortPid $Port)) { Start-Svc; "[$(Get-Date -Format HH:mm:ss)] 服务掉了，已拉起" }
      Start-Sleep -Seconds 30
    }
  }
  default { Write-Error "未知动作：$Action（可写 start/stop/restart/status/watch）"; exit 2 }
}
