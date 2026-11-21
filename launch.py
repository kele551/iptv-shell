#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPTV 外壳 · 免安装启动器（双击即用，机器上不需要装 Python）

用法：
  IPTV外壳.exe                        起服务并自动打开 http://127.0.0.1:8905/
  IPTV外壳.exe --port 8906            换端口
  IPTV外壳.exe --provider m3u         换线路（等价改 config.json 的 provider）
  IPTV外壳.exe --no-browser           只起服务，不开浏览器
  IPTV外壳.exe probe                  探路：本机有没有现成的 m3u / 服务可以接
  IPTV外壳.exe check [--provider X]   模板快检：结论「可以交」才算接好
  IPTV外壳.exe --base <目录>          指定资源目录（web/providers/data/config.json 所在）

资源目录（BASE）的找法：
  1. 环境变量 IPTV_BASE；
  2. 免安装 exe 运行时，先看 exe 同级目录（要有 web/ 和 providers/ 才算数），
     这样用户把线路文件放 exe 旁边就能换，不用重新打包；
  3. 同级目录不完整就退回包内解压目录（PyInstaller 的 sys._MEIPASS）；
  4. 源码态就直接用本文件所在目录。
"""
import atexit
import importlib.util
import json
import os
import runpy
import socket
import sys
import threading
import time
import webbrowser

# ---------------------------------------------------------------- 打包依赖锚点
# server.py 与 providers/*.py、tools/*.py 都是「运行时才动态加载」的，
# PyInstaller 的静态分析看不到它们的 import，必须在这里显式 import 一次，
# 否则打出来的 exe 会报 ModuleNotFoundError（例如 No module named 'http'）。
# 本文件自己并不直接用下面这些库。
import argparse                 # noqa: F401  tools/模板快检.py、探路.py
import base64                   # noqa: F401  server.py
import hashlib                  # noqa: F401  providers/mock.py
import http.server              # noqa: F401  server.py
import re                       # noqa: F401  server.py、providers/m3u.py
import shutil                   # noqa: F401  server.py
import subprocess               # noqa: F401  server.py、tools
import urllib.error             # noqa: F401  tools
import urllib.parse             # noqa: F401  server.py
import urllib.request           # noqa: F401  tools
import xml.etree.ElementTree    # noqa: F401  providers/m3u.py

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass


def _attach_console():
    """无窗口版（--noconsole）被命令行调用时，挂到发起它的那个控制台。

    不挂上去，在 cmd / PowerShell 里跑 `IPTV-Shell.exe check` 什么输出也看不到，
    退出码也传不回去 —— 用户按说明把 check 的原始输出贴给我这一步就死在这里。
    """
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            f = open('CONOUT$', 'w', encoding='utf-8', errors='replace', buffering=1)
            sys.stdout = f
            sys.stderr = f
    except Exception:
        pass


_attach_console()


class _Tee(object):
    """同时往多处写：控制台（有就写）+ exe 同级的「命令行输出.txt」。

    这样命令行里看不见输出时，也能直接打开这个文件把原文贴出来。
    """

    def __init__(self, streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except Exception:
                pass
        return len(s)

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except Exception:
                pass

    def isatty(self):
        return False


if getattr(sys, 'frozen', False):
    _cli_run = any(a in ('probe', 'check') for a in sys.argv[1:])
    if _cli_run or sys.stdout is None or sys.stderr is None:
        try:
            _logp = os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                 '命令行输出.txt')
            _lf = open(_logp, 'w', encoding='utf-8', errors='replace', buffering=1)
            _lf.write('# IPTV 外壳 · 命令行输出（这个文件是给没有控制台的 exe 看的）\n')
            _lf.flush()
            sys.stdout = _Tee([sys.stdout, _lf])
            sys.stderr = _Tee([sys.stderr, _lf])

            def _close_log():
                try:
                    _lf.write('\n# （以上是全部输出，进程已结束）\n')
                    _lf.flush()
                    _lf.close()
                except Exception:
                    pass
            atexit.register(_close_log)
        except Exception:
            pass


def _pre_scan(argv):
    """先扫 --base，必须在算 BASE 之前生效"""
    for i, a in enumerate(argv):
        if a == '--base' and i + 1 < len(argv):
            os.environ['IPTV_BASE'] = os.path.abspath(argv[i + 1])


def _resolve_base():
    env = os.environ.get('IPTV_BASE')
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        has_web = os.path.isdir(os.path.join(exe_dir, 'web'))
        has_prov = os.path.isdir(os.path.join(exe_dir, 'providers'))
        if has_web and has_prov:
            return exe_dir
        return getattr(sys, '_MEIPASS', exe_dir)
    return os.path.dirname(os.path.abspath(__file__))


_pre_scan(sys.argv[1:])
BASE = _resolve_base()
os.environ['IPTV_BASE'] = BASE

# exe 旁边放一份 config.json 就用它（单文件版换线路全靠这条：
# 包内配置解压在临时目录，用户改不到，只能改 exe 同级这份）
if getattr(sys, 'frozen', False):
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _ext_cfg = os.path.join(_exe_dir, 'config.json')
    if os.path.isfile(_ext_cfg) and os.path.abspath(_exe_dir) != os.path.abspath(BASE):
        try:
            import shutil as _shutil
            _shutil.copyfile(_ext_cfg, os.path.join(BASE, 'config.json'))
        except Exception:
            pass


def _config_port(default=8905):
    """端口以 config.json 为准：exe 同级的优先，其次资源目录里的"""
    cands = []
    if getattr(sys, 'frozen', False):
        cands.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'config.json'))
    env = os.environ.get('IPTV_CONFIG')
    if env:
        cands.append(env)
    cands.append(os.path.join(BASE, 'config.json'))
    for p in cands:
        try:
            if p and os.path.isfile(p):
                with open(p, encoding='utf-8-sig') as f:
                    v = json.load(f).get('port')
                if v:
                    return int(v)
        except Exception:
            pass
    return default


def _ensure_temp():
    """确保 TEMP/TMP 可写：有些机器把系统 TEMP 锁了，程序要自己找退路。

    顺序：系统 TEMP 能用就用 → 程序旁边 temp 目录 → LOCALAPPDATA 下的 IPTV外壳\temp。
    """
    def usable(d):
        try:
            if not d:
                return False
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.iptv_write_test')
            with open(probe, 'w', encoding='utf-8') as f:
                f.write('ok')
            os.remove(probe)
            return True
        except Exception:
            return False

    # 先试程序自己旁边的 temp（跟安装盘同一块盘，不占 C 盘），再退系统 TEMP
    cands = [os.path.join(BASE, 'temp'),
             os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
                          'IPTV外壳', 'temp')]
    for d in cands:
        if usable(d):
            os.environ['TEMP'] = d
            os.environ['TMP'] = d
            return d
    if usable(os.environ.get('TEMP')) and usable(os.environ.get('TMP')):
        return os.environ.get('TEMP')
    return os.environ.get('TEMP')


def _load_server():
    path = os.path.join(BASE, 'server.py')
    if not os.path.isfile(path):
        raise SystemExit('找不到 server.py：%s' % path)
    spec = importlib.util.spec_from_file_location('iptv_shell_server', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules['iptv_shell_server'] = mod
    spec.loader.exec_module(mod)
    return mod


def _port_open(port, timeout=0.4):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(('127.0.0.1', int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _open_when_ready(port, timeout=25.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _port_open(port):
            url = 'http://127.0.0.1:%d/' % int(port)
            try:
                ok = webbrowser.open(url)
            except Exception as exc:
                print('自动打开浏览器失败：%s' % exc, flush=True)
                return False
            print('已请系统打开浏览器：%s（webbrowser 返回：%s）' % (url, ok), flush=True)
            return bool(ok)
        time.sleep(0.3)
    print('等端口 %d 超时，没能自动打开浏览器' % int(port), flush=True)
    return False


def _run_tool(name, argv):
    path = os.path.join(BASE, 'tools', name)
    if not os.path.isfile(path):
        raise SystemExit('找不到 %s：%s（缺 tools 目录就打不成免安装包）' % (name, path))
    saved = sys.argv
    sys.argv = [path] + list(argv)
    code = 0
    try:
        runpy.run_path(path, run_name='__main__')
    except SystemExit as e:
        # 把工具的退出码原样传回去（check 没通过要非 0）
        c = e.code
        code = 0 if c is None else (c if isinstance(c, int) else 1)
    finally:
        sys.argv = saved
    return code


def _strip_own_opts(argv):
    """把自己认的参数去掉，剩下的原样交给 server.py"""
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == '--base' and i + 1 < len(argv):
            i += 2
            continue
        if a == '--no-browser':
            i += 1
            continue
        out.append(a)
        i += 1
    return out


def _desktop_dir():
    """拿到系统真正的桌面目录（有人把桌面搬到别的盘，靠 CSIDL 才认得准）"""
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
        return buf.value
    except Exception:
        return ''


def _powershell():
    """找 powershell.exe：有些机器的 PATH 里没有它，直接按系统目录找"""
    for p in (os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                          r'System32\WindowsPowerShell\v1.0\powershell.exe'),
              os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                          r'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'),
              shutil.which('powershell.exe') or ''):
        if p and os.path.isfile(p):
            return p
    return ''


def _make_lnk(lnk, exe, desc):
    ps = ('$ws=New-Object -ComObject WScript.Shell;'
          '$s=$ws.CreateShortcut("%s");'
          '$s.TargetPath="%s";$s.WorkingDirectory="%s";'
          '$s.IconLocation="%s,0";$s.Description="%s";$s.Save()'
          % (lnk, exe, BASE, exe, desc))
    sh = _powershell()
    if not sh:
        return False
    try:
        subprocess.run([sh, '-NoProfile', '-WindowStyle', 'Hidden', '-Command', ps],
                       capture_output=True, timeout=25, creationflags=0x08000000)
        return os.path.isfile(lnk)
    except Exception:
        return False


def _ensure_desktop_shortcut():
    """第一次启动时在桌面放一个图标：双击就开外壳（以后不再重复建）"""
    if not getattr(sys, 'frozen', False):
        return
    try:
        d = _desktop_dir()
        if not d or not os.path.isdir(d):
            return
        lnk = os.path.join(d, 'IPTV外壳.lnk')
        if os.path.isfile(lnk):
            return
        if _make_lnk(lnk, sys.executable, '我的IPTV · 本机外壳（双击打开）'):
            print('已在桌面放好图标：%s' % lnk, flush=True)
    except Exception:
        pass


def main():
    _ensure_temp()
    argv = sys.argv[1:]

    # tools/模板快检.py 在 exe 里会用「本 exe + server.py + 参数」再起一个服务，
    # 这里认出来，保证快检在免安装包内也真能跑通。
    if argv and os.path.basename(argv[0]).lower() == 'server.py':
        srv = _load_server()
        sys.argv = ['server.py'] + argv[1:]
        srv.main()
        return 0

    if argv and argv[0] in ('probe', 'check'):
        if argv[0] == 'probe':
            return _run_tool('探路.py', argv[1:])
        return _run_tool('模板快检.py', argv[1:])

    # 默认行为：起服务 + 服务通了自动开浏览器
    _ensure_desktop_shortcut()
    no_browser = '--no-browser' in argv
    port = 8905
    if '--port' in argv:
        i = argv.index('--port')
        if i + 1 < len(argv):
            try:
                port = int(argv[i + 1])
            except ValueError:
                raise SystemExit('--port 要给数字，例如 --port 8906')
    else:
        port = _config_port(port)

    if _port_open(port):
        print('端口 %d 上已经有服务在跑了，直接打开页面。' % port, flush=True)
        if not no_browser:
            webbrowser.open('http://127.0.0.1:%d/' % port)
        return 0

    if not no_browser:
        threading.Thread(target=_open_when_ready, args=(port,), daemon=True).start()

    srv = _load_server()
    sys.argv = ['server.py'] + _strip_own_opts(argv)
    if '--port' not in sys.argv:
        sys.argv += ['--port', str(port)]
    srv.main()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        tb = traceback.format_exc()
        try:
            print(tb)
        except Exception:
            pass
        try:
            p = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), '报错.txt')
            with open(p, 'a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + tb + '\n')
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, '%s\n\n详情已写进同目录的 报错.txt' % tb.strip().splitlines()[-1][:300],
                'IPTV 外壳出错了', 0x10)
        except Exception:
            pass
        sys.exit(1)
