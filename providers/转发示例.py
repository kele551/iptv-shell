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
      2. ensure_fresh() 给地址表 + load_meta() 给名字/分组（曲靖那套就是这个）
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
