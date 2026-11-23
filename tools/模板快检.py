#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板快检：起一个临时端口，把外壳该有的接口全部打一遍，出 PASS/FAIL。

用法：
  python tools/模板快检.py                      # 用 config.json 里的线路和端口
  python tools/模板快检.py --provider m3u --port 8906
  python tools/模板快检.py --register           # 只登记（不跑），给发版闸门用
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(HERE, 'server.py')
PY = sys.executable


def cfg_paths():
    """配置按这个顺序找：exe 同级的 config.json → 环境变量 IPTV_CONFIG → 包内/HERE 的那份。
    exe 同级那份优先，是为了让用户「改 IPTV-Shell 文件夹里的 config.json」真的生效。"""
    out = []
    if getattr(sys, 'frozen', False):
        out.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'config.json'))
    env = os.environ.get('IPTV_CONFIG')
    if env:
        out.append(os.path.abspath(env))
    out.append(os.path.join(HERE, 'config.json'))
    return [p for p in out if p]


def _subsystem(path):
    """读 PE 头里的子系统：3=控制台，2=窗口程序。窗口程序没法无窗口解码，会挂住。"""
    try:
        with open(path, 'rb') as f:
            if f.read(2) != b'MZ':
                return 0
            f.seek(0x3C)
            off = int.from_bytes(f.read(4), 'little')
            f.seek(off)
            if f.read(4) != b'PE\0\0':
                return 0
            f.seek(off + 24 + 68)
            return int.from_bytes(f.read(2), 'little')
    except Exception:
        return 0


def _any_player():
    """配置或常见路径里有没有能真播的播放器（带窗口的也算，只是不拿它做静默解码）"""
    for p in cfg_paths():
        try:
            with open(p, encoding='utf-8-sig') as f:
                for x in (json.load(f).get('mpv') or []):
                    if x and os.path.isfile(x):
                        return True
        except Exception:
            pass
    for p in (r'D:\Program Files (x86)\mpv-Yaozhi\Yaoplayer.exe',
              r'D:\软件\播放器\mpv\mpv.exe'):
        if os.path.isfile(p):
            return True
    return bool(shutil.which('mpv.exe'))


def find_mpv():
    """找本机可用于「无窗口静默解码」的 mpv。

    先找控制台版 mpv.exe（最稳）；没有的话退一步用带窗口的 mpv.exe（--vo=null 也常常能跑完），
    但 Yaoplayer / Yaozhi 那些带壳播放器一律不碰（它们会卡着等窗口，永远不退出）。
    真一个 mpv 都没有就返回空，自检跳过这一项。
    """
    cands = []
    for p in cfg_paths():
        try:
            with open(p, encoding='utf-8-sig') as f:
                cands += [x for x in (json.load(f).get('mpv') or []) if x]
        except Exception:
            pass
    for d in (r'D:\软件\播放器\mpv', r'C:\Program Files\mpv', r'C:\Program Files (x86)\mpv',
              os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'mpv')):
        if d and os.path.isdir(d):
            cands.append(os.path.join(d, 'mpv.exe'))
    pth = shutil.which('mpv.exe')
    if pth:
        cands.append(pth)

    good, gui = [], []
    for p in cands:
        if not os.path.isfile(p):
            continue
        name = os.path.basename(p).lower()
        if 'yaoplayer' in name or 'yaozhi' in name:
            continue
        if not name.startswith('mpv'):
            continue
        (good if _subsystem(p) == 3 else gui).append(p)
    for p in good + gui:
        return p
    return ''

ok_n = 0
bad_n = 0


def say(ok, name, detail=''):
    global ok_n, bad_n
    if ok:
        ok_n += 1
        print('  [OK]   %-34s %s' % (name, detail))
    else:
        bad_n += 1
        print('  [FAIL] %-34s %s' % (name, detail))


def free_port(start=8906):
    for p in range(start, start + 40):
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return start


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b'', e.headers.get('Content-Type', '')
    except Exception as e:
        return 0, str(e).encode('utf-8'), ''


def safe_json(body, default=None):
    """接口可能返回空/非 JSON，别在这里抛异常把 check 打断，也别让 exe 弹错误框"""
    try:
        return json.loads(body)
    except Exception:
        return default if default is not None else {}


def _player_pids(tag):
    """列出「命令行里带本次快检标记」的播放器进程 —— 用来判断点了以后到底起没起
    （只认播放器名 + 本次专用管道名，不会误伤用户自己打开的窗口，也不会把
      正在跑着的正式外壳那个窗口算进来）"""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match"
          " 'mpv|vlc|PotPlayer|Yaoplayer' -and $_.CommandLine -like"
          " '*%s*' } | Select-Object -ExpandProperty ProcessId") % tag
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                       'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        out = subprocess.run([exe, '-NoProfile', '-Command', ps],
                             capture_output=True, timeout=25,
                             creationflags=0x08000000)
        txt = (out.stdout or b'').decode('utf-8', 'ignore')
        return [int(x) for x in txt.split() if x.strip().isdigit()]
    except Exception:
        return []


def _kill_pids(pids):
    if not pids:
        return
    ids = ','.join(str(int(x)) for x in pids)
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                       'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        subprocess.run([exe, '-NoProfile', '-Command',
                        'Stop-Process -Id %s -Force -ErrorAction SilentlyContinue' % ids],
                       capture_output=True, timeout=20, creationflags=0x08000000)
    except Exception:
        pass


def _window_titles(pids):
    """看一眼播放器窗口标题里有没有台名（用户看到的就是这个）"""
    if not pids:
        return []
    ids = ','.join(str(int(x)) for x in pids)
    ps = ('Get-Process -Id %s -ErrorAction SilentlyContinue | '
          'ForEach-Object { $_.MainWindowTitle }' % ids)
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                       'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        out = subprocess.run([exe, '-NoProfile', '-Command', ps],
                             capture_output=True, timeout=20,
                             creationflags=0x08000000)
        return [x.strip() for x in (out.stdout or b'').decode('utf-8', 'ignore').splitlines()
                if x.strip()]
    except Exception:
        return []


def _player_windows(tag):
    """真正数「窗口」：Yaoplayer 是外壳+内核两个进程，只该有 1 个窗口。
    用户说的「跳出两个播放器」就是两个窗口，不是两个进程。"""
    ps = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match"
          " 'mpv|vlc|PotPlayer|Yaoplayer' -and $_.CommandLine -like"
          " '*%s*' } |"
          " ForEach-Object { $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue;"
          " if ($p -and $p.MainWindowHandle -ne 0) { $p.MainWindowTitle } }") % tag
    exe = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                       'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    try:
        out = subprocess.run([exe, '-NoProfile', '-Command', ps],
                             capture_output=True, timeout=25,
                             creationflags=0x08000000)
        return [x.strip() for x in (out.stdout or b'').decode('utf-8', 'ignore').splitlines()
                if x.strip()]
    except Exception:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--provider', default=None)
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--register', action='store_true')
    ap.add_argument('--demo', action='store_true',
                    help='演示数据自测（线路=mock 时照样给「可以交」，只给模板作者用）')
    a = ap.parse_args()
    if a.register:
        print('模板快检：已登记（本机程序，暂无发版闸门）')
        return 0

    cfg, cfg_file = {}, ''
    for p in cfg_paths():
        try:
            with open(p, encoding='utf-8-sig') as f:
                cfg, cfg_file = json.load(f), p
                break
        except Exception:
            continue
    prov = a.provider or cfg.get('provider') or 'mock'
    port = a.port or free_port()

    print('模板快检：线路=%s 端口=%d' % (prov, port))
    print('          配置=%s' % (cfg_file or '没找到 config.json，按演示线路跑'))
    log = os.path.join(os.environ.get('TEMP') or '/tmp', 'iptv_shell_check.log')
    tag = 'iptv-check-%d' % port      # 本次快检专用标记（管道名 + 进程命令行都带它）
    env = dict(os.environ)
    env['IPTV_MUTE'] = '1'   # 快检会真起播放器窗口（要证明画面出来），默认静音别吵到人
    if cfg_file:
        # 用临时配置跑：端口、管道都换成本次专用的，绝不去抢用户正在用的那个外壳
        try:
            c2 = dict(cfg)
            c2['port'] = port
            c2['mpv_pipe'] = '\\\\.\\pipe\\' + tag
            tcfg = os.path.join(os.environ.get('TEMP') or '/tmp', 'iptv_check_cfg_%d.json' % port)
            with open(tcfg, 'w', encoding='utf-8') as f:
                json.dump(c2, f, ensure_ascii=False, indent=2)
            env['IPTV_CONFIG'] = tcfg
        except Exception:
            env['IPTV_CONFIG'] = cfg_file
    proc = subprocess.Popen([PY, SERVER, '--port', str(port), '--provider', prov],
                            cwd=HERE, env=env, stdout=open(log, 'w', encoding='utf-8'),
                            stderr=subprocess.STDOUT)
    base = 'http://127.0.0.1:%d' % port
    try:
        up = False
        for _ in range(50):
            try:
                st, body, _ct = get(base + '/status', timeout=3)
                if st == 200:
                    up = True
                    break
            except Exception:
                time.sleep(0.4)
        say(up, '服务能起来', base)
        if not up:
            print('      看日志：%s' % log)
            return 1

        status = safe_json(get(base + '/status')[1])
        say(status.get('channels', 0) > 0, '频道数', str(status.get('channels')))
        say(status.get('error') is None, '没有报错', str(status.get('error')))

        st, body, ct = get(base + '/')
        say(st == 200 and '我的' in body.decode('utf-8', 'ignore')[:4000], '网页能打开',
            '%d 字节' % len(body))

        st, body, _ = get(base + '/api/live.json')
        live = safe_json(body)
        say(live.get('total', 0) > 0, '直播 JSON', '共 %s 台 / %d 组'
            % (live.get('total'), len(live.get('groups') or [])))

        st, body, _ = get(base + '/live.m3u')
        say(st == 200 and body.startswith(b'#EXTM3U'), '直播清单', '%d 字节' % len(body))

        caps = status.get('caps') or {}
        if caps.get('epg'):
            st, body, _ = get(base + '/epg.xml')
            n = body.count(b'<programme')
            if n:
                say(st == 200 and n > 0, 'XMLTV 节目单', '%d 条节目' % n)
            else:
                print('  [skip] 这条线路没有节目单（不影响观看）')
            codes = [c for g in (live.get('groups') or []) for c in [g['items'][0]['code']]] if live.get('groups') else []
            if codes and n:
                st, body, _ = get(base + '/epg?code=' + codes[0])
                say(safe_json(body).get('list') is not None, '单频道节目单', codes[0])

        first_img = ''
        if caps.get('vod'):
            st, body, _ = get(base + '/api/vod.json')
            meta = safe_json(body)
            if meta.get('total', 0) > 0:
                say(True, '点播统计', '共 %s 部 / %s 个题材'
                    % (meta.get('total'), len(meta.get('tags') or [])))
                tp = meta.get('types') or {}
                bd = meta.get('bands') or {}
                # 左栏全靠这两组数据：类型空 = 分类栏整块空白，这是用户实实在在会撞到的坑
                say(bool(tp) and bool(bd), '片库分类（左栏）',
                    '%d 类 / %d 个档位：%s' % (len(tp), len(bd),
                                              '、'.join('%s %s' % (k, v) for k, v in
                                                       list(tp.items())[:5]) or '空'))
                st, body, _ = get(base + '/api/vod.items.json?limit=3')
                items = safe_json(body).get('items') or []
                say(len(items) > 0, '点播条目', str(items[0]['name']) if items else '空')
                if items:
                    first_img = items[0].get('img') or ''
                    it = items[0]
                    st, body, _ = get(base + '/vod/%s/%s.m3u8' % (it['pid'], it['cid']))
                    head = body.decode('utf-8', 'ignore')
                    # 只有「地址指回本机 /vod/ 或 /seg」才算被改写；源站自己用 127.0.0.1 不算
                    hp = base.split('//', 1)[1]
                    rewritten = False
                    for ln in head.splitlines():
                        ln = ln.strip()
                        if not ln.startswith('http'):
                            continue
                        u = urllib.parse.urlsplit(ln)
                        if u.netloc == hp and u.path.startswith(('/vod', '/seg')):
                            rewritten = True
                            break
                    say(st in (200, 404), '点播清单可请求', 'HTTP %d' % st)
                    if st == 200:
                        say(not rewritten, '分片走直连（不绕本机）',
                            '是' if not rewritten else '被改写成转发地址了')
                # 成剧（多集）单独盯：电影单片一直是绿的，但成剧整部 id 签不出，
                # 适配器不回 vod_episodes 时外壳会把剧集能力判成没有 → 点电视剧全 404。
                ep_it = None
                for c in [k for k in (meta.get('groups') or {}) if '电视剧' in k][:6]:
                    st, body, _ = get(base + '/api/vod.items.json?cat=%s&limit=200'
                                      % urllib.parse.quote(c))
                    for x in (safe_json(body).get('items') or []):
                        # vol 有的线路给数字（'32'），有的给文案（'全32集'），都得认
                        d = ''.join(ch for ch in str(x.get('vol') or '') if ch.isdigit())
                        if d and int(d) > 1:
                            ep_it = x
                            break
                    if ep_it:
                        break
                if not ep_it:
                    print('  [skip] 片库里没找到多集内容，跳过成剧检查')
                else:
                    st, body, _ = get(base + '/vod/%s/%s.m3u8' % (ep_it['pid'], ep_it['cid']))
                    head = body.decode('utf-8', 'ignore')
                    eps = [ln.strip() for ln in head.splitlines()
                           if ln.strip().startswith('http') and '/vod/ep/' in ln]
                    say(st == 200 and len(eps) >= 2, '成剧返回集清单',
                        '%s（%s 集）→ %d 集' % (ep_it['name'], ep_it.get('vol'), len(eps))
                        if eps else 'HTTP %d，一条集地址都没有（适配器多半没接 vod_episodes）' % st)
                    if eps:
                        st, body, _ = get(eps[0])
                        ehead = body.decode('utf-8', 'ignore')
                        segs = [ln.strip() for ln in ehead.splitlines()
                                if ln.strip() and not ln.startswith('#')]
                        direct = bool(segs) and all(
                            not (urllib.parse.urlsplit(s).netloc == hp
                                 and urllib.parse.urlsplit(s).path.startswith(('/vod', '/seg')))
                            for s in segs)
                        say(st == 200 and bool(segs) and direct, '成剧第 1 集能签出分片',
                            'HTTP %d · %d 个分片 · %s' % (st, len(segs),
                                                          '直连源站' if direct else '被改写成转发地址'))
                # 2026-11-23 用户定：不许没验证就给片源贴“放不了”的标签，这里不再做 DRM 预判检查
            else:
                print('  [skip] 这条线路没有点播片库（页签仍在，进去显示空态）')
        else:
            print('  [skip] 这条线路没开点播（页签仍在，进去显示空态）')

        if caps.get('poster'):
            if prov == 'mock':
                # 演示线路的海报是内置占位图形（SVG），不是真海报，别拿它当 FAIL
                print('  [skip] 演示线路的海报是占位图形，不校验真图')
            else:
                rel = first_img or 'x'
                st, body, ct = get(base + '/poster?u=' + urllib.parse.quote(rel), timeout=25)
                if first_img:
                    say(st == 200 and len(body) > 1024, '海报能打开',
                        '%d 字节 / %s' % (len(body), ct))
                else:
                    say(st in (200, 404), '海报接口', 'HTTP %d %s' % (st, ct))
        else:
            print('  [skip] 这条线路没有海报')

        st, body, _ = get(base + '/favicon.ico')
        say(st == 200, '站内小图标', '%d 字节' % len(body))

        # --- 真播放：清单 → 分片 → 本机播放器解码，这一项是「打开就能看」的硬证据
        # 演示线路（mock）没有真实流，跳过这一段，免得白等
        groups = live.get('groups') or []
        if groups and groups[0].get('items') and prov != 'mock':
            code = groups[0]['items'][0]['code']
            name = groups[0]['items'][0].get('name', code)
            st, body, _ = get(base + '/ch/%s.m3u8' % code, timeout=20)
            head = body.decode('utf-8', 'ignore')
            urls = [x.strip() for x in head.splitlines() if x.strip().startswith('http')]
            say(st == 200 and bool(urls), '单频道清单', '%s HTTP %d' % (name, st))
            if urls:
                # 有些线路是「清单里套清单」：一路往下走，最多三层，最后拿到真正的分片
                st2, vbody, _ = get(urls[0], timeout=20)
                vtxt = vbody.decode('utf-8', 'ignore')
                hops, st3, sbody = 0, st2, vbody
                seg_url = urls[0]
                while hops < 3 and '#EXTM3U' in sbody.decode('utf-8', 'ignore')[:200]:
                    inner = [x.strip() for x in sbody.decode('utf-8', 'ignore').splitlines()
                             if x.strip().startswith('http')]
                    if not inner:
                        break
                    seg_url = inner[0]
                    st3, sbody, _ = get(seg_url, timeout=30)
                    hops += 1
                if st3 == 200 and len(sbody) > 2000:
                    say(True, '分片能拉到', 'HTTP %d / %d 字节 / %d 层' % (st3, len(sbody), hops))
                else:
                    say(False, '分片能拉到', 'HTTP %d / %d 字节' % (st3, len(sbody)))
                mpv = find_mpv()
                if mpv:
                    cmd = [mpv, '--no-audio', '--vo=null', '--frames=6', '--no-terminal',
                           '--force-window=no', '--quiet', base + '/ch/%s.m3u8' % code]
                    timed_out = False
                    try:
                        rc = subprocess.run(cmd, capture_output=True, timeout=35,
                                            creationflags=0x08000000).returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        rc = -1
                    except Exception:
                        rc = -1
                    if timed_out:
                        print('  [skip] 本机播放器无窗口解码超时（它多半是带窗口的版本），'
                              '不影响播放')
                    else:
                        say(rc == 0, '本机播放器真解码',
                            '%s 退出码 %d' % (os.path.basename(mpv), rc))
                else:
                    if _any_player():
                        print('  [skip] 本机只有带窗口的播放器，跳过无窗口解码自检'
                              '（不影响播放，点台照样交给它打开）')
                    else:
                        print('  [skip] 这台电脑没找到播放器'
                              '（装上 mpv / Yaoplayer / VLC 任一，路径填进 config.json 的 mpv）')

        # --- 点播放：接口回 ok 不算数，播放器进程必须真的起来
        # （历史坑：Yaoplayer 被当成 mpv 塞了 --input-ipc-server，静默退出，
        #   接口照样回 ok，用户看到的是「点了没反应」）
        first = second = None
        for g in groups:
            items = g.get('items') or []
            for it in items:
                if not first:
                    first = it
                elif not second:
                    second = it
                    break
            if second:
                break
        if prov != 'mock' and first and _any_player():
            before = set(_player_pids(tag))
            st, body, _ = get(base + '/play?u=/ch/%s.m3u8&t=%s'
                              % (first['code'],
                                 urllib.parse.quote(first.get('name') or '')))
            j = safe_json(body, {})
            new = []
            for _ in range(12):
                time.sleep(0.5)
                new = [x for x in _player_pids(tag) if x not in before]
                if new:
                    break
            if new:
                time.sleep(2)
                wins = _player_windows(tag)
                if wins:
                    say(True, '点播放真起播放器',
                        '窗口 %d 个 / 标题：%s' % (len(wins), '、'.join(wins)))
                else:
                    say(False, '点播放只起进程没窗口',
                        '进程 %d 个但一个窗口都没有，用户会以为点了没反应' % len(new))
                # 单窗口闸门：再点第二个台，播放器只许有一个窗口
                if second:
                    get(base + '/play?u=/ch/%s.m3u8&t=%s'
                        % (second['code'],
                           urllib.parse.quote(second.get('name') or '')))
                    time.sleep(4)
                    w2 = _player_windows(tag)
                    if len(w2) <= 1:
                        say(True, '换台只留一个窗口',
                            '连点两个台，播放器窗口还是 %d 个：%s'
                            % (len(w2), '、'.join(w2) or '（暂无标题）'))
                    else:
                        say(False, '换台跳出两个播放器',
                            '连点两个台后变成 %d 个窗口：%s'
                            % (len(w2), '、'.join(w2)))
                _kill_pids(new)
            else:
                say(False, '点播放没起播放器',
                    '接口回 ok=%s，但端口 %d 上没有任何播放器进程：'
                    '多半是播放器不认外壳传的参数' % (j.get('ok'), port))
    except Exception as exc:
        # 任何一项接口抽风都不许把 check 打断、弹错误框，记成 FAIL 继续走结论
        say(False, '快检过程没跑完', '%s: %s' % (type(exc).__name__, exc))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    demo = (prov == 'mock')
    if demo and not a.demo:
        print('\n结论：没接入（现在跑的是演示数据，台表和片库都是假数据，不能算接好）'
              '（OK %d / FAIL %d）' % (ok_n, bad_n))
        print('      真接线路：在 config.json 里把 provider 改成 m3u 并填 m3u_path，'
              '或照 providers\\转发示例.py 写适配器，再跑一次 check。')
        return 2
    print('\n结论：%s（OK %d / FAIL %d）'
          % ('可以交' if bad_n == 0 and ok_n > 0 else '不能交', ok_n, bad_n))
    return 0 if bad_n == 0 and ok_n > 0 else 1


if __name__ == '__main__':
    sys.exit(main())
