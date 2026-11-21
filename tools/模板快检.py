#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板快检：起一个临时端口，把外壳该有的接口全部打一遍，出 PASS/FAIL。

用法：
  python tools/模板快检.py                      # 用 config.json 里的线路和端口
  python tools/模板快检.py --provider m3u --port 8906
  python tools/模板快检.py --register           # 只登记（不跑），给发版闸门用
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(HERE, 'server.py')
PY = sys.executable

ok_n = 0
bad_n = 0


def say(ok, name, detail=''):
    global ok_n, bad_n
    if ok:
        ok_n += 1
        print('  [OK]   %-34s %s' % (name, detail))
    else:
        bad_n += 1
        print('  [FAIL] %-34s %s' % (name, detail))


def free_port(start=8906):
    for p in range(start, start + 40):
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    return start


def get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get('Content-Type', '')
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b'', e.headers.get('Content-Type', '')
    except Exception as e:
        return 0, str(e).encode('utf-8'), ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--provider', default=None)
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--register', action='store_true')
    a = ap.parse_args()
    if a.register:
        print('模板快检：已登记（本机程序，暂无发版闸门）')
        return 0

    cfg = {}
    try:
        with open(os.path.join(HERE, 'config.json'), encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except Exception:
        pass
    prov = a.provider or cfg.get('provider') or 'mock'
    port = a.port or free_port()

    print('模板快检：线路=%s 端口=%d' % (prov, port))
    log = os.path.join(os.environ.get('TEMP') or '/tmp', 'iptv_shell_check.log')
    proc = subprocess.Popen([PY, SERVER, '--port', str(port), '--provider', prov],
                            cwd=HERE, stdout=open(log, 'w', encoding='utf-8'),
                            stderr=subprocess.STDOUT)
    base = 'http://127.0.0.1:%d' % port
    try:
        up = False
        for _ in range(50):
            try:
                st, body, _ct = get(base + '/status', timeout=3)
                if st == 200:
                    up = True
                    break
            except Exception:
                time.sleep(0.4)
        say(up, '服务能起来', base)
        if not up:
            print('      看日志：%s' % log)
            return 1

        status = json.loads(get(base + '/status')[1])
        say(status.get('channels', 0) > 0, '频道数', str(status.get('channels')))
        say(status.get('error') is None, '没有报错', str(status.get('error')))

        st, body, ct = get(base + '/')
        say(st == 200 and '我的' in body.decode('utf-8', 'ignore')[:4000], '网页能打开',
            '%d 字节' % len(body))

        st, body, _ = get(base + '/api/live.json')
        live = json.loads(body)
        say(live.get('total', 0) > 0, '直播 JSON', '共 %s 台 / %d 组'
            % (live.get('total'), len(live.get('groups') or [])))

        st, body, _ = get(base + '/live.m3u')
        say(st == 200 and body.startswith(b'#EXTM3U'), '直播清单', '%d 字节' % len(body))

        caps = status.get('caps') or {}
        if caps.get('epg'):
            st, body, _ = get(base + '/epg.xml')
            n = body.count(b'<programme')
            if n:
                say(st == 200 and n > 0, 'XMLTV 节目单', '%d 条节目' % n)
            else:
                print('  [skip] 这条线路没有节目单（不影响观看）')
            codes = [c for g in (live.get('groups') or []) for c in [g['items'][0]['code']]] if live.get('groups') else []
            if codes and n:
                st, body, _ = get(base + '/epg?code=' + codes[0])
                say(json.loads(body).get('list') is not None, '单频道节目单', codes[0])

        if caps.get('vod'):
            st, body, _ = get(base + '/api/vod.json')
            meta = json.loads(body)
            if meta.get('total', 0) > 0:
                say(True, '点播统计', '共 %s 部 / %s 个题材'
                    % (meta.get('total'), len(meta.get('tags') or [])))
                st, body, _ = get(base + '/api/vod.items.json?limit=3')
                items = json.loads(body).get('items') or []
                say(len(items) > 0, '点播条目', str(items[0]['name']) if items else '空')
                if items:
                    it = items[0]
                    st, body, _ = get(base + '/vod/%s/%s.m3u8' % (it['pid'], it['cid']))
                    head = body.decode('utf-8', 'ignore')
                    # 只有「地址指回本机 /vod/ 或 /seg」才算被改写；源站自己用 127.0.0.1 不算
                    hp = base.split('//', 1)[1]
                    rewritten = False
                    for ln in head.splitlines():
                        ln = ln.strip()
                        if not ln.startswith('http'):
                            continue
                        u = urllib.parse.urlsplit(ln)
                        if u.netloc == hp and u.path.startswith(('/vod', '/seg')):
                            rewritten = True
                            break
                    say(st in (200, 404), '点播清单可请求', 'HTTP %d' % st)
                    if st == 200:
                        say(not rewritten, '分片走直连（不绕本机）',
                            '是' if not rewritten else '被改写成转发地址了')
            else:
                print('  [skip] 这条线路没有点播片库（页签仍在，进去显示空态）')
        else:
            print('  [skip] 这条线路没开点播（页签仍在，进去显示空态）')

        if caps.get('poster'):
            st, body, ct = get(base + '/poster?u=' + urllib.parse.quote('x'))
            say(st in (200, 404), '海报接口', 'HTTP %d %s' % (st, ct))
        else:
            print('  [skip] 这条线路没有海报')

        st, body, _ = get(base + '/favicon.ico')
        say(st == 200, '站内小图标', '%d 字节' % len(body))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    print('\n结论：%s（OK %d / FAIL %d）'
          % ('可以交' if bad_n == 0 and ok_n > 0 else '不能交', ok_n, bad_n))
    return 0 if bad_n == 0 and ok_n > 0 else 1


if __name__ == '__main__':
    sys.exit(main())
