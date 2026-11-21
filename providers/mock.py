# -*- coding: utf-8 -*-
"""演示线路（mock）：不联网也有 24 个台、节目单和 96 部假片，用来看界面和验收模板。

真接线路时不用改它，照这个文件的函数名写自己的适配器即可。
"""
import hashlib
import time

NAME = '演示线路'
DESC = '内置假数据，不连外网，用来看界面'
TITLE = '我的IPTV'
SUB = '演示外壳'
CAPS = {'live': True, 'epg': True, 'vod': True, 'search': True, 'poster': True,
        'episodes': True, 'variant': False, 'seg': False}

CHANNELS = [
    ('cctv1', 'CCTV-1 综合', '央视', 1), ('cctv2', 'CCTV-2 财经', '央视', 2),
    ('cctv5', 'CCTV-5 体育', '央视', 5), ('cctv6', 'CCTV-6 电影', '央视', 6),
    ('cctv8', 'CCTV-8 电视剧', '央视', 8), ('cctv13', 'CCTV-13 新闻', '央视', 13),
    ('hnws', '湖南卫视', '卫视', 30), ('dfws', '东方卫视', '卫视', 31),
    ('zjmx', '浙江卫视', '卫视', 32), ('jspt', '江苏卫视', '卫视', 33),
    ('bjsp', '北京卫视', '卫视', 34), ('gdsp', '广东卫视', '卫视', 35),
    ('szsp', '深圳卫视', '卫视', 36), ('ahsp', '安徽卫视', '卫视', 37),
    ('local1', '曲靖新闻综合', '本地', 60), ('local2', '曲靖公共频道', '本地', 61),
    ('film1', '影视频道', '轮播', 80), ('film2', '经典剧场', '轮播', 81),
    ('doc1', '纪录频道', '轮播', 82), ('kid1', '少儿频道', '轮播', 83),
    ('sysp', '云南卫视', '本地', 62), ('km1', '昆明新闻', '本地', 63),
    ('chan1', '游戏风云', '轮播', 84), ('chan2', '音乐现场', '轮播', 85),
]

_TYPES = ['电影', '电视剧', '综艺', '纪录']
_CATS = {'电影': '电影频道', '电视剧': '电视剧频道', '综艺': '综艺专区', '纪录': '纪录专区'}
_TAGS = ['动作', '悬疑', '喜剧', '爱情', '科幻', '武侠', '犯罪', '家庭', '历史', '美食']
_WORDS = ['逐光', '长夜', '归途', '破晓', '云上', '静默', '逆风', '群山', '深蓝', '回声',
          '无人区', '雾中', '山海', '少年', '江湖', '旧梦', '铁血', '春风', '暗涌', '潮汐']


def _seed(*parts):
    h = hashlib.md5(('|'.join(str(p) for p in parts)).encode('utf-8')).hexdigest()
    return int(h[:8], 16)


def channels():
    return [{'code': c, 'name': n, 'group': g, 'num': num,
             'url': 'http://127.0.0.1:9%d/%s.m3u8' % (num % 10, c)}
            for c, n, g, num in CHANNELS]


def _hhmm(minutes):
    minutes = max(0, min(minutes, 23 * 60 + 59))
    return '%02d%02d' % (minutes // 60, minutes % 60)


def epg():
    """今天一天的节目单，格式：HHMM 起始 + 节目名"""
    out = {}
    for c, n, g, num in CHANNELS:
        progs = []
        start = 6 * 60
        for i in range(6):
            length = 90 + _seed(c, i) % 90
            end = min(start + length, 23 * 60 + 59)
            label = ['早间新闻', '剧场', '午间档', '下午茶', '晚间黄金档', '深夜场'][i]
            progs.append({'s': _hhmm(start), 'e': _hhmm(end), 'n': '%s·%s' % (n, label)})
            start = end
        cur = None
        hhmm = time.strftime('%H%M')
        for p in progs:
            if p['s'] <= hhmm < p['e']:
                cur = p
        now_i = progs.index(cur) if cur else 0
        out[c] = {
            'cur': cur['n'] if cur else '',
            'cur_start': cur['s'] if cur else '',
            'cur_end': cur['e'] if cur else '',
            'next': progs[now_i + 1]['n'] if now_i + 1 < len(progs) else '',
            'next_start': progs[now_i + 1]['s'] if now_i + 1 < len(progs) else '',
            'list': progs,
        }
    return out


def live_playlist(code, host):
    """演示用：给一条放不出来的清单，只为证明链路通（真线路在这里签地址）"""
    return ('#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:-1,演示线路（无真实源）\n'
            'http://127.0.0.1:9/demo-%s.ts\n' % code)


def _all_items():
    items = []
    for i in range(96):
        t = _TYPES[i % 4]
        y = 2020 + (_seed(i, 'y') % 7)
        tags = [_TAGS[_seed(i, k) % len(_TAGS)] for k in range(2)]
        name = '%s%s' % (_WORDS[i % len(_WORDS)], _WORDS[(i * 7 + 3) % len(_WORDS)])
        if i >= 80:
            name += '（4K）'
        items.append({
            'pid': str(1000 + i), 'cid': 'v%03d' % i, 'name': name,
            'cat': _CATS[t], 'sub': _CATS[t], 'type': t, 'year': str(y),
            'res': '4K' if '4K' in name else 'HD',
            'img': 'mock://%s' % name, 'still': 'mock://%s?wide=1' % name,
            'vol': '全%d集' % (12 + i % 20) if t == '电视剧' else '',
            'act': '演员甲, 演员乙, 演员丙', 'dir': '导演%s' % _WORDS[(i + 5) % len(_WORDS)],
            'tg': ','.join(tags),
            'desc': '这是一部演示用的假片：%s，%s年出品。接真线路以后这里会显示上游给的简介。'
                    % (name, y),
            'reg': '中国', 'lang': '国语',
            'up': '%d%02d%02d%02d%02d%02d' % (y, 1 + i % 12, 1 + i % 28, 8 + i % 12, i % 60, 0),
        })
    return items


def _year_num(y):
    d = ''.join(ch for ch in str(y or '') if ch.isdigit())
    return int(d) if d else 0


def _year_band(y):
    n = _year_num(y)
    return '2026' if n >= 2026 else ('2025' if n == 2025 else ('2024' if n == 2024 else 'old'))


def vod_items(cat=None, kw=None, offset=0, limit=0, type=None, yb=None, fk=False,
              sort=None, tag=None):
    items = _all_items()
    if type and type != '全部':
        items = [i for i in items if i['type'] == type]
    if fk:
        items = [i for i in items if '4K' in i['name'].upper()]
    if yb and yb != 'all':
        items = [i for i in items if _year_band(i['year']) == yb]
    if cat:
        items = [i for i in items if i['cat'] == cat or i['sub'] == cat]
    if tag:
        items = [i for i in items if tag in [x.strip() for x in i['tg'].split(',')]]
    if kw:
        items = [i for i in items if kw in i['name']]
    items = sorted(items, key=lambda i: (i.get('up') or ''), reverse=True) if sort == 'up' \
        else sorted(items, key=lambda i: _year_num(i['year']), reverse=True)
    total = len(items)
    items = items[offset:offset + limit] if limit else items[offset:]
    return total, items


def vod_search(kw, limit=60):
    if not kw:
        return 0, []
    items = [i for i in _all_items() if kw in i['name']]
    return len(items), items[:limit]


def vod_meta():
    items = _all_items()
    bands, cats, types = {}, {}, {}
    for it in items:
        d = bands.setdefault(it['type'], {'all': 0, '2026': 0, '2025': 0, '2024': 0,
                                          'old': 0, '4k': 0})
        d['all'] += 1
        d[_year_band(it['year'])] += 1
        if '4K' in it['name'].upper():
            d['4k'] += 1
        cats.setdefault(it['cat'], {})
        cats[it['cat']][it['sub']] = cats[it['cat']].get(it['sub'], 0) + 1
        types[it['type']] = types.get(it['type'], 0) + 1
    cnt = {}
    for it in items:
        for t in it['tg'].split(','):
            t = t.strip()
            if t:
                cnt[t] = cnt.get(t, 0) + 1
    tags = [{'n': k, 'c': v} for k, v in cnt.items() if v >= 2]
    tags.sort(key=lambda x: -x['c'])
    return {'total': len(items), 'building': False, 'build_at': int(time.time()),
            'age_sec': 0, 'ttl_sec': 21600, 'error': None,
            'groups': {k: sum(v.values()) for k, v in cats.items()},
            'types': types, 'bands': bands, 'tags': tags[:18], 'subs': cats,
            'sample': items[:5]}


def vod_episodes(pid, cid):
    """演示：电视剧给 4 集，其它单片不给集"""
    for it in _all_items():
        if it['pid'] == pid and it['cid'] == cid and it['type'] == '电视剧':
            return [{'pid': pid, 'cid': '%s_ep%d' % (cid, k), 'seq': k,
                     'name': '第%d集' % k} for k in range(1, 5)]
    return []


def vod_playlist(pid, cid, host):
    return ('#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:6,演示线路（无真实源）\n'
            'http://127.0.0.1:9/demo-%s-%s.ts\n' % (pid, cid)), 1


_SVG = ('<?xml version="1.0" encoding="UTF-8"?><svg xmlns="http://www.w3.org/2000/svg" '
        'width="300" height="450"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#1b2c44"/><stop offset="1" stop-color="#0a1017"/>'
        '</linearGradient></defs><rect width="300" height="450" fill="url(#g)"/>'
        '<text x="150" y="235" fill="#7fb6ff" font-family="Microsoft YaHei" font-size="22" '
        'text-anchor="middle">%s</text></svg>')


def poster(url):
    name = str(url or '').replace('mock://', '').split('?')[0][:18] or '无海报'
    return _SVG % name, 'image/svg+xml'
