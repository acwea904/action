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
        return 'success', 'renewed'
    match = re.search(r'renew-error=([^&]+)', url)
    if match:
        return 'error', unquote(match.group(1).replace('+', ' '))
    if html and 'Your service has been renewed' in html:
        return 'success', 'renewed'
    return 'unknown', None


def is_login_page(url, html=''):
    if '/auth/login' in url:
        return True
    if html and 'name="email"' in html and 'name="password"' in html:
        return True
    return False


# ================= 主类 =================

class KataBumpRenewer:
    def __init__(self):
        self.session = None
        self.servers = []

    def init_session(self):
        self.session = requests.Session()
        
        # 设置 Headers
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        # 设置代理
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}')
        
        # 解析并设置 Cookies
        if not KATA_COOKIES:
            raise Exception('未设置 KATA_COOKIES')
        
        cookie_count = 0
        for item in KATA_COOKIES.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                key = key.strip()
                value = value.strip()
                # 直接设置到 session cookies，不指定 domain
                self.session.cookies.set(key, value)
                cookie_count += 1
                if DEBUG_MODE:
                    log(f'  Cookie: {key}={value[:20]}...', 'DEBUG')
        
        log(f'已加载 {cookie_count} 个 Cookie')

    def request(self, method, url, **kwargs):
        """发送请求，手动处理重定向以便调试"""
        kwargs['allow_redirects'] = False
        kwargs.setdefault('timeout', 60)
        
        max_redirects = 10
        redirect_count = 0
        
        while redirect_count < max_redirects:
            if method.upper() == 'GET':
                resp = self.session.get(url, **kwargs)
            else:
                resp = self.session.post(url, **kwargs)
            
            if DEBUG_MODE:
                log(f'  {method} {url} -> {resp.status_code}', 'DEBUG')
            
            # 检查是否需要重定向
            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = resp.headers.get('Location', '')
                if not redirect_url:
                    break
                
                # 处理相对 URL
                if redirect_url.startswith('/'):
                    redirect_url = f'{DASHBOARD_URL}{redirect_url}'
                
                if DEBUG_MODE:
                    log(f'  重定向到: {redirect_url}', 'DEBUG')
                
                url = redirect_url
                redirect_count += 1
                
                # POST 重定向后变成 GET
                if resp.status_code in (301, 302, 303):
                    method = 'GET'
                    kwargs.pop('data', None)
            else:
                break
        
        if redirect_count >= max_redirects:
            raise Exception(f'重定向次数过多 ({redirect_count})')
        
        # 设置最终 URL
        resp.url = url
        return resp

    def parse_servers(self, html):
        """解析服务器列表"""
        servers = []
        
        # 方法1: 匹配完整表格行
        pattern = r'<tr>\s*<td>(\d+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>[\s\S]*?servers/edit\?id=(\d+)'
        matches = re.findall(pattern, html, re.I)
        for match in matches:
            servers.append({
                'id': match[4],
                'name': match[1].strip(),
                'location': match[2].strip(),
            })
        
        # 方法2: 只提取链接
        if not servers:
            pattern = r'href="[^"]*servers/edit\?id=(\d+)"[^>]*>See</a>'
            ids = re.findall(pattern, html, re.I)
            for sid in ids:
                servers.append({'id': sid, 'name': f'Server-{sid}'})
        
        return servers

    def get_dashboard(self):
        """获取 Dashboard"""
        log('获取 Dashboard...')
        
        resp = self.request('GET', f'{DASHBOARD_URL}/dashboard')
        
        if DEBUG_MODE:
            with open('/tmp/dashboard.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
        
        # 检查是否在登录页
        if is_login_page(resp.url, resp.text):
            raise Exception('Cookie 已过期，请更新 KATA_COOKIES')
        
        # 检查 Cloudflare
        if 'Just a moment' in resp.text or 'challenge-platform' in resp.text:
            raise Exception('遇到 Cloudflare 挑战，请更新 Cookie')
        
        self.servers = self.parse_servers(resp.text)
        log('Dashboard 获取成功', 'SUCCESS')
        return resp.text

    def get_server_page(self, server_id):
        """获取服务器页面"""
        resp = self.request('GET', f'{DASHBOARD_URL}/servers/edit?id={server_id}')
        
        if DEBUG_MODE:
            with open(f'/tmp/server_{server_id}.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
        
        if is_login_page(resp.url, resp.text):
            raise Exception('Cookie 已过期')
        
        return resp.text, resp.url

    def should_renew(self, days_left):
        if FORCE_RENEW:
            return True
        if days_left is None:
            return True
        return days_left <= RENEW_THRESHOLD_DAYS

    def do_renew(self, server_id, html):
        """执行续订"""
        # 提取 CSRF
        modal = re.search(r'id="renew-modal"[\s\S]*?</form>', html, re.I)
        csrf = extract_csrf_token(modal.group(0) if modal else html)
        
        if not csrf:
            raise Exception('无法提取 CSRF token')
        
        if DEBUG_MODE:
            log(f'CSRF: {csrf[:30]}...', 'DEBUG')
        
        resp = self.request(
            'POST',
            f'{DASHBOARD_URL}/api-client/renew?id={server_id}',
            data={'csrf': csrf},
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': DASHBOARD_URL,
                'Referer': f'{DASHBOARD_URL}/servers/edit?id={server_id}',
            }
        )
        
        if DEBUG_MODE:
            with open(f'/tmp/renew_{server_id}.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
        
        result, msg = check_renew_result(resp.url, resp.text)
        
        if result == 'success':
            return True, msg
        elif result == 'error':
            return False, msg
        
        if 'turnstile' in resp.text.lower():
            return False, '需要验证，请更新 Cookie'
        
        return None, '结果未知'

    def process_server(self, server):
        """处理单个服务器"""
        sid = server['id']
        name = server.get('name', f'Server-{sid}')
        
        log(f'')
        log(f'━━━ {name} (ID: {sid}) ━━━')
        
        try:
            html, url = self.get_server_page(sid)
            
            # 检查是否已续订
            result, _ = check_renew_result(url, html)
            if result == 'success':
                log('检测到已续订', 'SUCCESS')
            
            info = get_server_info(html)
            expiry = info.get('expiry')
            days_left = days_until(expiry)
            
            log(f'到期: {expiry or "?"} | 剩余: {days_left if days_left is not None else "?"} 天')
            
            if not self.should_renew(days_left):
                log('无需续订', 'SUCCESS')
                return {'id': sid, 'name': name, 'expiry': expiry, 'days': days_left, 'action': 'skip', 'ok': True}
            
            log('执行续订...')
            old_exp = expiry
            ok, msg = self.do_renew(sid, html)
            
            if ok:
                time.sleep(2)
                html, _ = self.get_server_page(sid)
                new_exp = get_expiry_from_html(html) or '?'
                log(f'续订成功！{old_exp} → {new_exp}', 'SUCCESS')
                return {'id': sid, 'name': name, 'old': old_exp, 'new': new_exp, 'action': 'renewed', 'ok': True}
            elif ok is False:
                log(f'续订失败: {msg}', 'WARNING')
                if msg and 'can\'t renew' in msg.lower():
                    return {'id': sid, 'name': name, 'expiry': expiry, 'action': 'not_yet', 'msg': msg, 'ok': True}
                return {'id': sid, 'name': name, 'action': 'failed', 'msg': msg, 'ok': False}
            else:
                log(f'结果未知: {msg}', 'WARNING')
                return {'id': sid, 'name': name, 'action': 'unknown', 'msg': msg, 'ok': False}
                
        except Exception as e:
            log(f'出错: {e}', 'ERROR')
            return {'id': sid, 'name': name, 'action': 'error', 'msg': str(e), 'ok': False}

    def run(self):
        log('=' * 50)
        log('KataBump 自动续订')
        log('=' * 50)

        if DEBUG_MODE:
            log('调试模式', 'DEBUG')
        if FORCE_RENEW:
            log('强制续订', 'WARNING')

        self.init_session()

        try:
            self.get_dashboard()

            if not self.servers:
                raise Exception('未找到服务器')

            log(f'找到 {len(self.servers)} 个服务器')
            for s in self.servers:
                log(f'  • {s["name"]} ({s.get("location", "?")})')

            results = []
            for i, server in enumerate(self.servers):
                if i > 0:
                    time.sleep(random.uniform(2, 4))
                results.append(self.process_server(server))

            # 汇总
            log('')
            log('=' * 50)
            
            renewed = [r for r in results if r['action'] == 'renewed']
            skipped = [r for r in results if r['action'] == 'skip']
            not_yet = [r for r in results if r['action'] == 'not_yet']
            failed = [r for r in results if r['action'] in ('failed', 'error', 'unknown')]
            
            # 通知
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
                    msg.append(f"• {r['name']}: {r.get('msg', '?')}")
            
            tg_notify('\n'.join(msg))
            
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
