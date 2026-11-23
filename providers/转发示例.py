#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转发示例：把已经在跑的外部服务当数据源，外壳只做转发，不重复抓源。

用法：config.json 里写
  "provider": "转发示例",
  "svc_py": "D:\\某个\\直播数据服务.py"     # 那个服务必须提供 channels()/epg() 之类函数
也可以设环境变量 IPTV_SVC_PY 指过去。

要点：
  1. 用 importlib 按绝对路径加载外部模块，不复制它的代码；
  2. 外部服务起在自己端口上时，也可以直接 HTTP 打它的 /api/live.json，这里给的是函数调用式；
  3. 缓存、清单、节目单、播放全部由外壳负责。
"""
import importlib.util
import os

NAME = '转发示例'
CAPS = {}          # 不写就按「有没有实现对应函数」自动推断


def _cfg_paths():
    """和 m3u 一样：exe 同级 → IPTV_CONFIG → IPTV_BASE → 包内那份"""
    import sys
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = []
    if getattr(sys, 'frozen', False):
        out.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'config.json'))
    for k in ('IPTV_CONFIG', 'IPTV_BASE'):
        v = os.environ.get(k)
        if v:
            out.append(v if v.lower().endswith('.json') else os.path.join(v, 'config.json'))
    out.append(os.path.join(here, 'config.json'))
    return out


def _load_cfg():
    import json
    for p in _cfg_paths():
        try:
            with open(p, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def _svc_path():
    p = os.environ.get('IPTV_SVC_PY', '')
    if not p:
        p = _load_cfg().get('svc_py') or ''
    return os.path.expandvars(p)


_CACHE = {}


def _svc():
    p = _svc_path()
    if not p:
        raise RuntimeError('config.json 里没填 svc_py（外部服务的 py 路径）')
    if p in _CACHE:
        return _CACHE[p]
    if not os.path.isfile(p):
        raise RuntimeError('找不到外部服务的 py：%s' % p)
    spec = importlib.util.spec_from_file_location('iptv_external_svc', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CACHE[p] = mod
    return mod


def channels():
    """把外部服务的频道表翻译成外壳要的形状。

    认三种写法（有哪个用哪个，别让用户为了一个字段去改外部服务）：
      1. 服务自己就 export 了 channels()
      2. ensure_fresh() 给地址表 + load_meta() 给名字/分组（某运营商线路就是这样）
      3. rows() 之类返回 [{code,name,group,num}]
    """
    s = _svc()
    rows = getattr(s, 'ensure_fresh', None)
    if callable(rows):
        try:
            codes = rows()
            meta = s.load_meta() if callable(getattr(s, 'load_meta', None)) else {}
            out = []
            for code in codes:
                m = (meta or {}).get(code, {}) if isinstance(meta, dict) else {}
                out.append({'code': code,
                            'name': m.get('name') or code,
                            'group': m.get('group') or '',
                            'num': m.get('num') or ''})
            if out:
                return out
        except Exception:
            pass
    f = getattr(s, 'channels', None)
    if callable(f):
        try:
            got = f()
            if got:
                return got
        except Exception:
            pass
    for fn in ('rows', 'list_channels', 'get_channels'):
        g = getattr(s, fn, None)
        if callable(g):
            try:
                return g()
            except Exception:
                pass
    return []
    for fn in ('rows', 'list_channels', 'get_channels'):
        f = getattr(s, fn, None)
        if callable(f):
            try:
                return f()
            except Exception:
                pass
    return []


def epg(code=None):
    s = _svc()
    f = getattr(s, 'epg', None)
    if not callable(f):
        f = getattr(s, 'ensure_epg', None)
    if callable(f):
        try:
            return f(code) if code else f()
        except TypeError:
            return f()
    return {}


def live_playlist(code, host):
    """单台清单：外部服务一般叫 build_index(code, host)"""
    s = _svc()
    f = getattr(s, 'build_index', None) or getattr(s, 'live_playlist', None)
    if not callable(f):
        raise RuntimeError('外部服务没有 build_index(code, host)，播不了直播')
    r = f(code, host)
    return r[0] if isinstance(r, tuple) else r


def live_variant(code, idx, host):
    s = _svc()
    f = getattr(s, 'build_variant', None)
    if callable(f):
        return f(code, idx, host)
    raise RuntimeError('外部服务没有 build_variant，播不了分片清单')


def seg(u):
    s = _svc()
    up = getattr(s, 'upstream', None)
    dec = getattr(s, 'dec', None)
    if callable(up):
        return up(dec(u) if callable(dec) else u, timeout=30)
    raise RuntimeError('外部服务没有 upstream，播不了分片')


def vod_items(cat=None, kw=None, offset=0, limit=0, type=None, yb=None, fk=False,
              sort=None, tag=None):
    """点播列表：外部服务的参数名可能是 typ / tg，不是 type / tag，两种都试"""
    s = _svc()
    f = getattr(s, 'build_vod_items', None)
    if not callable(f):
        return (0, [])
    common = dict(cat=cat, kw=kw, offset=offset, limit=limit, yb=yb, fk=bool(fk), sort=sort)
    try:
        return f(type=type, tag=tag, **common)
    except TypeError:
        return f(typ=type, tg=tag, **common)


_META = None


def _meta():
    """左栏分类算法的唯一一份实现（providers/_meta.py）"""
    global _META
    if _META is None:
        import importlib.util as _iu
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_meta.py')
        spec = _iu.spec_from_file_location('iptv_vod_meta', p)
        m = _iu.module_from_spec(spec)
        spec.loader.exec_module(m)
        _META = m
    return _META


def _bands_of(items):
    return _meta().build_meta(items)


def vod_meta():
    s = _svc()
    f = getattr(s, 'vod_meta', None)
    if callable(f):
        try:
            m = f()
            if m and (m.get('types') or m.get('bands')):
                return m
        except Exception:
            pass
    items = []
    g = getattr(s, 'ensure_vod', None)
    if callable(g):
        try:
            items = g(nonblock=True) or []
        except TypeError:
            items = g() or []
        if not items:
            try:
                items = g(nonblock=False) or items
            except TypeError:
                pass
            except Exception:
                items = []
    cats = {}
    for it in items:
        c = it.get('cat') or '未分类'
        cats.setdefault(c, {})
        cats[c][it.get('sub', '')] = cats[c].get(it.get('sub', ''), 0) + 1
    types, bands, tag_list = _bands_of(items)
    return {'total': len(items), 'groups': {k: sum(v.values()) for k, v in cats.items()},
            'types': types, 'bands': bands, 'tags': tag_list, 'subs': cats,
            'sample': items[:5], 'building': False}


def vod_search(kw, limit=60):
    s = _svc()
    f = getattr(s, 'build_vod_search', None)
    return f(kw, limit) if callable(f) else []


def vod_episodes(pid, cid):
    """剧集列表：成剧（programSub 多集）整部 id 直接签 = `144020013 media is not exist`，
    必须拿每集自己的 programId / contentID 逐集签。外部服务一般叫 vod_episodes(pid, cid)。
    不回这个函数，外壳就把“剧集能力”判成没有，点电视剧一律 404。"""
    s = _svc()
    f = getattr(s, 'vod_episodes', None)
    if not callable(f):
        return []
    out = []
    for e in (f(str(pid), str(cid)) or []):
        if not (e.get('pid') and e.get('cid')):
            continue
        out.append({'pid': str(e['pid']), 'cid': str(e['cid']),
                    'name': (e.get('name') or '').strip(), 'seq': e.get('seq')})
    return out


def vod_playlist(pid, cid, host):
    s = _svc()
    f = getattr(s, 'build_vod_playlist', None)
    if not callable(f):
        raise RuntimeError('外部服务没有 build_vod_playlist')
    return f(pid, cid, host)


def poster(rel):
    """点播海报：交给外部服务按需抓取并缓存，返回 (二进制, 类型)"""
    s = _svc()
    f = getattr(s, 'poster_bytes', None)
    if not callable(f):
        return None, None
    try:
        return f(rel)
    except Exception:
        return None, None


def status_extra():
    return {'svc': _svc_path()}


# ------------------------------------------------ 播放可用性检查（可选函数）
# 2026-11-23 实测：2026 年上新的部分片子走「版权保护 / 私有编码」通道，
#   · 有的清单里直接带 #EXT-X-KEY:METHOD=AES-128,KEYFORMAT="copyrightDRM"（优酷 DRM）
#   · 有的没有任何 KEY 标记，但视频 SPS 不是标准 H.264（profile_idc 非法），播放器全解不了
# 共同表现：黑屏 → 跑到头自动跳下一集 → 进度条拖不动。外壳必须提前判出来并如实提示，不许起黑屏。
_DRM = None
_SPS_OK = {66, 77, 88, 100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139}
_AVS_TYPES = (0x42, 0x43)


def _head(url, n=96 * 1024):
    """只取前 96 KB（想看的 SPS/PPS/PMT 都在开头），别把一整集拉下来"""
    import urllib.request
    req = urllib.request.Request(url, headers={'Range': 'bytes=0-%d' % (n - 1),
                                               'User-Agent': 'IPTVShell/check'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(n)


def _ts_probe(ts):
    """从 TS 头里读视频 stream_type 和 SPS 的 profile_idc：(type, profile)

    只看开头几十个包就够了：PAT → PMT → 视频 PES 里的 SPS。解不出来就回 (None, None)，
    调用方按「判不出来就算能播」处理，不能因为解析失败误杀正常片子。
    """
    pkt = 188
    pmt_pid, stype, vid_pid, sps = None, None, None, None
    payload = bytearray()
    for i in range(len(ts) // pkt):
        b = ts[i * pkt:(i + 1) * pkt]
        if len(b) < pkt or b[0] != 0x47:
            continue
        pid = ((b[1] & 0x1f) << 8) | b[2]
        pusi = bool(b[1] & 0x40)
        afc = (b[3] >> 4) & 0x3
        off = 4
        if afc in (2, 3):
            off += 1 + b[4]
        if off >= pkt:
            continue
        data = b[off:]
        if not pusi:
            if vid_pid is not None and pid == vid_pid:
                payload += data
            continue
        # PES（视频包）以 00 00 01 开头；PSI（PAT/PMT）前面是一个 pointer_field（通常是 0）
        if data[:3] == b'\x00\x00\x01':
            if vid_pid is not None and pid == vid_pid and len(data) > 9:
                hl = data[8]
                if 9 + hl < pkt:
                    payload += data[9 + hl:]
        elif data[:1] == b'\x00' and len(data) > 1:
            ptr = data[0]
            sec = data[1 + ptr:]
            if not sec:
                continue
            tid = sec[0]
            if pid == 0 and tid == 0x00 and len(sec) >= 8:
                seclen = ((sec[1] & 0x0f) << 8) | sec[2]
                body = sec[8:max(8, 3 + seclen - 4)]
                for j in range(0, len(body) - 3, 4):
                    pnum = (body[j] << 8) | body[j + 1]
                    if pnum:
                        pmt_pid = ((body[j + 2] & 0x1f) << 8) | body[j + 3]
            elif pmt_pid is not None and pid == pmt_pid and tid == 0x02 and len(sec) >= 12:
                seclen = ((sec[1] & 0x0f) << 8) | sec[2]
                gil = ((sec[10] & 0x0f) << 8) | sec[11]
                j, end = 12 + gil, min(len(sec), 3 + seclen - 4)
                while j + 5 <= end:
                    t = sec[j]
                    ep = ((sec[j + 1] & 0x1f) << 8) | sec[j + 2]
                    if stype is None and t not in (0x0f, 0x03, 0x04, 0x81, 0x86, 0x87):
                        stype, vid_pid = t, ep
                    j += 5 + (((sec[j + 3] & 0x0f) << 8) | sec[j + 4])
    if payload:
        buf = bytes(payload)
        k = buf.find(b'\x00\x00\x01\x67')
        if k >= 0 and k + 4 < len(buf):
            sps = buf[k + 4]   # k+3 是 NAL 头（0x67），k+4 才是 profile_idc
    return stype, sps


def vod_check(pid, cid):
    """返回 {'ok':bool,'code':'drm'|'codec','msg':...}；判不出来一律当能放，不误杀正常片子"""
    import re
    s = _svc()
    raw = getattr(s, 'vod_playlist_raw', None)
    if not callable(raw):
        return None
    # 成剧必须拿第 1 集去签：整部的 contentID 上游直接回 144020013（media is not exist）
    ep_pid, ep_cid = str(pid), str(cid)
    fn_eps = globals().get('vod_episodes')
    if callable(fn_eps):
        try:
            eps = fn_eps(str(pid), str(cid)) or []
            if eps:
                ep_pid, ep_cid = str(eps[0]['pid']), str(eps[0]['cid'])
        except Exception:
            pass
    ent = {}
    for p_, c_ in ((ep_pid, ep_cid), (str(pid), str(cid))):
        try:
            ent = raw(p_, c_) or {}
            break
        except Exception:
            ent = {}
    text = ent.get('text') or ''
    if re.search(r'#EXT-X-KEY[^\r\n]*METHOD=(?!NONE)', text, re.I) \
            or 'KEYFORMAT="copyrightDRM"' in text:
        return {'ok': False, 'code': 'drm',
                'msg': '这部片是版权方加密的（DRM 保护），本机播放器解不了；'
                       '不是外壳坏了，官方机顶盒里才放得出来。'}
    segs = ent.get('segs') or []
    if not segs:
        return None
    u = segs[0]
    if not u.startswith('http'):
        u = (ent.get('base') or '') + '/' + u
    try:
        head = _head(u)
    except Exception:
        return None
    stype, prof = _ts_probe(head)
    if stype in _AVS_TYPES:
        return {'ok': False, 'code': 'codec',
                'msg': '这集的视频用的是 AVS 编码（国产标准），本机 mpv / VLC 解不了。'}
    if stype == 0x1b and prof is not None and prof not in _SPS_OK:
        return {'ok': False, 'code': 'codec',
                'msg': '这集的视频码流不是标准 H.264（源站私有编码 / 加密），'
                       '本机播放器解不了，不是外壳的问题。'}
    return {'ok': True, 'code': '', 'msg': ''}

