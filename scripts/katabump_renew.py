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
        
        # 使用 Session 来自动管理 Cookie
        self.session = requests.Session()
        
        # 设置初始 Cookie
        if KATA_COOKIES:
            for item in KATA_COOKIES.split(';'):
                item = item.strip()
                if '=' in item:
                    name, value = item.split('=', 1)
                    self.session.cookies.set(name.strip(), value.strip(), domain='dashboard.katabump.com')
        
        # 设置请求头
        self.session.headers.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'accept-encoding': 'gzip, deflate',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'referer': 'https://dashboard.katabump.com/dashboard',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
        })
        
        # 设置代理
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path, json_resp=False):
        headers = {}
        if json_resp:
            headers['accept'] = 'application/json'
            headers['sec-fetch-dest'] = 'empty'
            headers['sec-fetch-mode'] = 'cors'
        
        resp = self.session.get(f'{self.base}{path}', headers=headers, timeout=60)
        
        if DEBUG_MODE:
            log(f'GET {path} -> {resp.status_code}', 'DEBUG')
            # 显示当前 Cookie
            cookies = '; '.join([f'{c.name}={c.value[:20]}...' for c in self.session.cookies])
            log(f'Cookies: {cookies[:100]}...', 'DEBUG')
        
        return resp

    def post(self, path, data):
        headers = {
            'content-type': 'application/x-www-form-urlencoded',
            'origin': self.base,
        }
        resp = self.session.post(f'{self.base}{path}', data=data, headers=headers, timeout=60)
        if DEBUG_MODE:
            log(f'POST {path} -> {resp.status_code}', 'DEBUG')
        return resp

    def check_login(self, html, url):
        """检查是否已登录"""
        # 检查是否被重定向到登录页
        if '/auth/login' in str(url):
            return False
        # 检查页面是否包含登录表单
        if 'name="password"' in html and 'name="email"' in html:
            return False
        # 检查是否有用户信息（说明已登录）
        if 'dropdown-toggle' in html and 'Logout' in html:
            return True
        # 检查是否有 dashboard 内容
        if 'Your servers' in html or 'api-client/list-servers' in html:
            return True
        return True  # 默认认为已登录

    def get_servers(self):
        log('获取服务器列表...')
        
        # 访问 dashboard
        resp = self.get('/dashboard')
        
        if not self.check_login(resp.text, resp.url):
            log('登录检查失败，页面内容:', 'DEBUG')
            if DEBUG_MODE:
                log(resp.text[:500], 'DEBUG')
            raise Exception('Cookie 已过期，请更新 KATA_COOKIES')
        
        log('登录状态正常', 'SUCCESS')
        
        # 调用 API 获取服务器列表
        resp = self.get('/api-client/list-servers', json_resp=True)
        
        if DEBUG_MODE:
            log(f'API 响应: {resp.text[:200]}', 'DEBUG')
        
        try:
            servers = resp.json()
        except:
            raise Exception(f'API 返回非 JSON: {resp.text[:100]}')
        
        if not servers:
            return []
        
        return [{'id': s['id'], 'name': s.get('name', f"Server-{s['id']}")} for s in servers]

    def process_server(self, sid, name):
        log(f'处理: {name} (ID: {sid})')
        
        # 获取服务器页面
        self.session.headers['referer'] = f'{self.base}/dashboard'
        resp = self.get(f'/servers/edit?id={sid}')
        html = resp.text
        
        if not self.check_login(html, resp.url):
            return {'name': name, 'action': 'error', 'msg': 'Cookie过期', 'ok': False}
        
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
            log('  未找到 CSRF token', 'ERROR')
            if DEBUG_MODE:
                # 保存页面以便调试
                with open('/tmp/server_page.html', 'w') as f:
                    f.write(html)
                log('  页面已保存到 /tmp/server_page.html', 'DEBUG')
            return {'name': name, 'action': 'error', 'msg': '无CSRF', 'ok': False}
        csrf = m.group(1)
        
        # 执行续订
        log(f'  执行续订...')
        self.session.headers['referer'] = f'{self.base}/servers/edit?id={sid}'
        resp = self.post(f'/api-client/renew?id={sid}', {'csrf': csrf})
        url = str(resp.url)
        
        if 'renew=success' in url:
            # 获取新到期时间
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={sid}')
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', resp2.text)
            new_expiry = m2.group(1) if m2 else '?'
            log(f'  续订成功！新到期: {new_expiry}', 'SUCCESS')
            return {'name': name, 'old': expiry, 'new': new_expiry, 'action': 'renewed', 'ok': True}
        
        if 'renew-error=' in url:
            m = re.search(r'renew-error=([^&]+)', url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知'
            log(f'  {msg}', 'WARNING')
            if 'not yet' in msg.lower() or "can't" in msg.lower():
                return {'name': name, 'expiry': expiry, 'action': 'not_yet', 'msg': msg, 'ok': True}
            return {'name': name, 'action': 'failed', 'msg': msg, 'ok': False}
        
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
        
        # 汇总
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
