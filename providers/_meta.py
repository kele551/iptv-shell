"""点播元数据的公共算法：把一部部片子算成左栏要的「类型 + 年份档 + 4K + 题材」。

为什么单独抽出来：页面左栏和「全部 / 4K专区」的计数全靠这几组数据，
任何一条线路只要漏算，用户看到的就是空左栏、全是 0 —— 每条线路各写一份必然漏，
所以 m3u 线路和「转发示例」线路都用这一份。
"""

import re

TYPE_ORDER = ['电影', '电视剧', '纪录', '综艺', '动漫', '少儿']
BAND_KEYS = ['2026', '2025', '2024', 'old']


def year_band(year):
    m = re.search(r'(19|20)\d{2}', str(year or ''))
    if not m:
        return ''
    n = int(m.group(0))
    if n >= 2026:
        return '2026'
    if n == 2025:
        return '2025'
    if n == 2024:
        return '2024'
    return 'old'


def is_4k(it):
    blob = ((it.get('res') or '') + ' ' + (it.get('name') or '')).upper()
    return ('4K' in blob) or ('UHD' in blob) or ('2160' in blob)


def norm_type(it):
    t = (it.get('type') or '').strip()
    if t in TYPE_ORDER:
        return t
    if t:
        # 上游常见写法：纪录（片）/纪录片/综艺节目/动画/少儿动画
        for k in TYPE_ORDER:
            if k in t:
                return k
        return t
    # 没有类型就拿栏目名硬认一次
    cat = (it.get('cat') or '') + ' ' + (it.get('sub') or '')
    for k in TYPE_ORDER:
        if k in cat:
            return k
    return '其他'


def build_meta(items):
    """返回 (types, bands, tags)：都是页面左栏直接吃的结构"""
    types, bands, tags = {}, {}, {}
    for it in items:
        t = norm_type(it)
        types[t] = types.get(t, 0) + 1
        d = bands.setdefault(t, {'all': 0, '2026': 0, '2025': 0, '2024': 0,
                                 'old': 0, '4k': 0})
        d['all'] += 1
        # 有些 m3u 清单没有年份字段，年份写在片名里（如「XX（2024）」），也从名字里认一次
        yb = year_band(it.get('year')) or year_band(it.get('name'))
        if yb:
            d[yb] += 1
        if is_4k(it):
            d['4k'] += 1
        for x in (it.get('tg') or '').replace('，', ',').split(','):
            x = x.strip()
            if x:
                tags[x] = tags.get(x, 0) + 1
    tag_list = [{'n': k, 'c': v} for k, v in
                sorted(tags.items(), key=lambda kv: -kv[1])[:40]]
    return types, bands, tag_list
