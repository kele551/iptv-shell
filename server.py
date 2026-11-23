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
import glob
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _resolve_base():
    """资源根：web/ providers/ data/ tools/ 所在目录。

    免安装 exe（PyInstaller onefile）运行时，先找 exe 同级目录（要有 web/ 与
    providers/ 才算完整），这样用户可以把线路文件放在 exe 旁边直接换；
    同级不完整才退回包内解压出来的 _MEIPASS。源码态就是本文件所在目录。
    """
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


def _resolve_config(base):
    """配置：exe 同级放 config.json 就优先用它（换线路 / 换端口不用重新打包）"""
    if getattr(sys, 'frozen', False):
        side = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'config.json')
        if os.path.isfile(side):
            return side
    env = os.environ.get('IPTV_CONFIG')
    if env and os.path.isfile(env):
        return os.path.abspath(env)
    return os.path.join(base, 'config.json')


BASE = _resolve_base()
CONFIG_PATH = _resolve_config(BASE)
WEB_DIR = os.path.join(BASE, 'web')

START_TS = time.time()

# 站内小图标（32x32），省得浏览器每次白拿一个 404
FAVICON_PNG = base64.b64decode(
    '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'
    '8H58iFWO5/8Dp3Au5Ale2wEAAAAASUVORK5CYII='
)

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

# 签址凭证失效时给用户复制给智能体的通用操作稿（线路适配器可用 token_help() 覆盖成自己那套）
TOKEN_HELP_DEFAULT = """这条线路的签址凭证（token）失效了，接口返回了非 0。请按下面做，把凭证换新：

1) 先确认不是误报：重新触发一次签址（或重启本外壳服务）看是否还是非 0；
   如果只是公网 IP 变了、或服务没起，先修那两样，不要动机顶盒。
2) 真失效才动机顶盒：把它断电 30 秒再插回去，等它进到直播画面（约 1~2 分钟）。别用遥控器重启。
3) 重新抓一次包：电脑要和机顶盒同一网段；装 Npcap（图形安装，勾 winpcap_mode=yes），
   用 scapy 对机顶盒做“单向 ARP 毒化”（只毒机顶盒→网关这一条），流量走电脑用户态转发，
   不要改系统路由/注册表，不要两端都毒。抓 150 秒，期间在机顶盒上换一次台、点一次直播。
4) 从抓到的 getChannelPlayUrl 请求里取凭证字段（stbNo / userToken / userID / encryptedUserToken，
   以及 areaNode），写进项目自己的凭证文件（不要写进代码，不要贴聊天、日志、git）。
5) 换完自证：重新签址返回成功、状态接口无报错、抽普通台和 4K 台各拉一次首分片拿到 206。

红线：不用组播那条路；不把服务映射到公网、不异地转发、不共享账号。
"""


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
            'catchup': hasattr(mod, 'catchup'),
            'epg_day': hasattr(mod, 'epg_day'),
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

    # --- 签址凭证（token）健康
    def token_error(self):
        """线路适配器可选：签址凭证失效时返回一句人话错误，正常返回空串"""
        fn = getattr(self.mod, 'token_error', None)
        if not callable(fn):
            return ''
        try:
            return str(fn() or '').strip()
        except Exception:
            return ''

    def token_help(self):
        """线路适配器可选：返回「怎么换 token」的完整操作稿（给用户复制给智能体用）"""
        fn = getattr(self.mod, 'token_help', None)
        if callable(fn):
            try:
                txt = str(fn() or '').strip()
                if txt:
                    return txt
            except Exception:
                pass
        return TOKEN_HELP_DEFAULT

    def poster(self, url):
        fn = getattr(self.mod, 'poster', None)
        return fn(url) if fn else None

    # --- 回看 / 时移（可选能力：适配器实现了才有）
    def epg_day(self, day=1):
        fn = getattr(self.mod, 'epg_day', None)
        if fn is None:
            return {'day': day, 'channels': []}
        return fn(day)

    def catchup(self, code, start, end):
        fn = getattr(self.mod, 'catchup', None)
        if fn is None:
            raise RuntimeError('这条线路没实现回看')
        return fn(code, start, end)


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
    # 分组顺序：央视类永远排最前，其次省级/卫视，最后按台数从多到少
    def _rank(name):
        n = (name or '').upper()
        if '央视' in n or 'CCTV' in n or '中央' in n:
            return 0
        if '省级' in n or '卫视' in n or '省' in n:
            return 1
        return 2
    out.sort(key=lambda x: (_rank(x['name']), -x['count']))
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
    # Yaoplayer / mpv-Atmos 都是 MPV 套壳，实测吃 --input-ipc-server / --force-window /
    # --keep-open，所以一起当 mpv 处理：同一个窗口靠管道换台，绝不连开两个。
    if n.startswith('mpv') or n.startswith('yaoplayer'):
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
_mpv = {'url': '', 'pid': 0, 'exe': ''}


def _ours_alive():
    """本服务起的那个播放器还活着吗（只认自己记下的 PID，不碰用户自己开的）"""
    pid = _mpv.get('pid')
    if not pid:
        return False
    try:
        r = subprocess.run(['C:\\Windows\\System32\\tasklist.exe', '/FI', 'PID eq %d' % pid,
                            '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=10,
                           creationflags=0x08000000)
        return ('"%d"' % pid) in (r.stdout or '')
    except Exception:
        return False


def _kill_ours():
    pid = _mpv.get('pid')
    if pid:
        try:
            subprocess.run(['C:\\Windows\\System32\\taskkill.exe', '/PID', str(pid), '/T', '/F'],
                           capture_output=True, timeout=10, creationflags=0x08000000)
        except Exception:
            pass
    _mpv['pid'] = 0
    _mpv['url'] = ''


def _spawn(exe, args):
    p = subprocess.Popen(args, close_fds=True,
                         creationflags=0x08000000 if os.name == 'nt' else 0)
    _mpv['pid'] = p.pid
    _mpv['exe'] = exe
    return p


def _title_playlist(url, title):
    """非 mpv 的播放器（Yaoplayer / VLC / PotPlayer…）不给 mpv 那套参数，
    改成写一个小 m3u 清单喂给它：窗口标题直接就是台名，不再是频道编号。
    只保留最近一个，别在缓存目录里堆垃圾。"""
    name = re.sub(r'[\\/:*?"<>|\s]+', '_', (title or '播放')).strip('_')[:40] or '播放'
    d = os.path.expandvars(CFG.get('cache_dir') or os.path.join(
        os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'IPTV外壳'))
    try:
        os.makedirs(d, exist_ok=True)
        for old in glob.glob(os.path.join(d, '正在播放-*.m3u')):
            try:
                os.remove(old)
            except Exception:
                pass
        p = os.path.join(d, '正在播放-%s.m3u' % name)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('#EXTM3U\n#EXTINF:-1,%s\n%s\n' % (title or name, url))
        return p
    except Exception:
        return None


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
            # 不管哪种播放器，都只留一个窗口：能复用的复用，不能复用的先关自己那个
            src = _title_playlist(url, title) if title else url
            if kind == 'mpv':
                if _ours_alive() and _mpv_pipe_cmd({'command': ['loadfile', src or url]}):
                    if title:
                        _mpv_pipe_cmd({'command': ['set_property', 'title', title]})
                    _mpv['url'] = url
                    return True, ''
                _kill_ours()
                args = [src or url, '--force-window=yes', '--keep-open=yes',
                        '--input-ipc-server=' + CFG['mpv_pipe'], '--no-terminal']
                if title:
                    args.append('--title=' + title)
                if os.environ.get('IPTV_MUTE') == '1':
                    # 快检 / 演练专用：窗口照起（能证明画面出来），但一点声音都不出
                    args.append('--mute=yes')
                p = _spawn(exe, [exe] + args)
                time.sleep(1.3)
                if p.poll() is not None:
                    # 这版播放器不吃那套参数：退回「只喂清单」，照样一个窗口
                    _spawn(exe, [exe, src or url])
            else:
                # VLC / PotPlayer 等：先关掉自己上次开的，再开新的，永远只一个
                _kill_ours()
                _kill_stray()
                _spawn(exe, [exe, src or url])
            _mpv['url'] = url if kind == 'mpv' else ''
            return True, ''
        except Exception as e:
            return False, str(e)


# --------------------------------------------------------- 播放可用性检查（DRM / 非标编码）
# 2026-11-23 用户报「这两部电视剧不显示画面、会跳集、快进不了」：实测是片源带版权加密
# （#EXT-X-KEY:METHOD=AES-128 / KEYFORMAT="copyrightDRM"）或源站用了非标准 H.264（SPS 非法）。
# 播放器拿到放不了，会黑屏跑到头再自动跳下一集，看着就像「跳集 + 快进不了」。
# 这里在交给播放器之前先判一次：放不了就直接回原因，绝不起黑屏窗口。
_DRM_KEY = re.compile(r'#EXT-X-KEY[^\r\n]*METHOD=(?!NONE)', re.I)
_bad_cache = {}
_bad_lock = threading.Lock()
BAD_TTL = 900


def _plain_seconds(text):
    """算第一个加密段之前有多少秒明文：DRM 片常常开头几段是明文，
    播到加密段才黑屏。提示里带这个秒数，才不会把「开头能放」的片子说成「整部放不了」。"""
    plain, enc, last = 0.0, False, 0.0
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-KEY'):
            m = re.search(r'METHOD=([A-Za-z0-9\-]+)', line, re.I)
            if m and (m.group(1) or '').upper() != 'NONE':
                enc = True
            continue
        if line.startswith('#EXTINF'):
            m = re.search(r'#EXTINF:([0-9.]+)', line)
            last = float(m.group(1)) if m else 0.0
            continue
        if line.startswith('#'):
            continue
        if not enc:
            plain += last
    return plain


def vod_check(pid, cid):
    key = (str(pid), str(cid))
    with _bad_lock:
        ent = _bad_cache.get(key)
        if ent and time.time() - ent['at'] < BAD_TTL:
            return ent['res']
    res = None
    # 注意：要问线路适配器模块本身（PROV.mod），外壳的 Provider 包装类只转发固定几个函数
    f = getattr(getattr(PROV, 'mod', None), 'vod_check', None)
    if callable(f):
        try:
            got = f(str(pid), str(cid))
            if isinstance(got, dict):
                res = got
        except Exception as e:
            log('播放检查失败 %s/%s: %s' % (pid, cid, e))
    if res is None:
        try:
            body, _n = PROV.vod_playlist(str(pid), str(cid), '127.0.0.1')
            if _DRM_KEY.search(body or '') or 'KEYFORMAT="copyrightDRM"' in (body or ''):
                plain = _plain_seconds(body)
                if plain >= 5:
                    msg = ('只有开头约 %d 秒能放，之后是版权方加密（DRM），播放器到那里就会黑屏、跳集；'
                           '想看全片请到官方 App / 机顶盒。' % int(plain))
                else:
                    msg = '这部片的源站带版权加密（DRM），本机播放器解不了，不是外壳的问题。'
                res = {'ok': False, 'code': 'drm', 'msg': msg}
        except Exception:
            res = None
    if res is None:
        res = {'ok': True, 'code': '', 'msg': ''}
    with _bad_lock:
        _bad_cache[key] = {'at': time.time(), 'res': res}
        while len(_bad_cache) > 300:
            _bad_cache.pop(next(iter(_bad_cache)))
    return res


def play_target(u):
    """/play 的地址如果是点播，算出要检查的那一集（成剧先看第 1 集）"""
    m = re.match(r'^/vod/ep/([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)\.m3u8$', u)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'^/vod/([A-Za-z0-9_\-]+)/([A-Za-z0-9_\-]+)\.m3u8$', u)
    if not m:
        return None
    pid, cid = m.group(1), m.group(2)
    if PROV.caps.get('episodes'):
        try:
            eps = PROV.vod_episodes(pid, cid) or []
        except Exception:
            eps = []
        if eps:
            return str(eps[0]['pid']), str(eps[0]['cid'])
    return pid, cid


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
                    'token_error': PROV.token_error(),
                    'boot': int(START_TS),
                    'boot_str': time.strftime('%m-%d %H:%M', time.localtime(START_TS)),
                }, indent=2)
                return

            if path == '/refresh':
                ensure_channels(force=True)
                ensure_epg(force=True)
                self._send(200, 'ok, %d channels' % len(_cache['ch']))
                return

            if path == '/api/token.help.txt':
                # 页面用它把「怎么换 token」整段给用户复制；带上报错原文，智能体一看就知道是哪条线
                err = PROV.token_error()
                head = ('线路：%s\n报错：%s\n\n' % (PROV.name, err)) if err else ('线路：%s\n\n' % PROV.name)
                self._send(200, head + PROV.token_help())
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

            if path == '/api/epg.day.json':
                if not PROV.caps.get('epg_day'):
                    self._json({'day': 0, 'channels': [], 'caps': PROV.caps})
                    return
                day = (q.get('day') or ['1'])[0]
                self._json(PROV.epg_day(day))
                return

            m = re.match(r'^/catchup/([A-Za-z0-9_\-]+)\.m3u8$', path)
            if m:
                start = (q.get('start') or ['0'])[0]
                end = (q.get('end') or ['0'])[0]
                try:
                    body = PROV.catchup(m.group(1), int(float(start)), int(float(end)))
                    self._send(200, body, 'application/vnd.apple.mpegurl')
                except Exception as e:
                    log('回看失败 %s：%s' % (m.group(1), e))
                    self._send(404, '#EXTM3U\n# 回看失败：%s\n' % e)
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
                if eps:
                    # 只要这条线路认得出“集”，就给集清单（单集也照给，别再去签整部 id）
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

            if path == '/api/vod.check.json':
                pid = (q.get('pid') or [''])[0]
                cid = (q.get('cid') or [''])[0]
                if not pid or not cid:
                    self._json({'ok': False, 'err': '缺 pid / cid'})
                    return
                self._json(vod_check(pid, cid))
                return

            if path == '/play':
                u = (q.get('u') or [''])[0]
                t = (q.get('t') or [''])[0]
                if not u.startswith('/') or u.startswith('//'):
                    self._json({'ok': False, 'err': '地址不合法'})
                    return
                tgt = play_target(u)
                # 2026-11-23 用户定：不许没验证就给片源下“放不了”的结论。
                # 只记日志，不拦播放；真放不出来由播放器自己表现，外壳不预判。
                if tgt:
                    chk = vod_check(*tgt)
                    if not chk.get('ok'):
                        log('播放前检查提示（不拦）：%s %s' % (t or u, chk.get('msg')))
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
            # /api/ 一律回 JSON，别回一段纯文本让人解析崩掉（快检和网页都靠它）
            if path.startswith('/api/'):
                self._json({'error': 'provider error: %s' % e, 'items': [], 'total': 0}, 502)
            else:
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
