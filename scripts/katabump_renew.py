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
RENEW_THRESHOLD_DAYS = 1


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
        
        # 设置 Cookie
        for k, v in parse_cookies(KATA_COOKIES).items():
            self.session.cookies.set(k, v, domain='dashboard.katabump.com')
        
        # 完整的浏览器请求头
        self.session.headers.update({
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-encoding': 'gzip, deflate, br',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'none',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        })
        
        # 限制重定向次数
        self.session.max_redirects = 10
        
        # 设置代理
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path, json_resp=False, allow_redirects=True):
        url = f'{self.base}{path}'
        headers = {}
        if json_resp:
            headers['accept'] = 'application/json, text/plain, */*'
            headers['sec-fetch-dest'] = 'empty'
            headers['sec-fetch-mode'] = 'cors'
        
        try:
            resp = self.session.get(url, headers=headers, timeout=60, allow_redirects=allow_redirects)
            if DEBUG_MODE:
                log(f'GET {path} -> {resp.status_code}', 'DEBUG')
            return resp
        except requests.exceptions.TooManyRedirects:
            log(f'重定向过多，可能是 Cookie 失效或 CF 拦截', 'ERROR')
            raise Exception('Cookie 已过期或被 Cloudflare 拦截')

    def post(self, path, data, allow_redirects=True):
        url = f'{self.base}{path}'
        headers = {
            'content-type': 'application/x-www-form-urlencoded',
            'origin': self.base,
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
        }
        try:
            resp = self.session.post(url, data=data, headers=headers, timeout=60, allow_redirects=allow_redirects)
            if DEBUG_MODE:
                log(f'POST {path} -> {resp.status_code}, Location: {resp.headers.get("Location", "N/A")}', 'DEBUG')
            return resp
        except requests.exceptions.TooManyRedirects:
            raise Exception('Cookie 已过期或被 Cloudflare 拦截')

    def get_servers(self):
        log('获取服务器列表...')
        
        # 先访问 dashboard 建立会话
        resp = self.get('/dashboard')
        
        # 检查是否被重定向到登录页
        if '/auth/login' in str(resp.url) or 'name="password"' in resp.text:
            raise Exception('Cookie 已过期，需要重新登录')
        
        # 获取服务器列表
        resp = self.get('/api-client/list-servers', json_resp=True)
        
        if resp.text.strip().startswith('<'):
            raise Exception('Cookie 已过期')
        
        try:
            servers = resp.json()
        except:
            raise Exception(f'API 返回无效: {resp.text[:100]}')
        
        return [{'id': s['id'], 'name': s.get('name', f"Server-{s['id']}")} for s in servers] if servers else []

    def process_server(self, sid, name):
        log(f'处理: {name} (ID: {sid})')
        
        # 更新 referer
        self.session.headers['referer'] = f'{self.base}/dashboard'
        self.session.headers['sec-fetch-site'] = 'same-origin'
        
        resp = self.get(f'/servers/edit?id={sid}')
        html = resp.text
        
        # 获取到期时间
        m = re.search(r'(\d{4}-\d{2}-\d{2})', html)
        expiry = m.group(1) if m else None
        days = days_until(expiry)
        
        log(f'  到期: {expiry or "?"} | 剩余: {days if days is not None else "?"} 天')
        
        # 判断是否需要续订
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            log(f'  剩余 {days} 天，暂不能续订', 'SUCCESS')
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 获取 CSRF
        m = re.search(r'name="csrf"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf"', html)
        if not m:
            return {'name': name, 'action': 'error', 'msg': '无CSRF', 'ok': False}
        csrf = m.group(1)
        
        # 执行续订（不自动跟随重定向，手动处理）
        log(f'  尝试续订...')
        self.session.headers['referer'] = f'{self.base}/servers/edit?id={sid}'
        
        resp = self.post(f'/api-client/renew?id={sid}', {'csrf': csrf}, allow_redirects=False)
        
        # 获取重定向 URL
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resp.headers.get('Location', '')
        else:
            redirect_url = str(resp.url)
        
        log(f'  响应: {resp.status_code}, 重定向: {redirect_url}')
        
        # 检查结果
        if 'renew=success' in redirect_url:
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={sid}')
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', resp2.text)
            new_expiry = m2.group(1) if m2 else '?'
            new_days = days_until(new_expiry)
            log(f'  ✅ 续订成功！{expiry} → {new_expiry}', 'SUCCESS')
            return {'name': name, 'old': expiry, 'new': new_expiry, 'days': new_days, 'action': 'renewed', 'ok': True}
        
        if 'renew-error=' in redirect_url:
            m = re.search(r'renew-error=([^&]+)', redirect_url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            date_match = re.search(r'as of (\d+ \w+)', msg)
            renew_date = date_match.group(1) if date_match else ''
            log(f'  ⏳ {renew_date} 可续订', 'WARNING')
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'not_yet', 'msg': msg, 'ok': True}
        
        log(f'  结果未知', 'WARNING')
        return {'name': name, 'expiry': expiry, 'days': days, 'action': 'unknown', 'ok': True}

    def run(self):
        log('=' * 40)
        log('KataBump 自动续订')
        log('=' * 40)
        
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        servers = self.get_servers()
        log(f'服务器数量: {len(servers)}')
        
        if not servers:
            tg_notify('📋 KataBump: 没有服务器')
            return True
        
        results = []
        for s in servers:
            results.append(self.process_server(s['id'], s['name']))
            log('')
        
        # 汇总
        renewed = [r for r in results if r['action'] == 'renewed']
        skipped = [r for r in results if r['action'] == 'skip']
        not_yet = [r for r in results if r['action'] == 'not_yet']
        failed = [r for r in results if not r.get('ok', False)]
        
        msg = ['📋 <b>KataBump</b>', '']
        if renewed:
            for r in renewed:
                msg.append(f"✅ {r['name']}: {r.get('old')} → {r.get('new')}")
        if skipped:
            for r in skipped:
                msg.append(f"📋 {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        if not_yet:
            for r in not_yet:
                msg.append(f"⏳ {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        if failed:
            for r in failed:
                msg.append(f"❌ {r['name']}: {r.get('msg', '失败')}")
        
        tg_notify('\n'.join(msg))
        
        log('=' * 40)
        log(f'完成: 续订{len(renewed)} 跳过{len(skipped)} 待续{len(not_yet)} 失败{len(failed)}', 'SUCCESS')
        return len(failed) == 0


def main():
    try:
        ok = KataBumpRenewer().run()
        sys.exit(0 if ok else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        tg_notify(f'❌ KataBump: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
