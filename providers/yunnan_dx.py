# -*- coding: utf-8 -*-
"""云南电信（曲靖天邑盒）示例适配器：直接把已经跑通的「直播数据服务.py」当数据源。

它证明模板能接「需要自己签名、自己建片库」的线路：适配器只管翻译，
缓存、签名、片库重建仍由原来那套逻辑负责。

config.json 里可选：
  "yunnan_svc": "F:\\Harness\\projects\\云南IPTV\\tools\\直播数据服务.py"
不写就按这个默认路径找；也可以用环境变量 IPTV_YUNNAN_SVC 指定。
"""
import importlib.util
import json
import os

NAME = '云南电信'
DESC = '曲靖天邑盒线路：门户签名 + 点播片库，复用已跑通的服务模块'
CAPS = {'live': True, 'epg': True, 'vod': True, 'search': True, 'poster': True,
        'episodes': True, 'variant': True, 'seg': True}

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT = r'F:\Harness\projects\云南IPTV\tools\直播数据服务.py'


def _svc_path():
    try:
        with open(os.path.join(_HERE, 'config.json'), encoding='utf-8-sig') as f:
            cfg = json.load(f)
        p = (cfg.get('yunnan_svc') or '').strip()
    except Exception:
        p = ''
    return p or os.environ.get('IPTV_YUNNAN_SVC') or _DEFAULT


_svc = None


def svc():
    global _svc
    if _svc is None:
        p = _svc_path()
        if not os.path.isfile(p):
            raise RuntimeError('找不到云南线路的服务模块：%s' % p)
        spec = importlib.util.spec_from_file_location('yunnan_svc', p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _svc = mod
    return _svc


def channels():
    s = svc()
    rows = s.ensure_fresh()
    meta = s.load_meta()
    out = []
    for code in meta:
        if code not in rows:
            continue
        m = meta[code]
        out.append({'code': code, 'name': m.get('name', ''),
                    'group': m.get('group', ''), 'num': m.get('num', '')})
    return out


def epg():
    return svc().ensure_epg()


def live_playlist(code, host):
    body, _n = svc().build_index(code, host)
    return body


def live_variant(code, idx, host):
    return svc().build_variant(code, idx, host)


def seg(u):
    s = svc()
    return s.upstream(s.dec(u), timeout=30)


def vod_items(cat=None, kw=None, offset=0, limit=0, type=None, yb=None, fk=False,
              sort=None, tag=None):
    s = svc()
    return s.build_vod_items(cat, kw, offset, limit, type, yb, fk, sort, tag)


def vod_search(kw, limit=60):
    return svc().build_vod_search(kw, limit)


def vod_meta():
    s = svc()
    items = s.ensure_vod(nonblock=True)
    cats, types = {}, {}
    for it in items:
        cats.setdefault(it['cat'], {})
        cats[it['cat']][it['sub']] = cats[it['cat']].get(it['sub'], 0) + 1
        t = it.get('type') or '电影'
        types[t] = types.get(t, 0) + 1
    import time
    return {'total': len(items), 'building': not items, 'build_at': int(s._vod['at']),
            'age_sec': int(time.time() - s._vod['at']) if s._vod['at'] else None,
            'ttl_sec': s.VOD_TTL, 'error': s._vod['err'],
            'groups': {k: sum(v.values()) for k, v in cats.items()},
            'types': types, 'bands': s.vod_band_stats(items),
            'tags': s.vod_tag_stats(items), 'subs': cats, 'sample': items[:5]}


def vod_episodes(pid, cid):
    return svc().vod_episodes(pid, cid)


def vod_playlist(pid, cid, host):
    return svc().build_vod_playlist(pid, cid, host)


def poster(u):
    return svc().poster_bytes(u)
