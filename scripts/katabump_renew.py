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
RENEW_THRESHOLD_DAYS = 1  # 到期前1天才能续订


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
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/144.0.0.0 Safari/537.36',
        })
        
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path, json_resp=False):
        headers = {'accept': 'application/json'} if json_resp else {}
        return self.session.get(f'{self.base}{path}', headers=headers, timeout=60)

    def post(self, path, data):
        headers = {'content-type': 'application/x-www-form-urlencoded', 'origin': self.base}
        return self.session.post(f'{self.base}{path}', data=data, headers=headers, timeout=60)

    def get_servers(self):
        self.get('/dashboard')
        resp = self.get('/api-client/list-servers', json_resp=True)
        if resp.text.strip().startswith('<'):
            raise Exception('Cookie 已过期')
        servers = resp.json()
        return [{'id': s['id'], 'name': s.get('name', f"Server-{s['id']}")} for s in servers] if servers else []

    def process_server(self, sid, name):
        log(f'处理: {name} (ID: {sid})')
        
        resp = self.get(f'/servers/edit?id={sid}')
        html = resp.text
        
        # 获取到期时间
        m = re.search(r'(\d{4}-\d{2}-\d{2})', html)
        expiry = m.group(1) if m else None
        days = days_until(expiry)
        
        log(f'  到期: {expiry or "?"} | 剩余: {days if days is not None else "?"} 天')
        
        # 判断是否需要续订（到期前1天才能续订）
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            log(f'  剩余 {days} 天，暂不能续订（需 ≤{RENEW_THRESHOLD_DAYS} 天）', 'SUCCESS')
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 获取 CSRF
        m = re.search(r'name="csrf"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf"', html)
        if not m:
            return {'name': name, 'action': 'error', 'msg': '无CSRF', 'ok': False}
        csrf = m.group(1)
        
        # 执行续订
        log(f'  尝试续订...')
        self.session.headers['referer'] = f'{self.base}/servers/edit?id={sid}'
        resp = self.post(f'/api-client/renew?id={sid}', {'csrf': csrf})
        url = str(resp.url)
        
        # 检查结果
        if 'renew=success' in url:
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={sid}')
            m2 = re.search(r'(\d{4}-\d{2}-\d{2})', resp2.text)
            new_expiry = m2.group(1) if m2 else '?'
            new_days = days_until(new_expiry)
            log(f'  ✅ 续订成功！{expiry} → {new_expiry} ({new_days}天)', 'SUCCESS')
            return {'name': name, 'old': expiry, 'new': new_expiry, 'days': new_days, 'action': 'renewed', 'ok': True}
        
        if 'renew-error=' in url:
            m = re.search(r'renew-error=([^&]+)', url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            # 提取可续订日期
            date_match = re.search(r'as of (\d+ \w+)', msg)
            renew_date = date_match.group(1) if date_match else ''
            log(f'  ⏳ {renew_date} 可续订', 'WARNING')
            return {'name': name, 'expiry': expiry, 'days': days, 'action': 'not_yet', 'msg': msg, 'ok': True}
        
        log(f'  结果未知: {url}', 'WARNING')
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
        
        # 构建通知
        msg = ['📋 <b>KataBump 续订报告</b>', '']
        
        if renewed:
            msg.append('✅ <b>已续订:</b>')
            for r in renewed:
                msg.append(f"  • {r['name']}: {r.get('old')} → {r.get('new')}")
            msg.append('')
        
        if skipped:
            msg.append('📋 <b>无需续订:</b>')
            for r in skipped:
                msg.append(f"  • {r['name']}: {r.get('expiry')} (剩余{r.get('days')}天)")
            msg.append('')
        
        if not_yet:
            msg.append('⏳ <b>暂不能续订:</b>')
            for r in not_yet:
                msg.append(f"  • {r['name']}: {r.get('expiry')} (剩余{r.get('days')}天)")
            msg.append('')
        
        if failed:
            msg.append('❌ <b>失败:</b>')
            for r in failed:
                msg.append(f"  • {r['name']}: {r.get('msg', '未知错误')}")
        
        tg_notify('\n'.join(msg))
        
        log('=' * 40)
        log(f'完成: 续订 {len(renewed)}, 跳过 {len(skipped)}, 待续 {len(not_yet)}, 失败 {len(failed)}', 'SUCCESS')
        log('=' * 40)
        
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
