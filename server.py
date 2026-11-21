#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IPTV 外壳模板 · 通用服务（纯标准库，一个文件跑起来）

它只做四件事：
  1. 起一个本机 HTTP 服务（默认 8905），提供网页外壳 + m3u/xmltv + JSON 接口
  2. 从 providers/<线路名>.py 取数据（线路适配器就是「数据流接入点」）
  3. 缓存 + 后台刷新，页面永远是秒开
  4. 点一下把播放地址交给本机 MPV

换线路只改 config.json 里的 "provider"，不用碰这个文件，也不用改网页。

用法：
  python server.py                 # 前台跑
  python server.py --port 8906     # 换端口
  python server.py --provider mock # 换线路
"""
import base64
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, 'config.json')
WEB_DIR = os.path.join(BASE, 'web')

START_TS = time.time()

# 站内小图标（32x32），省得浏览器每次白拿一个 404
FAVICON_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAtklEQVR42mNkQANCGi7/GWgI3t3Yw4jM'
    'Z6Kn5djsYKKn5djsYqK35eiOYBwIy3GmgVEHDARgwSWhUTGPqhbd6EgiPgSobTk+M4dmGtgYKjvwiXB'
    'jqCxVHEJxFFDqEKqlAXIdQvVESKpDWKjtAP/Vj6lTENHaYqo5gFyLKXYApRZTlAipZfngLYpx1Vy0qA1'
    'Hm2SjDhh4B6D31egJ3t3Yw8iErcNIL8tRooCejkC2iwlf15ke3XMAKiVA1FwgEm8AAAAASUVORK5CYII=')

# ---------------------------------------------------------------- 配置


def load_config():
    cfg = {
        'port': 8905,
        'title': '我的IPTV',
        'sub': '本机外壳',
        'provider': 'mock',
        'ttl': {'live': 600, 'epg': 900},
        'mpv': [
            r'D:\Program Files (x86)\mpv-Yaozhi\Yaoplayer.exe',
            r'D:\软件\播放器\mpv\mpv.exe',
            r'C:\Program Files\mpv\mpv.exe',
        ],
        'mpv_pipe': r'\\.\pipe\iptv-shell',
        'cache_dir': os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
                                  'IPTV外壳'),
        'user_agent': 'IPTV-Shell/1.0',
    }
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding='utf-8-sig') as f:
            user = json.load(f)
        cfg.update(user)
        cfg['ttl'] = {**{'live': 600, 'epg': 900}, **(user.get('ttl') or {})}
    return cfg


CFG = load_config()
PORT = int(CFG['port'])
TITLE = CFG['title']
SUB = CFG['sub']
TTL_LIVE = int(CFG['ttl']['live'])
TTL_EPG = int(CFG['ttl']['epg'])
CACHE_DIR = os.path.expandvars(CFG['cache_dir'])
LOG_PATH = os.path.join(CACHE_DIR, '外壳.log')


def log(msg):
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


# ---------------------------------------------------------------- 线路适配器


class Provider(object):
    """线路适配器：就是 providers 目录里那个 .py 文件，缺的函数一律有默认实现"""

    def __init__(self, name):
        self.name = name
        path = os.path.join(BASE, 'providers', name + '.py')
        if not os.path.isfile(path):
            raise SystemExit('找不到线路适配器：%s（看 providers/ 目录）' % path)
        spec = importlib.util.spec_from_file_location('provider_' + name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.mod = mod
        self.title = getattr(mod, 'TITLE', None) or TITLE
        self.sub = getattr(mod, 'SUB', None) or SUB
        self.caps = getattr(mod, 'CAPS', {}) or {}
        # 能力按「有没有实现这个函数」自动推断，写 CAPS 只用于关掉某个能力
        auto = {
            'live': hasattr(mod, 'channels'),
            'epg': hasattr(mod, 'epg'),
            'vod': hasattr(mod, 'vod_items'),
            'search': hasattr(mod, 'vod_search'),
            'poster': hasattr(mod, 'poster'),
            'variant': hasattr(mod, 'live_variant'),
            'seg': hasattr(mod, 'seg'),
            'episodes': hasattr(mod, 'vod_episodes'),
        }
        self.caps = {k: bool(self.caps.get(k, v)) for k, v in auto.items()}
        self.desc = getattr(mod, 'DESC', '')

    # --- 直播
    def channels(self):
        return list(self.mod.channels())

    def epg(self):
        return dict(self.mod.epg())

    def live_playlist(self, code, host):
        fn = getattr(self.mod, 'live_playlist', None)
        if fn is None:
            # 没写就用频道自带地址，单条清单直接交出去
            for c in ensure_channels():
                if c['code'] == code and c.get('url'):
                    return '#EXTM3U\n#EXTINF:-1,%s\n%s\n' % (c['name'], c['url'])
            raise KeyError(code)
        return fn(code, host)

    def live_variant(self, code, idx, host):
        return self.mod.live_variant(code, idx, host)

    def seg(self, url):
        return self.mod.seg(url)

    # --- 点播
    def vod_meta(self):
        fn = getattr(self.mod, 'vod_meta', None)
        return fn() if fn else {'total': 0, 'bands': {}, 'tags': [], 'groups': {},
                                'types': {}, 'subs': {}, 'sample': [], 'building': False}

    def vod_items(self, **kw):
        return self.mod.vod_items(**kw)

    def vod_search(self, kw, limit):
        return self.mod.vod_search(kw, limit)

    def vod_playlist(self, pid, cid, host):
        fn = getattr(self.mod, 'vod_playlist', None)
        if fn is None:
            raise KeyError('provider 没实现 vod_playlist')
        return fn(pid, cid, host)

    def vod_episodes(self, pid, cid):
        fn = getattr(self.mod, 'vod_episodes', None)
        if fn is None:
            return []
        return fn(pid, cid)

    def poster(self, url):
        fn = getattr(self.mod, 'poster', None)
        return fn(url) if fn else None


PROV = Provider(CFG['provider'])

# ---------------------------------------------------------------- 缓存


_cache = {'ch': [], 'ch_at': 0.0, 'epg': {}, 'epg_at': 0.0, 'err': None}
_cache_lock = threading.Lock()
_build_lock = threading.Lock()


def _channels_now():
    rows = []
    for c in PROV.channels():
        rows.append({
            'code': str(c.get('code') or c.get('id') or ''),
            'name': c.get('name') or '',
            'group': c.get('group') or '未分组',
            'num': c.get('num') or '',
            'url': c.get('url') or '',
        })
    rows = [r for r in rows if r['code']]
    return rows


def ensure_channels(force=False):
    age = time.time() - _cache['ch_at']
    if _cache['ch'] and not force and age < TTL_LIVE:
        return _cache['ch']
    with _build_lock:
        if _cache['ch'] and not force and time.time() - _cache['ch_at'] < TTL_LIVE:
            return _cache['ch']
        try:
            rows = _channels_now()
            with _cache_lock:
                _cache['ch'] = rows
                _cache['ch_at'] = time.time()
                _cache['err'] = None
        except Exception as e:
            _cache['err'] = str(e)
            log('取频道失败：%s' % e)
    return _cache['ch']


def ensure_epg(force=False):
    age = time.time() - _cache['epg_at']
    if _cache['epg'] and not force and age < TTL_EPG:
        return _cache['epg']
    if not PROV.caps.get('epg'):
        return {}
    try:
        data = PROV.epg() or {}
        _cache['epg'] = data
        _cache['epg_at'] = time.time()
    except Exception as e:
        log('取节目单失败：%s' % e)
    return _cache['epg']


def _background():
    """后台每 60 秒看一眼，到点就悄悄换新，绝不让页面等"""
    while True:
        time.sleep(60)
        try:
            if time.time() - _cache['ch_at'] >= TTL_LIVE:
                ensure_channels(force=True)
            if PROV.caps.get('epg') and time.time() - _cache['epg_at'] >= TTL_EPG:
                ensure_epg(force=True)
        except Exception:
            pass


# ---------------------------------------------------------------- 页面与清单


def _norm_code(c):
    return re.sub(r'[^A-Za-z0-9_\-.]', '', c)


def render_index(host):
    with open(os.path.join(WEB_DIR, 'index.html'), encoding='utf-8') as f:
        html = f.read()
    inject = ('<script>window.TPL=%s;</script>'
              % json.dumps({'title': PROV.title, 'sub': PROV.sub, 'port': PORT,
                            'host': host, 'caps': PROV.caps, 'provider': PROV.name},
                           ensure_ascii=False))
    html = html.replace('<body>', '<body>' + inject, 1)
    # 「直播 / 点播」两个页签永远都在：没有点播数据的线路进去显示空态提示，不留死角也不藏页签
    return html.replace('__TITLE__', PROV.title).replace('__SUB__', PROV.sub)


def build_live_json():
    rows = ensure_channels()
    epg = ensure_epg()
    groups = {}
    for r in rows:
        g = r['group'] or '未分组'
        info = epg.get(r['code']) or {}
        groups.setdefault(g, []).append({
            'code': r['code'], 'name': r['name'],
            'num': str(r['num']).zfill(3) if str(r['num']).isdigit() else str(r['num']),
            'cur': info.get('cur') or '', 'cur_start': info.get('cur_start') or '',
            'cur_end': info.get('cur_end') or '', 'next': info.get('next') or '',
            'next_start': info.get('next_start') or '',
        })
    out = []
    for g, items in groups.items():
        items.sort(key=lambda x: x['num'])
        out.append({'name': g, 'count': len(items), 'items': items})
    out.sort(key=lambda x: -x['count'])
    return {'updated': time.strftime('%H:%M:%S'), 'total': sum(x['count'] for x in out),
            'groups': out}


def build_live_m3u(host):
    rows = ensure_channels()
    epg = ensure_epg()
    rows = sorted(rows, key=lambda r: (r['group'], str(r['num']).zfill(3), r['name']))
    lines = ['#EXTM3U']
    for r in rows:
        info = epg.get(r['code']) or {}
        ext = '#EXTINF:-1 tvg-id="%s" tvg-name="%s" group-title="%s"' % (
            r['code'], r['name'], r['group'])
        if info.get('cur'):
            ext += ' tvg-title="%s"' % str(info['cur']).replace('"', "'")
        lines.append(ext + ',' + r['name'])
        lines.append('http://%s/ch/%s.m3u8' % (host, r['code']))
    return '\n'.join(lines) + '\n', len(rows)


def build_epg_json():
    rows = {r['code']: r for r in ensure_channels()}
    out = {}
    for code, info in ensure_epg().items():
        m = rows.get(code) or {}
        out[code] = {'name': m.get('name', ''), 'group': m.get('group', ''), **info}
    return out


def _xesc(s):
    return (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace(
        '>', '&gt;').replace('"', '&quot;')


def build_xmltv():
    rows = ensure_channels()
    epg = ensure_epg()
    today = time.strftime('%Y%m%d')
    tomorrow = time.strftime('%Y%m%d', time.localtime(time.time() + 86400))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
           '<tv generator-info-name="%s" source-info-name="%s">'
           % (_xesc(PROV.title), _xesc(PROV.name))]
    order = sorted(rows, key=lambda r: (r['group'], str(r['num']).zfill(3), r['name']))
    for r in order:
        out.append('  <channel id="%s"><display-name lang="zh">%s</display-name></channel>'
                   % (_xesc(r['code']), _xesc(r['name'])))
    for r in order:
        for p in (epg.get(r['code']) or {}).get('list') or []:
            s, e, n = p.get('s', ''), p.get('e', ''), p.get('n', '')
            if len(s) != 4 or len(e) != 4 or not n:
                continue
            st = today + s + '00 +0800'
            ed = (today if e >= s else tomorrow) + e + '00 +0800'
            out.append('  <programme start="%s" stop="%s" channel="%s">' % (st, ed, _xesc(r['code'])))
            out.append('    <title lang="zh">%s</title>' % _xesc(n))
            out.append('  </programme>')
    out.append('</tv>')
    return '\n'.join(out) + '\n'


def build_vod_m3u(host, cat=None):
    total, items = PROV.vod_items(cat=cat, offset=0, limit=0) if PROV.caps.get('vod') else (0, [])
    lines = ['#EXTM3U']
    for it in items:
        lines.append('#EXTINF:-1 tvg-name="%s" group-title="%s",%s'
                     % (it['name'], it.get('type') or '', it['name']))
        lines.append('http://%s/vod/%s/%s.m3u8' % (host, it['pid'], it['cid']))
    return '\n'.join(lines) + '\n', len(items)


# ---------------------------------------------------------------- 播放器（一律用本机已装的）

def _player_kind(exe):
    n = os.path.basename(exe or '').lower()
    if n.startswith('mpv') or 'yaoplayer' in n:
        return 'mpv'
    if n.startswith('vlc'):
        return 'vlc'
    if 'potplayer' in n:
        return 'pot'
    return 'other'


def player_path():
    """按顺序找一个本机播放器：config 写的 → PATH → 常见安装位置。
    mpv / Yaoplayer / VLC / PotPlayer 都认，找不到就返回 None（绝不内嵌网页播放）。"""
    cands = list(CFG.get('mpv') or [])
    for name in ('mpv', 'vlc'):
        w = shutil.which(name)
        if w:
            cands.append(w)
    pf = os.environ.get('ProgramFiles', '')
    pf86 = os.environ.get('ProgramFiles(x86)', '')
    cands += [
        os.path.join(pf, 'VideoLAN', 'VLC', 'vlc.exe'),
        os.path.join(pf86, 'VideoLAN', 'VLC', 'vlc.exe'),
        os.path.join(pf, 'DAUM', 'PotPlayer', 'PotPlayerMini64.exe'),
        os.path.join(pf86, 'DAUM', 'PotPlayer', 'PotPlayerMini64.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'mpv', 'mpv.exe'),
    ]
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


def mpv_path():
    return player_path()


def _mpv_pipe_cmd(obj):
    try:
        with open(CFG['mpv_pipe'], 'wb') as f:
            f.write((json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8'))
        return True
    except Exception:
        return False


_mpv_lock = threading.Lock()
_mpv = {'url': ''}


def _kill_stray():
    """只收拾本服务起的那个窗口，别误伤用户自己开的 MPV"""
    pat = '*127.0.0.1:%d/*' % PORT
    ps = ("Get-CimInstance Win32_Process | Where-Object { "
          "($_.Name -eq 'mpv.exe' -or $_.Name -eq 'Yaoplayer.exe') -and "
          "$_.CommandLine -like '%s' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
          % pat)
    try:
        subprocess.run(['C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe',
                        '-NoProfile', '-Command', ps], capture_output=True, timeout=15,
                       creationflags=0x08000000)
    except Exception:
        pass


def mpv_play(url, title=''):
    exe = player_path()
    if not exe:
        return False, ('没找到本机播放器：装一个 mpv 或 VLC（推荐 VLC，官网直装），'
                       '或把任意播放器 exe 的完整路径填进 config.json 的 mpv 列表')
    kind = _player_kind(exe)
    with _mpv_lock:
        try:
            if kind == 'mpv' and _mpv['url'] and _mpv_pipe_cmd({'command': ['loadfile', url]}):
                if title:
                    _mpv_pipe_cmd({'command': ['set_property', 'title', title]})
                _mpv['url'] = url
                return True, ''
            if kind == 'mpv':
                _kill_stray()
                args = [exe, url, '--force-window=yes', '--keep-open=yes',
                        '--input-ipc-server=' + CFG['mpv_pipe'], '--no-terminal']
                if title:
                    args.append('--title=' + title)
            else:
                # VLC / PotPlayer / 其它：直接喂地址，各开一个窗口（不强制单窗口复用）
                args = [exe, url]
            subprocess.Popen(args, close_fds=True,
                             creationflags=0x08000000 if os.name == 'nt' else 0)
            _mpv['url'] = url if kind == 'mpv' else ''
            return True, ''
        except Exception as e:
            return False, str(e)


# ---------------------------------------------------------------- HTTP


def host_of(h):
    h = (h or '').strip()
    return h or ('127.0.0.1:%d' % PORT)


class Handler(BaseHTTPRequestHandler):
    server_version = 'IPTVShell/1.0'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='text/plain; charset=utf-8', extra=None):
        data = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _json(self, obj, code=200, indent=None):
        self._send(code, json.dumps(obj, ensure_ascii=False, indent=indent),
                   'application/json; charset=utf-8')

    def do_GET(self):
        host = host_of(self.headers.get('Host'))
        path, _, qs = self.path.partition('?')
        q = urllib.parse.parse_qs(qs)
        try:
            if path in ('/', '/ui', '/index.html'):
                self._send(200, render_index(host), 'text/html; charset=utf-8')
                return

            if path == '/favicon.ico':
                self._send(200, FAVICON_PNG, 'image/png',
                           {'Cache-Control': 'public, max-age=2592000'})
                return

            if path == '/api/meta':
                self._json({'provider': PROV.name, 'title': PROV.title, 'sub': PROV.sub,
                            'caps': PROV.caps, 'desc': PROV.desc, 'port': PORT,
                            'contract': '见 智能体接入指南.md'}, indent=2)
                return

            if path == '/status':
                self._json({
                    'provider': PROV.name,
                    'channels': len(_cache['ch']),
                    'epg_age_sec': int(time.time() - _cache['epg_at']) if _cache['epg_at'] else None,
                    'cache_age_sec': int(time.time() - _cache['ch_at']) if _cache['ch_at'] else None,
                    'ttl_sec': TTL_LIVE, 'caps': PROV.caps, 'error': _cache['err'],
                    'boot': int(START_TS),
                    'boot_str': time.strftime('%m-%d %H:%M', time.localtime(START_TS)),
                }, indent=2)
                return

            if path == '/refresh':
                ensure_channels(force=True)
                ensure_epg(force=True)
                self._send(200, 'ok, %d channels' % len(_cache['ch']))
                return

            if path == '/live.m3u':
                body, n = build_live_m3u(host)
                self._send(200, body, 'application/vnd.apple.mpegurl')
                return

            if path in ('/xmltv', '/epg.xml'):
                self._send(200, build_xmltv(), 'application/xml; charset=utf-8')
                return

            if path == '/epg.json':
                self._json(build_epg_json())
                return

            if path == '/epg':
                code = (q.get('code') or [''])[0]
                info = ensure_epg().get(code) or {}
                self._json(info, indent=2)
                return

            if path == '/api/live.json':
                self._json(build_live_json())
                return

            if path == '/poster' and PROV.caps.get('poster'):
                rel = urllib.parse.unquote((q.get('u') or [''])[0])
                data = PROV.poster(rel)
                ct = 'image/jpeg'
                if isinstance(data, tuple):
                    data, ct = data
                if not data:
                    self._send(404, b'', 'image/png')
                else:
                    self._send(200, data, ct, {'Cache-Control': 'public, max-age=604800'})
                return

            if path == '/api/vod.json':
                meta = PROV.vod_meta()
                meta.setdefault('total', 0)
                self._json(meta, indent=1)
                return

            if path == '/api/vod.items.json':
                if not PROV.caps.get('vod'):
                    self._json({'total': 0, 'offset': 0, 'items': []})
                    return
                kw = {
                    'cat': urllib.parse.unquote((q.get('cat') or [''])[0]) or None,
                    'kw': (q.get('kw') or [''])[0] or None,
                    'type': urllib.parse.unquote((q.get('type') or [''])[0]) or None,
                    'offset': int((q.get('offset') or ['0'])[0] or 0),
                    'limit': int((q.get('limit') or ['300'])[0] or 0),
                    'yb': (q.get('yb') or [''])[0] or None,
                    'fk': (q.get('fk') or ['0'])[0] in ('1', 'true', 'yes'),
                    'sort': (q.get('sort') or [''])[0] or None,
                    'tag': urllib.parse.unquote((q.get('tg') or [''])[0]) or None,
                }
                total, items = PROV.vod_items(**kw)
                self._json({'total': total, 'offset': kw['offset'], 'items': items})
                return

            if path == '/api/vod.search' and PROV.caps.get('search'):
                kw = urllib.parse.unquote((q.get('kw') or [''])[0]).strip()
                limit = max(1, min(120, int((q.get('limit') or ['60'])[0] or 60)))
                total, items = PROV.vod_search(kw, limit)
                self._json({'kw': kw, 'total': total, 'items': items})
                return

            if path in ('/vod.m3u', '/vod'):
                cat = urllib.parse.unquote((q.get('cat') or [''])[0]) or None
                body, n = build_vod_m3u(host, cat)
                self._send(200, body, 'application/vnd.apple.mpegurl')
                return

            m = re.match(r'^/vod/([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)\.m3u8$', path)
            if m:
                pid, cid = m.group(1), m.group(2)
                eps = PROV.vod_episodes(pid, cid) if PROV.caps.get('episodes') else []
                if len(eps) > 1:
                    lines = ['#EXTM3U']
                    for e in eps:
                        lines.append('#EXTINF:-1,' + (e.get('name') or '第%s集' % e.get('seq', '')))
                        lines.append('http://%s/vod/ep/%s/%s.m3u8' % (host, e['pid'], e['cid']))
                    self._send(200, '\n'.join(lines) + '\n', 'application/vnd.apple.mpegurl')
                    return
                try:
                    body, n = PROV.vod_playlist(pid, cid, host)
                except Exception:
                    self._send(404, '#EXTM3U\n')
                    return
                self._send(200, body, 'application/vnd.apple.mpegurl')
                return

            m = re.match(r'^/vod/ep/([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)\.m3u8$', path)
            if m:
                try:
                    body, n = PROV.vod_playlist(m.group(1), m.group(2), host)
                except Exception:
                    self._send(404, '#EXTM3U\n')
                    return
                self._send(200, body, 'application/vnd.apple.mpegurl')
                return

            m = re.match(r'^/ch/([A-Za-z0-9_\-]+)\.m3u8$', path)
            if m:
                body = PROV.live_playlist(m.group(1), host)
                self._send(200, body, 'application/vnd.apple.mpegurl')
                return

            m = re.match(r'^/ch/([A-Za-z0-9_\-]+)/v\.m3u8$', path)
            if m and PROV.caps.get('variant'):
                idx = int((q.get('i') or ['0'])[0] or 0)
                body = PROV.live_variant(m.group(1), idx, host)
                self._send(200, body, 'application/vnd.apple.mpegurl')
                return

            if path == '/seg' and PROV.caps.get('seg'):
                u = (q.get('u') or [''])[0]
                if not u:
                    self._send(400, 'bad seg')
                    return
                st, data, hd = PROV.seg(u)
                self._send(st, data, (hd or {}).get('Content-Type', 'video/mp2t'))
                return

            if path == '/play':
                u = (q.get('u') or [''])[0]
                t = (q.get('t') or [''])[0]
                if not u.startswith('/') or u.startswith('//'):
                    self._json({'ok': False, 'err': '地址不合法'})
                    return
                ok, err = mpv_play('http://127.0.0.1:%d%s' % (PORT, u), t)
                if ok:
                    log('交给 MPV：%s %s' % (t or u, u))
                self._json({'ok': ok, 'err': err})
                return

            self._send(404, 'unknown path: %s' % path)

        except KeyError as e:
            self._send(404, 'not found: %s' % e)
        except Exception as e:
            log('请求失败 %s -> %s' % (path, e))
            self._send(502, 'provider error: %s' % e)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('223.5.5.5', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def main():
    import argparse
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--provider', default=None)
    a = ap.parse_args()
    PORT = a.port
    if a.provider and a.provider != PROV.name:
        globals()['PROV'] = Provider(a.provider)

    os.makedirs(CACHE_DIR, exist_ok=True)
    ensure_channels(force=True)
    if PROV.caps.get('epg'):
        ensure_epg(force=True)
    threading.Thread(target=_background, daemon=True).start()

    srv = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    log('外壳已启动：线路=%s  端口=%d  频道=%d  点播=%s'
        % (PROV.name, PORT, len(_cache['ch']), '有' if PROV.caps.get('vod') else '无'))
    print('本机  http://127.0.0.1:%d/' % PORT)
    print('局域网 http://%s:%d/' % (lan_ip(), PORT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
