# -*- coding: utf-8 -*-
"""万能线路适配器：只要手上有「m3u/m3u8 清单 + 可选 xmltv 节目单」就能用。

config.json 里填：
  "provider": "m3u",
  "m3u_path":  "F:\某个\播放清单.m3u",       # 必填
  "xmltv_path":"F:\某个\节目单.xml"          # 可不填

清单里认这些写法（都算可选，没写的自动降级）：
  #EXTINF:-1 tvg-id="cctv1" tvg-name="CCTV-1" group-title="央视",CCTV-1 综合
  http://.../index.m3u8
没有 tvg-id 就用频道名当 id，没有 group-title 归到「未分组」。
"""
import os
import re

NAME = 'm3u 清单'
DESC = '直接吃现成的 m3u/xmltv 文件，任何线路都能接'
CAPS = {'live': True, 'vod': True, 'search': True, 'poster': False,
        'episodes': False, 'variant': False, 'seg': False}

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg():
    import json
    p = os.path.join(_HERE, 'config.json')
    try:
        with open(p, encoding='utf-8-sig', errors='ignore') as f:
            return json.load(f)
    except Exception:
        return {}


def _paths():
    cfg = _cfg()
    m3u = (cfg.get('m3u_path') or os.environ.get('IPTV_M3U') or '').strip()
    xml = (cfg.get('xmltv_path') or os.environ.get('IPTV_XMLTV') or '').strip()
    return m3u, xml


_EXTINF = re.compile(r'^#EXTINF:(-?\d+)(.*),(.*)$')


def _parse():
    m3u, _ = _paths()
    if not m3u or not os.path.isfile(m3u):
        raise RuntimeError('没配置 m3u_path，或文件不存在：%s' % m3u)
    rows, cur = [], None
    with open(m3u, encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#EXTINF'):
                m = _EXTINF.match(line)
                attrs, title = (m.group(2) if m else ''), (m.group(3) if m else '')
                g = re.search(r'group-title="([^"]*)"', attrs)
                i = re.search(r'tvg-id="([^"]*)"', attrs)
                n = re.search(r'tvg-name="([^"]*)"', attrs)
                name = (n.group(1) if n else '').strip() or title.strip()
                cur = {'code': (i.group(1) if i else '').strip() or name,
                       'name': name, 'group': (g.group(1) if g else '').strip() or '未分组',
                       'num': ''}
            elif line.startswith('#'):
                continue
            elif cur is not None:
                cur['url'] = line
                rows.append(cur)
                cur = None
    seen, out = set(), []
    for r in rows:
        if r['code'] in seen:
            continue
        seen.add(r['code'])
        out.append(r)
    return out


def channels():
    rows = _parse()
    for k, r in enumerate(rows, 1):
        r['num'] = '%03d' % k
        # 频道码要塞进 URL，字符之外的一律替换掉
        r['code'] = re.sub(r'[^A-Za-z0-9_\-.]', '_', r['code'])[:64] or ('ch%d' % k)
    return rows


def epg():
    _, xml = _paths()
    if not xml or not os.path.isfile(xml):
        return {}
    import xml.etree.ElementTree as ET
    root = ET.parse(xml).getroot()
    out = {}
    for ch in root.findall('channel'):
        cid = ch.get('id') or ''
        for prog in root.findall('programme'):
            if prog.get('channel') != cid:
                continue
            st = (prog.get('start') or '')[:14]
            sp = (prog.get('stop') or '')[:14]
            if len(st) < 12 or len(sp) < 12:
                continue
            t = prog.findtext('title') or ''
            out.setdefault(cid, {'list': []})['list'].append(
                {'s': st[8:12], 'e': sp[8:12], 'n': t})
        lst = (out.get(cid) or {}).get('list') or []
        lst.sort(key=lambda x: x['s'])
        if lst:
            import time
            hhmm = time.strftime('%H%M')
            cur = next((p for p in lst if p['s'] <= hhmm < p['e']), None)
            idx = lst.index(cur) if cur else 0
            out[cid].update({
                'cur': cur['n'] if cur else '', 'cur_start': cur['s'] if cur else '',
                'cur_end': cur['e'] if cur else '',
                'next': lst[idx + 1]['n'] if idx + 1 < len(lst) else '',
                'next_start': lst[idx + 1]['s'] if idx + 1 < len(lst) else '',
            })
    return out


def live_playlist(code, host):
    for r in channels():
        if r['code'] == code:
            return '#EXTM3U\n#EXTINF:-1,%s\n%s\n' % (r['name'], r['url'])
    raise KeyError(code)


# ---------------------------------------------------------------- 点播（可选）
# 想让这条线路也有「点播」，就在 config.json 再填一份点播清单：
#   "vod_m3u": "F:\\某个\\点播清单.m3u"
# 每一条 = 一部片（#EXTINF 的标题当片名，group-title 当分类），地址可以是 m3u8 或 mp4。
# 不填也不影响：点播页签照样在，只是显示「这条线路没有点播片库」。

def _vod_path():
    cfg = _cfg()
    return (cfg.get('vod_m3u') or os.environ.get('IPTV_VOD') or '').strip()


_VOD = {'path': '', 'mtime': 0.0, 'rows': []}


def _vod_rows():
    p = _vod_path()
    if not p or not os.path.isfile(p):
        return []
    try:
        mt = os.path.getmtime(p)
    except Exception:
        mt = 0.0
    if _VOD['path'] == p and _VOD['mtime'] == mt:
        return _VOD['rows']
    rows, cur = [], None
    with open(p, encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith('#EXTINF'):
                m = _EXTINF.match(line)
                title = (m.group(3) if m else '').strip()
                attrs = m.group(2) if m else ''
                g = re.search(r'group-title="([^"]*)"', attrs)
                cur = {'name': title or '未命名',
                       'cat': (g.group(1) if g else '').strip() or '未分类'}
            elif line.startswith('#'):
                continue
            elif cur is not None:
                cur['url'] = line
                rows.append(cur)
                cur = None
    out = []
    for i, r in enumerate(rows, 1):
        if not r.get('url'):
            continue
        out.append({'pid': 'v%05d' % i, 'cid': 'c%05d' % i, 'name': r['name'],
                    'cat': r['cat'], 'sub': r['cat'], 'type': r['cat'], 'url': r['url']})
    _VOD.update({'path': p, 'mtime': mt, 'rows': out})
    return out


def _pick(kw):
    rows = _vod_rows()
    cat = kw.get('cat') or ''
    t = kw.get('type') or ''
    tag = kw.get('tag') or ''
    q = (kw.get('kw') or '').strip()
    if cat:
        rows = [r for r in rows if r['cat'] == cat]
    if t and t != '全部':
        rows = [r for r in rows if r['cat'] == t]
    if tag:
        rows = [r for r in rows if tag in (r['name'] + r['cat'])]
    if q:
        rows = [r for r in rows if q in r['name']]
    return rows


def vod_items(cat=None, kw=None, offset=0, limit=0, type=None, yb=None, fk=False,
              sort=None, tag=None):
    rows = _pick({'cat': cat, 'kw': kw, 'type': type, 'tag': tag})
    total = len(rows)
    off = max(0, int(offset or 0))
    lim = int(limit or 0)
    part = rows[off:off + lim] if lim > 0 else rows[off:]
    return total, [{'pid': r['pid'], 'cid': r['cid'], 'name': r['name'],
                    'cat': r['cat'], 'sub': r['sub'], 'type': r['type'],
                    'img': '', 'year': '', 'res': '', 'desc': ''} for r in part]


def vod_meta():
    rows = _vod_rows()
    groups = {}
    for r in rows:
        groups[r['cat']] = groups.get(r['cat'], 0) + 1
    return {'total': len(rows), 'groups': groups,
            'types': dict(groups), 'tags': list(groups.keys()),
            'bands': {}, 'subs': dict(groups), 'sample': [], 'building': False,
            'note': '' if rows else '这条线路没有点播片库（可在 config.json 填 vod_m3u）'}


def vod_search(kw, limit=60):
    rows = _pick({'kw': kw})
    part = rows[:max(1, int(limit or 60))]
    return len(rows), [{'pid': r['pid'], 'cid': r['cid'], 'name': r['name'],
                        'cat': r['cat'], 'img': '', 'desc': ''} for r in part]


def vod_playlist(pid, cid, host):
    for r in _vod_rows():
        if r['pid'] == pid and r['cid'] == cid:
            return '#EXTM3U\n#EXTINF:-1,%s\n%s\n' % (r['name'], r['url']), 1
    raise KeyError('%s/%s' % (pid, cid))

