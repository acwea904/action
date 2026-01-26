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


class KataBumpRenewer:
    def __init__(self):
        self.base = 'https://dashboard.katabump.com'
        self.session = requests.Session()
        
        # 禁用自动重定向，手动处理
        self.session.max_redirects = 10
        
        # 设置初始 Cookie
        if KATA_COOKIES:
            for item in KATA_COOKIES.split(';'):
                item = item.strip()
                if '=' in item:
                    name, value = item.split('=', 1)
                    self.session.cookies.set(name.strip(), value.strip(), domain='dashboard.katabump.com')
                    # 同时设置到主域名
                    self.session.cookies.set(name.strip(), value.strip(), domain='katabump.com')
        
        self.session.headers.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Chromium";v="144", "Google Chrome";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'upgrade-insecure-requests': '1',
        })
        
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path, json_resp=False):
        url = f'{self.base}{path}'
        headers = {'referer': f'{self.base}/dashboard'}
        
        if json_resp:
            headers['accept'] = 'application/json, text/plain, */*'
            headers['x-requested-with'] = 'XMLHttpRequest'
        
        # 禁用自动重定向
        resp = self.session.get(url, headers=headers, timeout=60, allow_redirects=False)
        
        # 手动处理重定向（最多5次）
        for i in range(5):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            
            location = resp.headers.get('Location', '')
            if not location:
                break
            
            if not location.startswith('http'):
                location = f'{self.base}{location}'
            
            if DEBUG_MODE:
                log(f'重定向 {i+1}: {location}', 'DEBUG')
            
            # 检查是否重定向到登录页
            if '/auth/login' in location:
                log('被重定向到登录页', 'WARNING')
                break
            
            resp = self.session.get(location, headers=headers, timeout=60, allow_redirects=False)
        
        if DEBUG_MODE:
            log(f'GET {path} -> {resp.status_code} (len={len(resp.text)})', 'DEBUG')
        
        return resp

    def post(self, path, data):
        url = f'{self.base}{path}'
        headers = {
            'content-type': 'application/x-www-form-urlencoded',
            'origin': self.base,
            'referer': f'{self.base}/dashboard',
        }
        
        resp = self.session.post(url, data=data, headers=headers, timeout=60, allow_redirects=False)
        
        # 手动处理重定向
        for i in range(5):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            
            location = resp.headers.get('Location', '')
            if not location:
                break
            
            if not location.startswith('http'):
                location = f'{self.base}{location}'
            
            if DEBUG_MODE:
                log(f'POST 重定向 {i+1}: {location}', 'DEBUG')
            
            # POST 后的重定向用 GET
            resp = self.session.get(location, timeout=60, allow_redirects=False)
        
        if DEBUG_MODE:
            log(f'POST {path} -> {resp.status_code}', 'DEBUG')
        
        return resp

    def get_servers(self):
        log('获取服务器列表...')
        
        # 访问 dashboard
        resp = self.get('/dashboard')
        
        # 检查登录状态
        if resp.status_code == 302 and '/auth/login' in resp.headers.get('Location', ''):
            raise Exception('Cookie 已过期 (重定向到登录页)')
        
        if 'name="password"' in resp.text and 'name="email"' in resp.text:
            raise Exception('Cookie 已过期 (显示登录表单)')
        
        if 'Your servers' not in resp.text and 'Dashboard' not in resp.text:
            if DEBUG_MODE:
                log(f'页面内容: {resp.text[:300]}', 'DEBUG')
            raise Exception('Cookie 已过期 (页面内容异常)')
        
        log('登录状态正常', 'SUCCESS')
        
        # 调用 API
        resp = self.get('/api-client/list-servers', json_resp=True)
        
        if DEBUG_MODE:
            log(f'API 响应: {resp.text[:300]}', 'DEBUG')
        
        try:
            servers = resp.json()
        except:
            raise Exception(f'API 返回非 JSON: {resp.text[:100]}')
        
        if not isinstance(servers, list):
            raise Exception(f'API 格式错误: {servers}')
        
        return [{'id': s['id'], 'name': s.get('name', f"Server-{s['id']}")} for s in servers]

    def process_server(self, sid, name):
        log(f'处理: {name} (ID: {sid})')
        
        resp = self.get(f'/servers/edit?id={sid}')
        html = resp.text
        
        # 获取到期时间
        m = re.search(r'Expiry[\s\S]{0,100}?(\d{4}-\d{2}-\d{2})', html) or re.search(r'>(\d{4}-\d{2}-\d{2})<', html)
        expiry = m.group(1) if m else None
        days = days_until(expiry)
        
        log(f'  到期: {expiry or "?"} | 剩余: {days if days is not None else "?"} 天')
        
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            log(f'  无需续订', 'SUCCESS')
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 获取 CSRF
        m = re.search(r'name="csrf"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf"', html)
        if not m:
            return {'name': name, 'action': 'error', 'msg': '无CSRF', 'ok': False}
        csrf = m.group(1)
        
        log(f'  执行续订...')
        resp = self.post(f'/api-client/renew?id={sid}', {'csrf': csrf})
        
        # 检查最终 URL
        final_url = str(resp.url) if hasattr(resp, 'url') else ''
        
        # 也检查响应内容和 Location 头
        location = resp.headers.get('Location', '')
        
        if 'renew=success' in final_url or 'renew=success' in location or 'renew=success' in resp.text:
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={sid}')
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', resp2.text)
            new_expiry = m2.group(1) if m2 else '?'
            log(f'  续订成功！新到期: {new_expiry}', 'SUCCESS')
            return {'name': name, 'old': expiry, 'new': new_expiry, 'action': 'renewed', 'ok': True}
        
        # 检查错误
        error_match = re.search(r'renew-error=([^&"]+)', final_url + location + resp.text)
        if error_match:
            msg = unquote(error_match.group(1).replace('+', ' '))
            log(f'  {msg}', 'WARNING')
            if 'not yet' in msg.lower() or "can't" in msg.lower():
                return {'name': name, 'expiry': expiry, 'action': 'not_yet', 'msg': msg, 'ok': True}
            return {'name': name, 'action': 'failed', 'msg': msg, 'ok': False}
        
        if DEBUG_MODE:
            log(f'  响应: {resp.text[:200]}', 'DEBUG')
        
        return {'name': name, 'action': 'unknown', 'ok': False}

    def run(self):
        log('KataBump 自动续订')
        log('=' * 40)
        
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        if FORCE_RENEW:
            log('强制续订模式', 'WARNING')
        
        servers = self.get_servers()
        log(f'找到 {len(servers)} 个服务器')
        
        if not servers:
            tg_notify('📋 KataBump: 没有服务器')
            return True
        
        results = []
        for s in servers:
            results.append(self.process_server(s['id'], s['name']))
        
        log('=' * 40)
        
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
                msg.append(f"ℹ️ {r['name']}: 暂不能续订")
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
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        tg_notify(f'❌ KataBump 出错: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
