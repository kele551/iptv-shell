# -*- coding: utf-8 -*-
"""IPTV 外壳模板 · 发版前快检（发版闸门认这一份）

四段固定：① 能不能起来 ② 关键功能逐条冒烟 ③ 真实界面截图 ④ 升级路径
输出约定：每项一行 "OK  <功能名>  | <证据>"，最后一句「结论：这版可以交 / 可以发 —— N 项 PASS」

用法：
    python tools\发版前快检.py           # 全跑（含界面截图，约 40–90 秒）
    python tools\发版前快检.py --no-ui   # 跳过截图，快跑
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = r"F:\Harness"
PY = r"F:\Harness\toolchain\python\envs\default\Scripts\python.exe"
PROJ = r"F:\Harness\projects\IPTV外壳模板"
LOGS = os.path.join(PROJ, "logs")
SHOTS = os.path.join(LOGS, "快检截图")
INSTALL = r"D:\Program Files\IPTV外壳\IPTV-Shell"
ZIP = os.path.join(PROJ, "builds", "exe", "iptv-shell-Windows.zip")

rows = []


def add(ok, name, detail=""):
    rows.append(("OK" if ok else "FAIL", name, detail))
    print("%-5s %s%s" % ("OK" if ok else "FAIL", name, ("  |  " + detail) if detail else ""),
          flush=True)


def cfg():
    p = os.path.join(PROJ, "config.json")
    try:
        return json.loads(open(p, encoding="utf-8-sig").read())
    except Exception:
        return {}


def free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---------- ① 能不能起来 ----------
def check_start():
    bad = []
    for f in ("server.py", "launch.py"):
        p = subprocess.run([PY, "-m", "py_compile", os.path.join(PROJ, f)],
                           capture_output=True, text=True)
        if p.returncode:
            bad.append("%s: %s" % (f, (p.stderr or "")[-160:]))
    add(not bad, "源码语法", "server.py / launch.py 编译通过" if not bad else "；".join(bad))


# ---------- ② 关键功能逐条冒烟（直接复用模板快检的 19 项）----------
LINE = re.compile(r"^\s*\[(OK|FAIL|skip)\]\s+(.+?)\s{2,}(.*)$")


def check_features():
    port = free_port()
    p = subprocess.run([PY, os.path.join(PROJ, "tools", "模板快检.py"),
                        "--port", str(port), "--provider", cfg().get("provider", "mock")],
                       cwd=PROJ, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    body = (p.stdout or "") + (p.stderr or "")
    seen = 0
    for line in body.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        lv, name, detail = m.group(1), m.group(2).strip(), m.group(3).strip()
        if lv == "skip":
            continue
        seen += 1
        add(lv == "OK", name, detail)
    if not seen:
        add(False, "模板快检跑完", "没有解析到任何检查项：%s" % body[-200:])


# ---------- ③ 真实界面截图 ----------
def shot(url, path, click_text=None):
    code = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    b=p.chromium.launch(channel='msedge')\n"
        "    pg=b.new_page(viewport={'width':1360,'height':900})\n"
        "    pg.goto(%r, wait_until='networkidle', timeout=60000)\n"
        "    pg.wait_for_timeout(2500)\n"
        "%s"
        "    pg.wait_for_timeout(3000)\n"
        "    pg.screenshot(path=%r)\n"
        "    b.close()\n" % (
            url,
            ("    pg.click('text=%s')\n" % click_text) if click_text else "",
            path))
    r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return r.returncode == 0 and os.path.exists(path)


def check_ui():
    os.makedirs(SHOTS, exist_ok=True)
    port = free_port()
    proc = subprocess.Popen([PY, os.path.join(PROJ, "server.py"), "--port", str(port)],
                            cwd=PROJ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(60):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/status" % port, timeout=2).read()
                up = True
                break
            except Exception:
                time.sleep(0.3)
        if not up:
            add(False, "真实界面截图", "服务没起来")
            return
        stamp = time.strftime("%Y%m%d-%H%M%S")
        live = os.path.join(SHOTS, "界面-直播-%s.png" % stamp)
        vod = os.path.join(SHOTS, "界面-点播-%s.png" % stamp)
        ok1 = shot("http://127.0.0.1:%d/" % port, live)
        ok2 = shot("http://127.0.0.1:%d/" % port, vod, click_text="点播")
        size = min(os.path.getsize(p) if os.path.exists(p) else 0 for p in (live, vod))
        add(ok1 and ok2 and size > 20 * 1024, "真实界面截图",
            "直播/点播两张，最小 %d B（%s）" % (size, os.path.relpath(SHOTS, ROOT)))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


# ---------- ④ 升级路径 ----------
def check_upgrade():
    # 旧配置（装好的那份）能被新版读出来：端口、线路、播放器路径
    old = {}
    try:
        old = json.loads(open(os.path.join(INSTALL, "config.json"), encoding="utf-8-sig").read())
    except Exception:
        pass
    add(bool(old.get("port") and old.get("provider")), "升级后旧配置可读",
        "旧 config.json：port=%s provider=%s" % (old.get("port"), old.get("provider"))
        if old else "这台机器上没有装好的旧配置")

    # 免安装包结构完整：exe + 同级 config + _internal 里的 web/providers/tools/_meta.py
    ok = False
    detail = "包不存在"
    try:
        with zipfile.ZipFile(ZIP) as z:
            names = set(z.namelist())
            need = ["IPTV-Shell/IPTV-Shell.exe", "IPTV-Shell/config.json",
                    "IPTV-Shell/_internal/server.py",
                    "IPTV-Shell/_internal/providers/_meta.py",
                    "IPTV-Shell/_internal/web/index.html",
                    "使用说明.txt"]
            missing = [n for n in need if n not in names]
            ok = not missing
            detail = "缺 %s" % missing if missing else "%d 个文件，%.1f MB" % (
                len(names), os.path.getsize(ZIP) / 1048576)
    except Exception as e:
        detail = repr(e)
    add(ok, "免安装包结构完整", detail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ui", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    print("=" * 60)
    print("IPTV 外壳模板 · 发版前快检")
    print("=" * 60)
    check_start()
    check_features()
    if not a.no_ui:
        check_ui()
    check_upgrade()
    fails = sum(1 for r in rows if r[0] == "FAIL")
    print("-" * 60)
    if fails:
        print("结论：不可发 —— %d 项 FAIL，先修再谈发版（%.1fs）" % (fails, time.time() - t0))
        sys.exit(1)
    print("结论：这版可以交 / 可以发 —— %d 项 PASS（%.1fs）" % (len(rows), time.time() - t0))


if __name__ == "__main__":
    main()
