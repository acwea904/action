#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KataBump 自动续订"""

import os, sys, re, requests
from datetime import datetime, timezone, timedelta

DASHBOARD = 'https://dashboard.katabump.com'
CF_CHALLENGE = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/cmg/1'
EMAIL = os.environ.get('KATA_EMAIL', '')
PASSWORD = os.environ.get('KATA_PASSWORD', '')
TG_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT = os.environ.get('TG_USER_ID', '')
PROXY = os.environ.get('PROXY_SERVER', '')


def log(msg):
    t = datetime.now(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def tg(msg):
    if TG_TOKEN and TG_CHAT:
        try:
            requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
                          json={'chat_id': TG_CHAT, 'text': msg}, timeout=30)
        except:
            pass


def expiry(text):
    m = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', text, re.I)
    return m.group(1) if m else None


def days(d):
    try:
        return (datetime.strptime(d, '%Y-%m-%d') - datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).days
    except:
        return None


def err(url):
    if 'renew-error' not in url:
        return None
    from urllib.parse import unquote
    m = re.search(r'renew-error=([^&]+)', url)
    return unquote(m.group(1).replace('+', ' ')) if m else '受限'


def renew(s, sid):
    log(f'📦 {sid[:8]}...')
    url = f'{DASHBOARD}/servers/edit?id={sid}'
    
    resp = s.get(url, timeout=30)
    exp = expiry(resp.text) or '?'
    log(f'📅 {exp} (剩{days(exp)}天)')
    
    e = err(resp.url)
    if e:
        log(f'⏳ {e}')
        return {'id': sid, 'exp': exp, 'ok': False}
    
    s.get(CF_CHALLENGE, timeout=30)
    
    r = s.post(f'{DASHBOARD}/api-client/renew?id={sid}',
               headers={'Referer': url}, timeout=30, allow_redirects=False)
    loc = r.headers.get('Location', '')
    
    if 'renew=success' in loc:
        new = expiry(s.get(url, timeout=30).text) or exp
        log(f'🎉 {exp} → {new}')
        return {'id': sid, 'exp': new, 'ok': True, 'old': exp}
    
    if 'renew-error' in loc:
        log(f'⏳ {err(loc)}')
    
    return {'id': sid, 'exp': exp, 'ok': False}


def main():
    if not EMAIL or not PASSWORD:
        sys.exit('设置 KATA_EMAIL 和 KATA_PASSWORD')
    
    log('🚀 KataBump')
    
    s = requests.Session()
    s.headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    if PROXY:
        s.proxies = {'http': PROXY, 'https': PROXY}
    
    log('🔐 登录...')
    s.get(f'{DASHBOARD}/auth/login', timeout=30)
    r = s.post(f'{DASHBOARD}/auth/login',
               data={'email': EMAIL, 'password': PASSWORD, 'remember': 'true'},
               timeout=30, allow_redirects=True)
    if '/auth/login' in r.url:
        sys.exit('❌ 登录失败')
    log('✅ 登录成功')
    
    html = s.get(f'{DASHBOARD}/servers', timeout=30).text
    sids = list({m.group(1) for m in re.finditer(r'/servers/edit\?id=([a-zA-Z0-9-]+)', html)})
    log(f'📦 {len(sids)} 个服务器')
    
    results = [renew(s, sid) for sid in sids]
    ok = [r for r in results if r['ok']]
    
    msg = f'KataBump: {len(ok)}/{len(results)} 成功'
    for r in ok:
        msg += f"\n{r['id'][:8]}→{r['exp']}"
    tg(msg)
    log(f'✅ 完成 {len(ok)}/{len(results)}')


if __name__ == '__main__':
    main()
