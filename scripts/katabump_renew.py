#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本
使用完整 Cookie 直接访问，自动获取服务器列表并续订
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

DASHBOARD_URL = 'https://dashboard.katabump.com'
KATA_COOKIES = os.environ.get('KATA_COOKIES', '')

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')

DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'

RENEW_THRESHOLD_DAYS = 2


# ================= 工具函数 =================

def log(msg, level='INFO'):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    prefix = {'INFO': '📋', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'DEBUG': '🔍'}
    print(f'[{t}] {prefix.get(level, "📋")} {msg}')


def tg_notify(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=30,
            proxies={'http': None, 'https': None}
        )
        return resp.status_code == 200
    except Exception as e:
        log(f'Telegram 通知失败: {e}', 'WARNING')
        return False


def parse_cookies(cookie_str):
    """解析 Cookie 字符串为字典"""
    cookies = {}
    if not cookie_str:
        return cookies
    for item in cookie_str.split(';'):
        item = item.strip()
        if '=' in item:
            key, value = item.split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies


def get_expiry_from_html(html):
    patterns = [
        r'<div[^>]*>\s*Expiry\s*</div>\s*<div[^>]*>(\d{4}-\d{2}-\d{2})</div>',
        r'>Expiry<[\s\S]*?>(\d{4}-\d{2}-\d{2})<',
        r'Expiry[\s\S]{0,100}?(\d{4}-\d{2}-\d{2})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            return match.group(1)
    return None


def get_server_info(html):
    info = {}
    match = re.search(r'<div[^>]*>\s*Name\s*</div>\s*<div[^>]*>([^<]+)</div>', html, re.I | re.S)
    if match:
        info['name'] = match.group(1).strip()
    match = re.search(r'<div[^>]*>\s*Identifier\s*</div>\s*<div[^>]*>([^<]+)</div>', html, re.I | re.S)
    if match:
        info['identifier'] = match.group(1).strip()
    info['expiry'] = get_expiry_from_html(html)
    return info


def days_until(date_str):
    if not date_str:
        return None
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


def extract_csrf_token(html):
    patterns = [
        r'<input[^>]*name="csrf"[^>]*value="([^"]+)"',
        r'<input[^>]*value="([^"]+)"[^>]*name="csrf"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1)
    return None


def check_renew_result(url, html=''):
    if 'renew=success' in url:
        return 'success', 'Your service has been renewed.'
    match = re.search(r'renew-error=([^&]+)', url)
    if match:
        error_msg = unquote(match.group(1).replace('+', ' '))
        return 'error', error_msg
    if html:
        if 'Your service has been renewed' in html:
            return 'success', 'Your service has been renewed.'
    return 'unknown', None


def is_login_page(url):
    return '/auth/login' in url


# ================= 主类 =================

class KataBumpRenewer:
    def __init__(self):
        self.session = None
        self.servers = []
        self.base_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def init_session(self):
        self.session = requests.Session()
        self.session.headers.update(self.base_headers)
        
        # 设置代理
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}', 'DEBUG' if DEBUG_MODE else 'INFO')
        
        # 解析并设置 Cookies
        cookies = parse_cookies(KATA_COOKIES)
        if not cookies:
            raise Exception('未设置 KATA_COOKIES')
        
        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain='.katabump.com')
        
        log(f'已加载 {len(cookies)} 个 Cookie', 'DEBUG' if DEBUG_MODE else 'INFO')
        if DEBUG_MODE:
            log(f'Cookie 名称: {list(cookies.keys())}', 'DEBUG')

    def parse_servers_from_dashboard(self, html):
        """从 dashboard 页面解析服务器列表"""
        servers = []
        
        # 匹配表格行
        pattern = r'<tr>\s*<td>(\d+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td><a href="[^"]*servers/edit\?id=(\d+)"'
        
        matches = re.findall(pattern, html, re.I | re.S)
        
        for match in matches:
            server = {
                'id': match[7],
                'name': match[1].strip(),
                'location': match[2].strip(),
                'type': match[3].strip(),
            }
            servers.append(server)
        
        # 备用方法
        if not servers:
            id_pattern = r'href="[^"]*servers/edit\?id=(\d+)"[^>]*>See</a>'
            ids = re.findall(id_pattern, html, re.I)
            for server_id in ids:
                servers.append({'id': server_id, 'name': f'Server-{server_id}'})
        
        return servers

    def get_dashboard(self):
        """获取 dashboard 页面"""
        log('获取 Dashboard...')
        resp = self.session.get(f'{DASHBOARD_URL}/dashboard', timeout=60, allow_redirects=True)
        current_url = str(resp.url)
        
        if DEBUG_MODE:
            log(f'Dashboard 状态: {resp.status_code}', 'DEBUG')
            log(f'Dashboard URL: {current_url}', 'DEBUG')
            with open('/tmp/dashboard.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
        
        # 检查是否被重定向到登录页
        if is_login_page(current_url):
            raise Exception('Cookie 已过期，请更新 KATA_COOKIES')
        
        # 检查 Cloudflare 挑战
        if 'challenge-platform' in resp.text or 'Just a moment' in resp.text:
            raise Exception('遇到 Cloudflare 挑战，请更新 Cookie（特别是 cf_clearance）')
        
        self.servers = self.parse_servers_from_dashboard(resp.text)
        log(f'成功获取 Dashboard', 'SUCCESS')
        return resp.text

    def get_server_page(self, server_id):
        """获取服务器详情页面"""
        server_url = f'{DASHBOARD_URL}/servers/edit?id={server_id}'

        resp = self.session.get(server_url, timeout=60, allow_redirects=True)
        current_url = str(resp.url)

        if DEBUG_MODE:
            log(f'服务器页面 URL: {current_url}', 'DEBUG')
            with open(f'/tmp/server_{server_id}.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)

        if is_login_page(current_url):
            raise Exception('Cookie 已过期')

        return resp.text, current_url

    def should_renew(self, days_left):
        if FORCE_RENEW:
            return True
        if days_left is None:
            return True
        return days_left <= RENEW_THRESHOLD_DAYS

    def do_renew(self, server_id, html):
        """执行续订"""
        log(f'正在续订...')

        # 提取 CSRF token
        modal_match = re.search(r'id="renew-modal"[\s\S]*?</form>', html, re.I)
        if modal_match:
            csrf = extract_csrf_token(modal_match.group(0))
        else:
            csrf = extract_csrf_token(html)

        if not csrf:
            raise Exception('无法提取 CSRF token')

        if DEBUG_MODE:
            log(f'CSRF: {csrf[:30]}...', 'DEBUG')

        renew_url = f'{DASHBOARD_URL}/api-client/renew?id={server_id}'

        resp = self.session.post(
            renew_url,
            data={'csrf': csrf},
            headers={
                **self.base_headers,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': DASHBOARD_URL,
                'Referer': f'{DASHBOARD_URL}/servers/edit?id={server_id}',
            },
            timeout=60,
            allow_redirects=True
        )

        if DEBUG_MODE:
            log(f'续订响应 URL: {resp.url}', 'DEBUG')
            with open(f'/tmp/renew_{server_id}.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)

        result, message = check_renew_result(str(resp.url), resp.text)

        if result == 'success':
            return True, message
        elif result == 'error':
            return False, message

        if 'turnstile' in resp.text.lower():
            return False, '需要 Turnstile 验证，请更新 Cookie'

        return None, '续订结果未知'

    def process_server(self, server):
        """处理单个服务器"""
        server_id = server['id']
        server_name = server.get('name', f'Server-{server_id}')
        
        log(f'')
        log(f'━━━ {server_name} (ID: {server_id}) ━━━')
        
        try:
            html, current_url = self.get_server_page(server_id)
            
            # 检查 URL 中是否有续订结果
            result, message = check_renew_result(current_url, html)
            if result == 'success':
                log(f'检测到已续订', 'SUCCESS')
            
            server_info = get_server_info(html)
            expiry = server_info.get('expiry')
            days_left = days_until(expiry)
            
            log(f'到期: {expiry or "未知"} | 剩余: {days_left if days_left is not None else "?"} 天')
            
            if not self.should_renew(days_left):
                log(f'无需续订', 'SUCCESS')
                return {
                    'server_id': server_id,
                    'server_name': server_info.get('name', server_name),
                    'expiry': expiry,
                    'days_left': days_left,
                    'action': 'skip',
                    'success': True,
                }
            
            if FORCE_RENEW:
                log('强制续订', 'WARNING')
            else:
                log(f'剩余 {days_left} 天，执行续订...')
            
            old_expiry = expiry
            success, message = self.do_renew(server_id, html)
            
            if success:
                time.sleep(2)
                html, _ = self.get_server_page(server_id)
                new_expiry = get_expiry_from_html(html) or '未知'
                new_days = days_until(new_expiry)
                log(f'续订成功！{old_expiry} → {new_expiry}', 'SUCCESS')
                return {
                    'server_id': server_id,
                    'server_name': server_info.get('name', server_name),
                    'old_expiry': old_expiry,
                    'new_expiry': new_expiry,
                    'days_left': new_days,
                    'action': 'renewed',
                    'success': True,
                }
            elif success is False:
                log(f'续订失败: {message}', 'WARNING')
                if message and ("can't renew" in message.lower() or 'not yet' in message.lower()):
                    return {
                        'server_id': server_id,
                        'server_name': server_info.get('name', server_name),
                        'expiry': expiry,
                        'days_left': days_left,
                        'action': 'not_yet',
                        'message': message,
                        'success': True,
                    }
                return {
                    'server_id': server_id,
                    'server_name': server_info.get('name', server_name),
                    'expiry': expiry,
                    'action': 'failed',
                    'message': message,
                    'success': False,
                }
            else:
                log(f'结果未知: {message}', 'WARNING')
                return {
                    'server_id': server_id,
                    'server_name': server_info.get('name', server_name),
                    'expiry': expiry,
                    'action': 'unknown',
                    'message': message,
                    'success': False,
                }
                
        except Exception as e:
            log(f'出错: {e}', 'ERROR')
            return {
                'server_id': server_id,
                'server_name': server_name,
                'action': 'error',
                'message': str(e),
                'success': False,
            }

    def run(self):
        log('=' * 50)
        log('KataBump 自动续订')
        log('=' * 50)

        if DEBUG_MODE:
            log('调试模式已启用', 'DEBUG')
        if FORCE_RENEW:
            log('强制续订已启用', 'WARNING')

        self.init_session()

        try:
            # 获取 Dashboard 和服务器列表
            self.get_dashboard()

            if not self.servers:
                raise Exception('未找到任何服务器')

            log(f'找到 {len(self.servers)} 个服务器:')
            for s in self.servers:
                log(f'  • {s.get("name")} ({s.get("location", "?")})')

            # 处理每个服务器
            results = []
            for i, server in enumerate(self.servers):
                if i > 0:
                    delay = random.uniform(2, 4)
                    time.sleep(delay)
                
                result = self.process_server(server)
                results.append(result)

            # 汇总
            log('')
            log('=' * 50)
            log('处理完成')
            
            renewed = [r for r in results if r['action'] == 'renewed']
            skipped = [r for r in results if r['action'] == 'skip']
            not_yet = [r for r in results if r['action'] == 'not_yet']
            failed = [r for r in results if r['action'] in ('failed', 'error', 'unknown')]
            
            # 构建通知
            msg_parts = ['📋 <b>KataBump 续订报告</b>']
            
            if renewed:
                msg_parts.append('\n✅ <b>已续订:</b>')
                for r in renewed:
                    msg_parts.append(f"• {r['server_name']}: {r.get('old_expiry')} → {r.get('new_expiry')}")
            
            if skipped:
                msg_parts.append('\n📋 <b>无需续订:</b>')
                for r in skipped:
                    msg_parts.append(f"• {r['server_name']}: {r.get('expiry')} ({r.get('days_left')}天)")
            
            if not_yet:
                msg_parts.append('\nℹ️ <b>暂不能续订:</b>')
                for r in not_yet:
                    msg_parts.append(f"• {r['server_name']}")
            
            if failed:
                msg_parts.append('\n❌ <b>失败:</b>')
                for r in failed:
                    msg_parts.append(f"• {r['server_name']}: {r.get('message', '?')}")
            
            tg_notify('\n'.join(msg_parts))
            
            return len(failed) == 0

        finally:
            if self.session:
                self.session.close()


def main():
    try:
        renewer = KataBumpRenewer()
        success = renewer.run()
        log('🏁 完成')
        sys.exit(0 if success else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n{e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
