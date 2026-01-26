#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本
"""

import os
import sys
import re
import time
import random
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote

# ================= 配置 =================

KATA_COOKIES = os.environ.get('KATA_COOKIES', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'
RENEW_THRESHOLD_DAYS = 2


def log(msg, level='INFO'):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    prefix = {'INFO': '📋', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌'}
    print(f'[{t}] {prefix.get(level, "📋")} {msg}')


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


def get_expiry(html):
    for pattern in [r'Expiry[\s\S]{0,100}?(\d{4}-\d{2}-\d{2})', r'>(\d{4}-\d{2}-\d{2})<']:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def get_csrf(html):
    m = re.search(r'name="csrf"[^>]*value="([^"]+)"', html) or re.search(r'value="([^"]+)"[^>]*name="csrf"', html)
    return m.group(1) if m else None


class KataBumpRenewer:
    def __init__(self):
        self.base_url = 'https://dashboard.katabump.com'
        
        self.headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-encoding': 'gzip, deflate',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'cache-control': 'no-cache',
            'cookie': KATA_COOKIES,
            'pragma': 'no-cache',
            'referer': 'https://dashboard.katabump.com/dashboard',
            'sec-ch-ua': '"Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
        }
        
        self.proxies = None
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path, json_response=False):
        url = f'{self.base_url}{path}'
        headers = self.headers.copy()
        if json_response:
            headers['accept'] = 'application/json, text/plain, */*'
            headers['sec-fetch-dest'] = 'empty'
            headers['sec-fetch-mode'] = 'cors'
        return requests.get(url, headers=headers, proxies=self.proxies, timeout=60)

    def post(self, path, data):
        url = f'{self.base_url}{path}'
        headers = self.headers.copy()
        headers['content-type'] = 'application/x-www-form-urlencoded'
        headers['origin'] = self.base_url
        return requests.post(url, data=data, headers=headers, proxies=self.proxies, timeout=60)

    def get_servers(self):
        log('获取服务器列表...')
        
        resp = self.get('/dashboard')
        if '/auth/login' in str(resp.url) or 'name="password"' in resp.text:
            raise Exception('Cookie 已过期，请更新 KATA_COOKIES')
        
        resp = self.get('/api-client/list-servers', json_response=True)
        
        try:
            servers = resp.json()
        except:
            raise Exception('API 返回非 JSON 数据')
        
        if not isinstance(servers, list):
            raise Exception(f'API 返回格式错误')
        
        if not servers:
            log('没有服务器', 'WARNING')
            return []
        
        log(f'找到 {len(servers)} 个服务器', 'SUCCESS')
        
        result = []
        for s in servers:
            server_info = {
                'id': s.get('id'),
                'name': s.get('name', f"Server-{s.get('id')}"),
                'location': s.get('location', '?'),
            }
            log(f"  - {server_info['id']}: {server_info['name']} ({server_info['location']})")
            result.append(server_info)
        
        return result

    def process_server(self, server_info):
        server_id = server_info['id']
        name = server_info['name']
        
        log(f'')
        log(f'━━━ {name} (ID: {server_id}) ━━━')
        
        self.headers['referer'] = f'{self.base_url}/dashboard'
        resp = self.get(f'/servers/edit?id={server_id}')
        html = resp.text
        
        if '/auth/login' in str(resp.url):
            return {'id': server_id, 'name': name, 'action': 'error', 'msg': 'Cookie 过期', 'ok': False}
        
        expiry = get_expiry(html)
        days = days_until(expiry)
        
        log(f'到期: {expiry or "未知"} | 剩余: {days if days is not None else "?"} 天')
        
        # 判断是否需要续订
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            log('无需续订', 'SUCCESS')
            return {'id': server_id, 'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 执行续订
        log('执行续订...')
        csrf = get_csrf(html)
        if not csrf:
            log('无法获取 CSRF token', 'ERROR')
            return {'id': server_id, 'name': name, 'action': 'error', 'msg': '无法获取 CSRF', 'ok': False}
        
        self.headers['referer'] = f'{self.base_url}/servers/edit?id={server_id}'
        resp = self.post(f'/api-client/renew?id={server_id}', {'csrf': csrf})
        final_url = str(resp.url)
        
        # 检查结果
        if 'renew=success' in final_url:
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={server_id}')
            new_expiry = get_expiry(resp2.text) or '?'
            log(f'续订成功！新到期: {new_expiry}', 'SUCCESS')
            return {
                'id': server_id, 'name': name, 
                'old_expiry': expiry, 'new_expiry': new_expiry,
                'action': 'renewed', 'ok': True
            }
        
        if 'renew-error=' in final_url:
            m = re.search(r'renew-error=([^&]+)', final_url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            log(f'续订失败: {msg}', 'WARNING')
            
            # 暂不能续订 - 返回成功状态
            if "can't renew" in msg.lower() or 'not yet' in msg.lower():
                log('暂不能续订', 'INFO')
                return {
                    'id': server_id, 'name': name, 
                    'expiry': expiry, 'days': days,
                    'action': 'not_yet', 'msg': msg, 'ok': True
                }
            
            return {'id': server_id, 'name': name, 'action': 'failed', 'msg': msg, 'ok': False}
        
        log('续订结果未知', 'WARNING')
        return {'id': server_id, 'name': name, 'action': 'unknown', 'ok': False}

    def run(self):
        log('=' * 50)
        log('KataBump 自动续订')
        log('=' * 50)
        
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        if FORCE_RENEW:
            log('强制续订模式', 'WARNING')
        
        servers = self.get_servers()
        
        if not servers:
            log('没有服务器需要处理')
            tg_notify('📋 <b>KataBump</b>\n\n没有服务器')
            return True
        
        results = []
        for i, server_info in enumerate(servers):
            if i > 0:
                time.sleep(random.uniform(2, 4))
            results.append(self.process_server(server_info))
        
        # 汇总
        log('')
        log('=' * 50)
        log('完成')
        
        renewed = [r for r in results if r['action'] == 'renewed']
        skipped = [r for r in results if r['action'] == 'skip']
        not_yet = [r for r in results if r['action'] == 'not_yet']
        failed = [r for r in results if r['action'] in ('failed', 'error', 'unknown')]
        
        # 构建通知消息
        msg_lines = ['📋 <b>KataBump 续订报告</b>']
        
        if renewed:
            msg_lines.append('')
            for r in renewed:
                msg_lines.append(f"✅ {r['name']}: {r.get('old_expiry', '?')} → {r.get('new_expiry', '?')}")
        
        if skipped:
            msg_lines.append('')
            for r in skipped:
                days_str = f"({r.get('days')}天)" if r.get('days') is not None else ""
                msg_lines.append(f"📋 {r['name']}: {r.get('expiry', '?')} {days_str}")
        
        if not_yet:
            msg_lines.append('')
            for r in not_yet:
                days_str = f"({r.get('days')}天)" if r.get('days') is not None else ""
                msg_lines.append(f"ℹ️ {r['name']}: {r.get('expiry', '?')} {days_str} - 暂不能续订")
        
        if failed:
            msg_lines.append('')
            for r in failed:
                msg_lines.append(f"❌ {r.get('name', r['id'])}: {r.get('msg', '未知错误')}")
        
        tg_notify('\n'.join(msg_lines))
        
        return len(failed) == 0


def main():
    try:
        ok = KataBumpRenewer().run()
        log('🏁 结束')
        sys.exit(0 if ok else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n{e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
