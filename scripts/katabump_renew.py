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
from http.cookiejar import CookieJar

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
        
        # 直接设置 Cookie header，不使用 cookie jar
        self.cookie_str = KATA_COOKIES
        
        self.session.headers.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36',
            'upgrade-insecure-requests': '1',
            'cookie': self.cookie_str,  # 直接设置 Cookie header
        })
        
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def request(self, method, path, **kwargs):
        url = f'{self.base}{path}'
        
        # 确保每次请求都带上原始 Cookie
        headers = kwargs.pop('headers', {})
        headers['cookie'] = self.cookie_str
        headers['referer'] = f'{self.base}/dashboard'
        
        # 禁用自动重定向
        resp = self.session.request(method, url, headers=headers, timeout=60, allow_redirects=False, **kwargs)
        
        # 手动处理重定向（最多10次）
        visited = set()
        for i in range(10):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            
            location = resp.headers.get('Location', '')
            if not location:
                break
            
            # 防止无限循环
            if location in visited:
                log(f'检测到重定向循环: {location}', 'WARNING')
                break
            visited.add(location)
            
            if not location.startswith('http'):
                location = f'{self.base}{location}'
            
            log(f'  重定向 {i+1}: {location}')
            
            # 检查是否重定向到登录页
            if '/auth/login' in location:
                log('被重定向到登录页，Cookie 已过期', 'ERROR')
                raise Exception('Cookie 已过期')
            
            # 重定向请求也要带上 Cookie
            resp = self.session.get(location, headers={'cookie': self.cookie_str}, timeout=60, allow_redirects=False)
        
        log(f'{method} {path} -> {resp.status_code} (len={len(resp.text)})')
        return resp

    def get(self, path, json_resp=False):
        headers = {}
        if json_resp:
            headers['accept'] = 'application/json, text/plain, */*'
            headers['x-requested-with'] = 'XMLHttpRequest'
        return self.request('GET', path, headers=headers)

    def post(self, path, data):
        headers = {
            'content-type': 'application/x-www-form-urlencoded',
            'origin': self.base,
        }
        return self.request('POST', path, headers=headers, data=data)

    def get_servers(self):
        log('获取服务器列表...')
        
        # 访问 dashboard
        resp = self.get('/dashboard')
        html = resp.text
        
        # 检查是否是 Cloudflare 验证页面
        if 'Just a moment' in html or 'cf-browser-verification' in html:
            raise Exception('遇到 Cloudflare 验证页面')
        
        # 检查是否是登录页面
        if 'name="password"' in html and 'name="email"' in html:
            raise Exception('Cookie 已过期 (显示登录表单)')
        
        # 检查是否有 dashboard 内容
        if 'Your servers' not in html and 'Dashboard' not in html and len(html) < 100:
            log(f'页面内容异常: {html[:500]}', 'WARNING')
            raise Exception('Cookie 已过期或页面异常')
        
        log('登录状态正常', 'SUCCESS')
        
        # 调用 API
        resp = self.get('/api-client/list-servers', json_resp=True)
        
        if not resp.text:
            raise Exception('API 返回空响应')
        
        log(f'API 响应: {resp.text[:200]}')
        
        try:
            servers = resp.json()
        except Exception as e:
            raise Exception(f'API 返回非 JSON: {resp.text[:200]}')
        
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
            log(f'  未找到 CSRF，页面: {html[:300]}', 'WARNING')
            return {'name': name, 'action': 'error', 'msg': '无CSRF', 'ok': False}
        csrf = m.group(1)
        
        log(f'  执行续订...')
        resp = self.post(f'/api-client/renew?id={sid}', {'csrf': csrf})
        
        # 检查结果
        location = resp.headers.get('Location', '')
        text = resp.text
        
        if 'renew=success' in location or 'renew=success' in text:
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={sid}')
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', resp2.text)
            new_expiry = m2.group(1) if m2 else '?'
            log(f'  续订成功！新到期: {new_expiry}', 'SUCCESS')
            return {'name': name, 'old': expiry, 'new': new_expiry, 'action': 'renewed', 'ok': True}
        
        error_match = re.search(r'renew-error=([^&"]+)', location + text)
        if error_match:
            msg = unquote(error_match.group(1).replace('+', ' '))
            log(f'  {msg}', 'WARNING')
            if 'not yet' in msg.lower() or "can't" in msg.lower():
                return {'name': name, 'expiry': expiry, 'action': 'not_yet', 'msg': msg, 'ok': True}
            return {'name': name, 'action': 'failed', 'msg': msg, 'ok': False}
        
        log(f'  未知响应: location={location}, text={text[:200]}')
        return {'name': name, 'action': 'unknown', 'ok': False}

    def run(self):
        log('KataBump 自动续订')
        log('=' * 50)
        
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        # 显示 Cookie 信息（隐藏值）
        cookies = [c.split('=')[0] for c in KATA_COOKIES.split(';') if '=' in c]
        log(f'Cookie 名称: {", ".join(cookies)}')
        
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
        
        log('=' * 50)
        
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
        import traceback
        traceback.print_exc()
        tg_notify(f'❌ KataBump 出错: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
