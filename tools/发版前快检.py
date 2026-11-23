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


def line_cfg():
    """优先拿「已装好那份」的线路（用户真正在看的那条），没装才用项目里的示例配置。
    2026-11-22 教训：以前只跑项目 config 里的 mock，成剧整块 404 也照样全绿。"""
    for p in (os.path.join(INSTALL, "config.json"), os.path.join(PROJ, "config.json")):
        try:
            c = json.loads(open(p, encoding="utf-8-sig").read())
            if c.get("provider"):
                return c, p
        except Exception:
            continue
    return {}, ""


def check_features():
    port = free_port()
    lc, lp = line_cfg()
    env = dict(os.environ)
    if lc.get("svc_py"):
        # 转发式线路靠这个变量找到外部服务；不传就会当成空线路，快检白跑
        env["IPTV_SVC_PY"] = lc["svc_py"]
    p = subprocess.run([PY, os.path.join(PROJ, "tools", "模板快检.py"),
                        "--port", str(port), "--provider", lc.get("provider") or "mock"],
                       cwd=PROJ, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900)
    body = (p.stdout or "") + (p.stderr or "")
    print("  快检线路=%s（%s）" % (lc.get("provider") or "mock", lp or "项目示例配置"))
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


# ---------- ③ 版权保护 / 非标编码片源必须被拦下 ----------
# 2026-11-23 用户报「电视剧不显示画面、会跳集、快进不了」：源站有的是 DRM 加密，
# 有的是私有编码，播放器解不了 → 黑屏跑到头自动跳集。这条闸门用 mock 里固定的
# drmdemo 假清单做确定性验证（不看上游脸色的那种）。
def check_drm_gate():
    port = free_port()
    proc = subprocess.Popen([PY, os.path.join(PROJ, "server.py"), "--port", str(port),
                             "--provider", "mock"], cwd=PROJ,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/status" % port, timeout=2).read()
                up = True
                break
            except Exception:
                time.sleep(0.3)
        if not up:
            add(False, "版权保护片源拦下", "mock 服务没起来")
            return
        base = "http://127.0.0.1:%d" % port
        chk = json.loads(urllib.request.urlopen(
            base + "/api/vod.check.json?pid=1&cid=drmdemo", timeout=20).read().decode("utf-8"))
        add(chk.get("ok") is False and chk.get("code") == "drm"
            and "秒能放" in (chk.get("msg") or ""), "版权保护片源拦下",
            "%s：%s" % (chk.get("code"), (chk.get("msg") or "")[:34]))
        r2 = json.loads(urllib.request.urlopen(
            base + "/play?u=%2Fvod%2F1%2Fdrmdemo.m3u8&t=DRM", timeout=20).read().decode("utf-8"))
        add(r2.get("ok") is False, "版权保护片源不起黑窗",
            "拦下并回原因：%s" % ((r2.get("err") or "")[:30]))
        # 正常片子不能被误杀：普通假清单必须判成可播
        chk2 = json.loads(urllib.request.urlopen(
            base + "/api/vod.check.json?pid=1&cid=okdemo", timeout=20).read().decode("utf-8"))
        add(chk2.get("ok") is True, "正常片源不被误拦",
            "okdemo 判为可播")
    except Exception as e:
        add(False, "版权保护片源拦下", repr(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


# ---------- ④ 签址凭证失效必须在页面上弹提示、并带可复制的操作稿 ----------
# 2026-11-23 用户要求：接口返回 retCode 非 0 后，页面要自己跳提示，里面直接给一段
# 「复制给智能体」的换 token 操作稿，不能让用户自己去猜。
def check_token_warn():
    port = free_port()
    env = dict(os.environ)
    env["IPTV_TOKEN_DEMO"] = '接口拒绝: {"retCode":"1","retMsg":"用户未登录或会话已失效"}'
    proc = subprocess.Popen([PY, os.path.join(PROJ, "server.py"), "--port", str(port),
                             "--provider", "mock"], cwd=PROJ, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(40):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/status" % port, timeout=2).read()
                up = True
                break
            except Exception:
                time.sleep(0.3)
        if not up:
            add(False, "签址失效弹提示", "mock 服务没起来")
            return
        base = "http://127.0.0.1:%d" % port
        st = json.loads(urllib.request.urlopen(base + "/status", timeout=10).read().decode("utf-8"))
        add(bool(st.get("token_error")), "状态接口报出签址失效",
            (st.get("token_error") or "")[:40])
        txt = urllib.request.urlopen(base + "/api/token.help.txt", timeout=10).read().decode("utf-8")
        add(len(txt) > 150 and "token" in txt.lower(), "提示里带换token操作稿",
            "%d 字节 / 首行：%s" % (len(txt), (txt.splitlines() or [""])[0][:30]))
        code = (
            "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    b=p.chromium.launch(channel='msedge')\n"
            "    pg=b.new_page(viewport={'width':1360,'height':900})\n"
            "    pg.goto(%r, wait_until='networkidle', timeout=60000)\n"
            "    pg.wait_for_timeout(2200)\n"
            "    op=pg.evaluate(\"document.getElementById('tokWarn').open\")\n"
            "    n=pg.evaluate(\"document.getElementById('tokWarnText').innerText.length\")\n"
            "    print('TOKOK' if op and n>150 else 'TOKBAD', op, n)\n"
            "    b.close()\n" % (base + "/",)
        )
        r = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout or "") + (r.stderr or "")
        add(out.startswith("TOKOK") or "TOKOK" in out, "签址失效弹提示",
            "弹窗已打开且操作稿已加载" if "TOKOK" in out else out[-120:])
    except Exception as e:
        add(False, "签址失效弹提示", repr(e))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


# ---------- ⑤ 真实界面截图 ----------
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
            need = ["IPTV-Shell/IPTV-Shell.exe", "IPTV-Shell/config.example.json",
                    "IPTV-Shell/_internal/server.py",
                    "IPTV-Shell/_internal/providers/_meta.py",
                    "IPTV-Shell/_internal/web/index.html",
                    "使用说明.txt"]
            missing = [n for n in need if n not in names]
            # 包里不许带 config.json：带了就会在覆盖安装时把用户的线路配置冲掉
            if "IPTV-Shell/config.json" in names:
                missing.append("包里不该有 IPTV-Shell/config.json（会冲掉用户配置）")
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
    check_token_warn()
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
