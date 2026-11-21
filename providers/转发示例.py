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


def _svc_path():
    import json
    cfg_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)),
                                        'config.json'))
    p = os.environ.get('IPTV_SVC_PY', '')
    if not p and os.path.isfile(cfg_p):
        try:
            with open(cfg_p, 'r', encoding='utf-8-sig') as f:
                p = json.load(f).get('svc_py') or ''
        except Exception:
            p = ''
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
    return _svc().channels()


def epg(code=None):
    return _svc().epg(code)


def status_extra():
    return {'svc': _svc_path()}
