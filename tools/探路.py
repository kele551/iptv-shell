#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探路：在「这台电脑所在的网络」里先找有没有现成的数据流，再决定怎么接。

智能体接到「帮我接一条 IPTV 线路」时的**第一步**就该跑它，别先反问用户要地址。

它只做本机可查、低风险的事（不发包扫描外网、不试密码）：
  1. 本机正在跑的服务端口（可能已经是别人接好的数据源）
  2. 本机磁盘上的 m3u / m3u8 / xmltv 清单（现成可用的最多）
  3. 已有的 IPTV 项目与配置（本机已跑通的 IPTV 项目、模板自己）
  4. 系统里有没有 IPTV 相关的抓源档案 / 账号配置（只看存在与否，不打印内容）

用法：
  python tools/探路.py
  python tools/探路.py --json     # 给程序读
"""
import argparse
import json
import os
import socket
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser('~')
# 不写死某台机器的盘符：默认搜「模板自己 + 用户常见放片源的地方」，另可用 --roots / IPTV_SEARCH_ROOTS 追加
SEARCH_ROOTS = [HERE,
                os.path.join(HOME, 'Desktop'), os.path.join(HOME, 'Downloads'),
                os.path.join(HOME, 'Documents'), os.path.join(HOME, 'Videos'),
                os.path.join(HOME, '视频'), os.path.join(HOME, '下载'),
                r'F:\桌面', r'F:\下载', r'F:\Download']
MAX_DEPTH = 3
SKIP_DIRS = {'node_modules', '__pycache__', '.git', 'builds', 'archive', 'toolchain',
             'site-packages', 'AppData'}
PROJECT_HINTS = ('iptv', '直播', 'livetv', 'live-tv')


def listening_ports():
    out = set()
    if os.name != 'nt':
        return []
    try:
        import subprocess
        r = subprocess.run(['netstat', '-ano', '-p', 'TCP'], capture_output=True, timeout=20,
                           creationflags=0x08000000)
        txt = (r.stdout or b'').decode('gbk', 'ignore')
        for line in txt.splitlines():
            if 'LISTENING' not in line:
                continue
            parts = line.split()
            if len(parts) < 5 or ':' not in parts[1]:
                continue
            port = int(parts[1].rsplit(':', 1)[-1])
            if 8000 <= port <= 9999 or port in (80, 8080, 5000, 3080):
                out.add((port, parts[1], parts[4]))
    except Exception:
        pass
    return [{'port': p, 'addr': a, 'pid': i} for p, a, i in sorted(out)]


def probe_http(port):
    """看这个端口像不像 IPTV 服务（只打本机地址，5 秒超时）"""
    import urllib.request
    for path in ('/status', '/api/meta', '/'):
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, path), timeout=5) as r:
                body = r.read(4000).decode('utf-8', 'ignore')
                return {'path': path, 'looks': ('iptv' in body.lower() or '直播' in body
                                                or 'channels' in body or '外壳' in body),
                        'sample': body[:120].replace('\n', ' ')}
        except Exception:
            continue
    return None


def _is_xmltv(path):
    """xml 太多了：只有开头是 <tv 的才算节目单，别把 AndroidManifest 也算进来"""
    try:
        with open(path, 'rb') as f:
            head = f.read(400).lstrip()
        return b'<tv' in head or b'<TV' in head
    except Exception:
        return False


def walk_lists(roots):
    found = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip('\\').rstrip('/').count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
            for f in filenames:
                p = os.path.join(dirpath, f)
                low = f.lower()
                try:
                    if os.path.getsize(p) >= 8 * 1024 * 1024:
                        continue
                except Exception:
                    continue
                if low.endswith(('.m3u', '.m3u8')):
                    found.add(p)
                elif low.endswith('.xml') and _is_xmltv(p):
                    found.add(p)
    return sorted(found)[:40]


def drive_roots(budget=18.0):
    """盘符浅扫：只认名字里带 iptv / 直播 的目录，最多下探 3 层，带时间预算。

    不扫这一步，用户把线路项目放在 F:\\Harness\\projects 这类地方时，
    探路就只会看桌面和文档，等于「明明有源却报没有」。
    """
    if os.name != 'nt':
        return []
    import string
    import time
    t0 = time.time()
    found = []
    junk = SKIP_DIRS | {'windows', 'program files', 'program files (x86)', 'programdata',
                        '$recycle.bin', 'system volume information', 'recovery',
                        'perflogs', 'msocache'}
    for d in string.ascii_uppercase:
        root = d + ':\\'
        if not os.path.isdir(root):
            continue
        stack = [(root, 0)]
        while stack and time.time() - t0 < budget:
            cur, dep = stack.pop()
            try:
                subs = os.listdir(cur)
            except Exception:
                continue
            for s in subs:
                p = os.path.join(cur, s)
                if not os.path.isdir(p):
                    continue
                low = s.lower()
                if low in junk or s.startswith('.'):
                    continue
                if any(k in low for k in PROJECT_HINTS):
                    found.append(p)
                    continue
                if dep < 2:
                    stack.append((p, dep + 1))
    return found[:10]


def known_projects(roots):
    """不认死项目名：凡是名字带 iptv/直播、或里面有 直播数据服务.py 的目录都算"""
    out = set()
    here_norm = os.path.normcase(HERE)
    for root in roots:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip('\\').rstrip('/').count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= 2:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith('.')]
            if os.path.normcase(dirpath) == here_norm:
                continue
            name = os.path.basename(dirpath).lower()
            if any(k in name for k in PROJECT_HINTS) or '直播数据服务.py' in filenames:
                out.add(dirpath)
                dirnames[:] = []
    return sorted(out)[:10]


def _taste(p):
    """给清单一个「像直播还是像点播」的味道：条数 + 头两三个名字"""
    try:
        with open(p, 'r', encoding='utf-8-sig', errors='ignore') as f:
            lines = f.readlines()
    except Exception:
        return (0, '')
    names = []
    for ln in lines:
        s = ln.strip()
        if s.startswith('#EXTINF'):
            nm = s.rsplit(',', 1)[-1].strip()
            if nm:
                names.append(nm)
        if len(names) >= 3:
            break
    return (len([1 for ln in lines if ln.strip().startswith('#EXTINF')]),
            '、'.join(names))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--roots', default='', help='额外要搜的目录，多个用 ; 隔开')
    a = ap.parse_args()
    roots = list(SEARCH_ROOTS)
    extra = [x.strip() for x in (a.roots or os.environ.get('IPTV_SEARCH_ROOTS', '')).split(';') if x.strip()]
    roots.extend(extra)
    # 盘符浅扫出来的「名字像 IPTV」的目录也加进去，不然后面找不到真实项目
    for d in drive_roots():
        if d not in roots:
            roots.append(d)
    data = {'search_roots': [r for r in roots if os.path.isdir(r)],
            'listening': [], 'playlists': walk_lists(roots),
            'projects': known_projects(roots)}

    for item in listening_ports():
        h = probe_http(item['port'])
        if h:
            item.update(h)
        data['listening'].append(item)

    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print('探路：这台电脑所在网络上的现成数据流')
    print('   搜过：%s' % ' | '.join(data['search_roots']))
    print('\n1) 本机在跑的服务')
    if not data['listening']:
        print('   （没有发现 8000-9999 端口的服务）')
    for it in data['listening']:
        tag = '像 IPTV 服务' if it.get('looks') else '不确定'
        print('   - 端口 %-5d %-22s %s' % (it['port'], it['addr'], tag))
        if it.get('sample'):
            print('       %s' % it['sample'][:100])

    print('\n2) 现成清单文件（最省事的接法：provider 用 m3u）')
    if not data['playlists']:
        print('   （没找到 m3u/m3u8/xmltv 文件；去问用户盒子/路由器里存的清单在哪）')
    for p in data['playlists']:
        if p.lower().endswith('.xml'):
            print('   - %-64s 节目单(xmltv)' % p)
        else:
            n, nm = _taste(p)
            print('   - %-64s %d 条／%s' % (p, n, (nm[:46] + '…') if len(nm) > 46 else nm))

    print('\n3) 已有的 IPTV / 直播项目（可以直接照 转发示例.py 转发）')
    for p in data['projects'] or ['（无）']:
        print('   - %s' % p)

    print('\n下一步：')
    print('   有清单  → config.json 填 "provider": "m3u" + "m3u_path"，不写代码')
    print('             台名是频道（CCTV / 卫视 / 本地台）的那份当直播填 m3u_path；')
    print('             名字是剧名/片名、条数不多、没有频道号的那份当点播填 vod_m3u（接点播用）；')
    print('             只有一份就只填 m3u_path，点播页签照样在、进去显示空态')
    print('   有服务  → 照 providers/转发示例.py 写一个转发适配器')
    print('   都没有  → 这时才去问用户：盒子型号 / 是不是运营商 IPTV / 有没有抓源档案')
    print('             （问不到也可以让用户把 m3u 拖进 data\\ 目录，用 --roots 再跑一遍）')
    print('   接完都要跑：python tools/模板快检.py --provider <线路>')
    return 0


if __name__ == '__main__':
    sys.exit(main())
