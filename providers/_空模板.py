# -*- coding: utf-8 -*-
"""新线路适配器样板：复制成 providers/<你的线路名>.py，改里面的 TODO。

命名注意：文件名就是 config.json 里 "provider" 要填的值。
"""
import os

NAME = '我的线路'          # TODO 线路名
DESC = '一句话说明这条线路 '
CAPS = {'live': True, 'epg': False, 'vod': False,
        'search': False, 'poster': False, 'episodes': False,
        'variant': False, 'seg': False}

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def channels():
    """必写。返回 [{'code','name','group','num','url'}]，code 要 URL 安全。"""
    # TODO 从上游/缓存文件取频道
    return [{'code': 'demo1', 'name': '示例频道', 'group': '示例',
             'num': 1, 'url': 'http://127.0.0.1:9/x.m3u8'}]


# 想有节目单就实现这个（时间为 HHMM 字符串）
# def epg():
#     return {'demo1': {'cur': '', 'cur_start': '', 'cur_end': '', 'next': '',
#                       'next_start': '', 'list': []}}

# 想自己签播放地址就实现这个（不实现则用 channels() 里的 url）
# def live_playlist(code, host):
#     return '#EXTM3U\n#EXTINF:-1,X\nhttp://...\n'

# 想有点播海报墙，实现这四个（结构照 providers/mock.py）
# def vod_items(cat=None, kw=None, offset=0, limit=0, type=None, yb=None,
#               fk=False, sort=None, tag=None): return 0, []
# def vod_meta(): return {'total': 0}
# def vod_search(kw, limit=60): return 0, []
# def vod_playlist(pid, cid, host): return '#EXTM3U\n', 0
