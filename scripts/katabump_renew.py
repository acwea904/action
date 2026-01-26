#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KataBump 自动续订脚本"""

import os
import sys
import re
import time
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote

KATA_COOKIES = os.environ.get('KATA_COOKIES', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'
RENEW_THRESHOLD_DAYS = 2


def log(msg, level='INFO'):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    icons = {'INFO': '📋', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'DEBUG': '🔍'}
    print(f'[{t}] {icons.get(level, "📋")} {msg}')


def tg_notify(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=30, proxies={'http': None, 'https': None}
        )
    except:
        pass


def days_until(date_str):
    if not date_str:
        return None
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


def parse_cookies(cookie_str):
    cookies = {}
    if cookie_str:
        for item in cookie_str.split(';'):
            item = item.strip()
            if '=' in item:
                k, v = item.split('=', 1)
                cookies[k.strip()] = v.strip()
    return cookies


class KataBumpRenewer:
    def __init__(self):
        self.base = 'https://dashboard.katabump.com'
        self.session = requests.Session()
        
        for k, v in parse_cookies(KATA_COOKIES).items():
            self.session.cookies.set(k, v, domain='dashboard.katabump.com')
        
        self.session.headers.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-encoding': 'gzip, deflate',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        })
        
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path, json_resp=False):
        url = f'{self.base}{path}'
        headers = {}
        if json_resp:
            headers['accept'] = 'application/json, text/plain, */*'
            headers['sec-fetch-dest'] = 'empty'
            headers['sec-fetch-mode'] = 'cors'
        
        resp = self.session.get(url, headers=headers, timeout=60)
        
        if DEBUG_MODE:
            log(f'GET {path} -> {resp.status_code}, URL: {resp.url}', 'DEBUG')
        
        return resp

    def post(self, path, data):
        url = f'{self.base}{path}'
        headers = {
            'content-type': 'application/x-www-form-urlencoded',
            'origin': self.base,
        }
        resp = self.session.post(url, data=data, headers=headers, timeout=60, allow_redirects=True)
        
        if DEBUG_MODE:
            log(f'POST {path} -> {resp.status_code}, URL: {resp.url}', 'DEBUG')
        
        return resp

    def get_servers(self):
        log('获取服务器列表...')
        
        resp = self.get('/dashboard')
        resp = self.get('/api-client/list-servers', json_resp=True)
        
        if resp.text.strip().startswith('<!') or resp.text.strip().startswith('<html'):
            raise Exception('Cookie 已过期')
        
        try:
            servers = resp.json()
        except:
            raise Exception('Cookie 已过期或 API 错误')
        
        if not isinstance(servers, list):
            raise Exception(f'API 返回格式错误')
        
        return [{'id': s['id'], 'name': s.get('name', f"Server-{s['id']}")} for s in servers]

    def process_server(self, sid, name):
        log(f'处理: {name} (ID: {sid})')
        
        self.session.headers['referer'] = f'{self.base}/dashboard'
        resp = self.get(f'/servers/edit?id={sid}')
        html = resp.text
        
        # 获取到期时间
        m = re.search(r'Expiry[\s\S]{0,100}?(\d{4}-\d{2}-\d{2})', html) or re.search(r'>(\d{4}-\d{2}-\d{2})<', html)
        expiry = m.group(1) if m else None
        days = days_until(expiry)
        
        log(f'  到期: {expiry or "?"} | 剩余: {days if days is not None else "?"} 天')
        
        # 判断是否需要续订
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            log(f'  无需续订', 'SUCCESS')
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 获取 CSRF
        m = re.search(r'name="csrf"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf"', html)
        if not m:
            return {'name': name, 'action': 'error', 'msg': '无CSRF', 'ok': False}
        csrf = m.group(1)
        
        # 执行续订
        log(f'  执行续订...')
        self.session.headers['referer'] = f'{self.base}/servers/edit?id={sid}'
        resp = self.post(f'/api-client/renew?id={sid}', {'csrf': csrf})
        
        # 检查最终 URL
        final_url = str(resp.url)
        if DEBUG_MODE:
            log(f'  最终URL: {final_url}', 'DEBUG')
        
        # 检查结果 - 优先检查 error
        if 'renew-error=' in final_url:
            m = re.search(r'renew-error=([^&]+)', final_url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            log(f'  {msg}', 'WARNING')
            # 暂不能续订（正常情况）
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'not_yet', 'msg': msg, 'ok': True}
        
        if 'renew=success' in final_url:
            # 获取新到期时间
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={sid}')
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', resp2.text)
            new_expiry = m2.group(1) if m2 else '?'
            log(f'  续订成功！新到期: {new_expiry}', 'SUCCESS')
            return {'name': name, 'old': expiry, 'new': new_expiry, 'action': 'renewed', 'ok': True}
        
        # 未知结果
        log(f'  续订结果未知', 'WARNING')
        return {'name': name, 'expiry': expiry, 'action': 'unknown', 'msg': '未知结果', 'ok': False}

    def run(self):
        log('KataBump 自动续订')
        
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        servers = self.get_servers()
        log(f'找到 {len(servers)} 个服务器')
        
        if not servers:
            tg_notify('📋 KataBump: 没有服务器')
            return True
        
        results = []
        for s in servers:
            results.append(self.process_server(s['id'], s['name']))
        
        # 汇总
        renewed = [r for r in results if r['action'] == 'renewed']
        skipped = [r for r in results if r['action'] == 'skip']
        not_yet = [r for r in results if r['action'] == 'not_yet']
        failed = [r for r in results if r['action'] in ('failed', 'error', 'unknown')]
        
        msg = ['📋 <b>KataBump</b>']
        if renewed:
            for r in renewed:
                msg.append(f"✅ {r['name']}: {r.get('old')} → {r.get('new')}")
        if skipped:
            for r in skipped:
                msg.append(f"📋 {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        if not_yet:
            for r in not_yet:
                msg.append(f"ℹ️ {r['name']}: {r.get('expiry')} ({r.get('days')}天) - 暂不能续订")
        if failed:
            for r in failed:
                msg.append(f"❌ {r['name']}: {r.get('msg', '失败')}")
        
        tg_notify('\n'.join(msg))
        log('完成', 'SUCCESS')
        return len(failed) == 0


def main():
    try:
        ok = KataBumpRenewer().run()
        sys.exit(0 if ok else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        tg_notify(f'❌ KataBump 出错: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
