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
DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'
RENEW_THRESHOLD_DAYS = 2


def log(msg, level='INFO'):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    prefix = {'INFO': '📋', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'DEBUG': '🔍'}
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


def get_server_name(html):
    m = re.search(r'<div[^>]*>\s*Name\s*</div>\s*<div[^>]*>([^<]+)</div>', html, re.I | re.S)
    return m.group(1).strip() if m else None


class KataBumpRenewer:
    def __init__(self):
        self.base_url = 'https://dashboard.katabump.com'
        
        # 完全模拟浏览器请求头
        self.headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Cache-Control': 'no-cache',
            'Cookie': KATA_COOKIES,
            'Pragma': 'no-cache',
            'Sec-Ch-Ua': '"Not)A;Brand";v="24", "Chromium";v="116"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Upgrade-Insecure-Requests': '1',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36',
        }
        
        # 设置代理
        self.proxies = None
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')

    def get(self, path):
        """GET 请求"""
        url = f'{self.base_url}{path}'
        if DEBUG_MODE:
            log(f'GET {url}', 'DEBUG')
        
        resp = requests.get(url, headers=self.headers, proxies=self.proxies, timeout=60)
        
        if DEBUG_MODE:
            log(f'状态: {resp.status_code}', 'DEBUG')
        return resp

    def post(self, path, data, referer):
        """POST 请求"""
        url = f'{self.base_url}{path}'
        if DEBUG_MODE:
            log(f'POST {url}', 'DEBUG')
        
        headers = self.headers.copy()
        headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': self.base_url,
            'Referer': referer,
            'Sec-Fetch-Site': 'same-origin',
        })
        
        resp = requests.post(url, headers=headers, data=data, proxies=self.proxies, timeout=60)
        
        if DEBUG_MODE:
            log(f'状态: {resp.status_code}, URL: {resp.url}', 'DEBUG')
        return resp

    def get_servers(self):
        """获取服务器列表"""
        log('获取 Dashboard...')
        resp = self.get('/dashboard')
        
        if DEBUG_MODE:
            with open('/tmp/dashboard.html', 'w') as f:
                f.write(resp.text)
            log(f'页面长度: {len(resp.text)}', 'DEBUG')
        
        # 检查登录状态
        if '/auth/login' in str(resp.url) or 'name="password"' in resp.text:
            raise Exception('Cookie 已过期，请更新 KATA_COOKIES')
        
        if 'Just a moment' in resp.text:
            raise Exception('遇到 Cloudflare 挑战，请更新 Cookie')
        
        # 解析服务器 ID
        ids = re.findall(r'servers/edit\?id=(\d+)', resp.text)
        servers = list(dict.fromkeys(ids))  # 去重保持顺序
        
        if not servers:
            # 打印部分页面内容帮助调试
            if DEBUG_MODE:
                log(f'页面内容片段: {resp.text[:500]}', 'DEBUG')
            raise Exception('未找到服务器')
        
        log(f'找到 {len(servers)} 个服务器: {servers}', 'SUCCESS')
        return servers

    def process_server(self, server_id):
        """处理单个服务器"""
        log(f'')
        log(f'━━━ 服务器 {server_id} ━━━')
        
        # 获取服务器页面
        resp = self.get(f'/servers/edit?id={server_id}')
        html = resp.text
        
        if DEBUG_MODE:
            with open(f'/tmp/server_{server_id}.html', 'w') as f:
                f.write(html)
        
        if '/auth/login' in str(resp.url):
            return {'id': server_id, 'action': 'error', 'msg': 'Cookie 过期', 'ok': False}
        
        # 获取信息
        name = get_server_name(html) or f'Server-{server_id}'
        expiry = get_expiry(html)
        days = days_until(expiry)
        
        log(f'名称: {name}')
        log(f'到期: {expiry or "未知"} | 剩余: {days if days is not None else "?"} 天')
        
        # 检查 URL 是否已有续订结果
        if 'renew=success' in str(resp.url):
            log('已续订', 'SUCCESS')
        
        # 判断是否需要续订
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            log('无需续订', 'SUCCESS')
            return {'id': server_id, 'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 执行续订
        log('执行续订...')
        csrf = get_csrf(html)
        if not csrf:
            return {'id': server_id, 'name': name, 'action': 'error', 'msg': '无法获取 CSRF', 'ok': False}
        
        referer = f'{self.base_url}/servers/edit?id={server_id}'
        resp = self.post(f'/api-client/renew?id={server_id}', {'csrf': csrf}, referer)
        
        if DEBUG_MODE:
            with open(f'/tmp/renew_{server_id}.html', 'w') as f:
                f.write(resp.text)
        
        final_url = str(resp.url)
        
        # 检查结果
        if 'renew=success' in final_url:
            time.sleep(1)
            resp2 = self.get(f'/servers/edit?id={server_id}')
            new_expiry = get_expiry(resp2.text) or '?'
            log(f'续订成功！新到期: {new_expiry}', 'SUCCESS')
            return {'id': server_id, 'name': name, 'old': expiry, 'new': new_expiry, 'action': 'renewed', 'ok': True}
        
        if 'renew-error=' in final_url:
            m = re.search(r'renew-error=([^&]+)', final_url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            log(f'续订失败: {msg}', 'WARNING')
            if "can't renew" in msg.lower() or 'not yet' in msg.lower():
                return {'id': server_id, 'name': name, 'expiry': expiry, 'action': 'not_yet', 'msg': msg, 'ok': True}
            return {'id': server_id, 'name': name, 'action': 'failed', 'msg': msg, 'ok': False}
        
        log('续订结果未知', 'WARNING')
        return {'id': server_id, 'name': name, 'action': 'unknown', 'ok': False}

    def run(self):
        log('=' * 50)
        log('KataBump 自动续订')
        log('=' * 50)
        
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        if DEBUG_MODE:
            log('调试模式', 'DEBUG')
        if FORCE_RENEW:
            log('强制续订', 'WARNING')
        
        servers = self.get_servers()
        
        results = []
        for i, sid in enumerate(servers):
            if i > 0:
                time.sleep(random.uniform(2, 4))
            results.append(self.process_server(sid))
        
        # 汇总
        log('')
        log('=' * 50)
        log('完成')
        
        renewed = [r for r in results if r['action'] == 'renewed']
        skipped = [r for r in results if r['action'] == 'skip']
        not_yet = [r for r in results if r['action'] == 'not_yet']
        failed = [r for r in results if r['action'] in ('failed', 'error', 'unknown')]
        
        msg = ['📋 <b>KataBump 续订报告</b>']
        if renewed:
            msg.append('\n✅ <b>已续订:</b>')
            for r in renewed:
                msg.append(f"• {r['name']}: {r.get('old')} → {r.get('new')}")
        if skipped:
            msg.append('\n📋 <b>无需续订:</b>')
            for r in skipped:
                msg.append(f"• {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        if not_yet:
            msg.append('\nℹ️ <b>暂不能续订:</b>')
            for r in not_yet:
                msg.append(f"• {r['name']}")
        if failed:
            msg.append('\n❌ <b>失败:</b>')
            for r in failed:
                msg.append(f"• {r.get('name', r['id'])}: {r.get('msg', '?')}")
        
        tg_notify('\n'.join(msg))
        
        return len(failed) == 0


def main():
    try:
        ok = KataBumpRenewer().run()
        log('🏁 结束')
        sys.exit(0 if ok else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n{e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
