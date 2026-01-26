#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本
自动从 dashboard 获取服务器列表并续订
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
KATA_EMAIL = os.environ.get('KATA_EMAIL', '')
KATA_PASSWORD = os.environ.get('KATA_PASSWORD', '')
CF_CLEARANCE = os.environ.get('CF_CLEARANCE', '')

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
    match = re.search(r'<div[^>]*>\s*Identifier\s*</div>\s*<div[^>]*>([^<]+)</div>', html, re.I | re.S)
    if match:
        info['identifier'] = match.group(1).strip()
    info['expiry'] = get_expiry_from_html(html)
    match = re.search(r'Every\s+(\d+)\s+days', html, re.I)
    if match:
        info['renew_period'] = int(match.group(1))
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
        if 'alert-success' in html and 'renewed' in html.lower():
            return 'success', 'Your service has been renewed.'
    return 'unknown', None


# ================= 主类 =================

class KataBumpRenewer:
    def __init__(self):
        self.session = None
        self.logged_in = False
        self.servers = []  # 服务器列表
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
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {'http': proxy, 'https': proxy}
            log(f'使用代理: {proxy}', 'DEBUG' if DEBUG_MODE else 'INFO')
        if CF_CLEARANCE:
            self.session.cookies.set('cf_clearance', CF_CLEARANCE, domain='.katabump.com')
            log('已预设 cf_clearance Cookie', 'DEBUG' if DEBUG_MODE else 'INFO')

    def parse_servers_from_dashboard(self, html):
        """从 dashboard 页面解析服务器列表"""
        servers = []
        # 匹配表格中的服务器行
        # <tr>
        #     <td>185829</td>
        #     <td>www</td>
        #     <td>Gravelines (FR)</td>
        #     <td>NodeJs</td>
        #     <td>308 MB</td>
        #     <td>716 MB</td>
        #     <td>25%</td>
        #     <td><a href="https://dashboard.katabump.com/servers/edit?id=xxxxx">See</a></td>
        # </tr>
        
        pattern = r'<tr>\s*<td>(\d+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td><a href="[^"]*servers/edit\?id=(\d+)"'
        
        matches = re.findall(pattern, html, re.I | re.S)
        
        for match in matches:
            server = {
                'id': match[7],  # 从链接中提取的 ID
                'name': match[1].strip(),
                'location': match[2].strip(),
                'type': match[3].strip(),
                'ram': match[4].strip(),
                'disk': match[5].strip(),
                'cpu': match[6].strip(),
            }
            servers.append(server)
        
        # 备用方法：只提取链接中的 ID
        if not servers:
            id_pattern = r'href="[^"]*servers/edit\?id=(\d+)"[^>]*>See</a>'
            ids = re.findall(id_pattern, html, re.I)
            for server_id in ids:
                servers.append({'id': server_id, 'name': f'Server-{server_id}'})
        
        return servers

    def login(self):
        log('正在登录...')
        login_url = f'{DASHBOARD_URL}/auth/login'

        # 获取登录页面
        resp = self.session.get(login_url, timeout=60, allow_redirects=True)

        if DEBUG_MODE:
            log(f'登录页面状态: {resp.status_code}', 'DEBUG')
            log(f'登录页面 URL: {resp.url}', 'DEBUG')

        # 如果已经被重定向到 dashboard，说明已登录
        if '/dashboard' in str(resp.url) or '/servers/edit' in str(resp.url):
            log('已处于登录状态', 'SUCCESS')
            self.logged_in = True
            # 解析服务器列表
            if '/dashboard' in str(resp.url):
                self.servers = self.parse_servers_from_dashboard(resp.text)
            return True

        # 检查 Cloudflare 挑战
        if 'challenge-platform' in resp.text or 'Just a moment' in resp.text:
            raise Exception('遇到 Cloudflare 挑战，请更新 cf_clearance Cookie')

        # 提取 CSRF token
        csrf = extract_csrf_token(resp.text)
        if DEBUG_MODE:
            log(f'CSRF Token: {csrf[:30] if csrf else "None"}...', 'DEBUG')

        # 提交登录
        login_data = {'email': KATA_EMAIL, 'password': KATA_PASSWORD}
        if csrf:
            login_data['csrf'] = csrf

        resp = self.session.post(
            login_url,
            data=login_data,
            headers={
                **self.base_headers,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': DASHBOARD_URL,
                'Referer': login_url,
            },
            timeout=60,
            allow_redirects=True
        )

        if DEBUG_MODE:
            log(f'登录后状态: {resp.status_code}', 'DEBUG')
            log(f'登录后 URL: {resp.url}', 'DEBUG')
            with open('/tmp/login_response.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)

        final_url = str(resp.url)

        # 登录成功：URL 变为 dashboard 或 servers/edit
        if '/dashboard' in final_url or '/servers/edit' in final_url:
            log('登录成功', 'SUCCESS')
            self.logged_in = True
            # 解析服务器列表
            if '/dashboard' in final_url:
                self.servers = self.parse_servers_from_dashboard(resp.text)
            return True

        # 还在登录页面，检查错误
        if '/auth/login' in final_url:
            if 'Invalid' in resp.text or 'incorrect' in resp.text.lower():
                raise Exception('登录失败：邮箱或密码错误')
            if 'turnstile' in resp.text.lower() or 'captcha' in resp.text.lower():
                raise Exception('登录需要验证码，请更新 cf_clearance Cookie')

        raise Exception('登录失败：无法确认登录状态')

    def get_dashboard(self):
        """获取 dashboard 页面并解析服务器列表"""
        log('获取 Dashboard...')
        resp = self.session.get(f'{DASHBOARD_URL}/dashboard', timeout=60, allow_redirects=True)
        
        if DEBUG_MODE:
            log(f'Dashboard URL: {resp.url}', 'DEBUG')
            with open('/tmp/dashboard.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
        
        if '/auth/login' in str(resp.url):
            raise Exception('会话已过期')
        
        self.servers = self.parse_servers_from_dashboard(resp.text)
        return resp.text

    def get_server_page(self, server_id):
        """获取服务器详情页面"""
        server_url = f'{DASHBOARD_URL}/servers/edit?id={server_id}'
        log(f'获取服务器页面: {server_id}')

        resp = self.session.get(server_url, timeout=60, allow_redirects=True)

        if DEBUG_MODE:
            log(f'服务器页面 URL: {resp.url}', 'DEBUG')
            with open(f'/tmp/server_{server_id}.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)

        if '/auth/login' in str(resp.url):
            raise Exception('会话已过期')

        return resp.text, str(resp.url)

    def should_renew(self, days_left):
        if FORCE_RENEW:
            return True
        if days_left is None:
            return True
        return days_left <= RENEW_THRESHOLD_DAYS

    def do_renew(self, server_id, html):
        """执行续订"""
        log(f'正在续订服务器 {server_id}...')

        # 提取 CSRF token
        modal_match = re.search(r'id="renew-modal"[\s\S]*?</form>', html, re.I)
        if modal_match:
            csrf = extract_csrf_token(modal_match.group(0))
        else:
            csrf = extract_csrf_token(html)

        if not csrf:
            raise Exception('无法提取 CSRF token')

        if DEBUG_MODE:
            log(f'续订 CSRF: {csrf[:30]}...', 'DEBUG')

        renew_url = f'{DASHBOARD_URL}/api-client/renew?id={server_id}'
        form_data = {'csrf': csrf}

        resp = self.session.post(
            renew_url,
            data=form_data,
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

        final_url = str(resp.url)
        result, message = check_renew_result(final_url, resp.text)

        if result == 'success':
            return True, message
        elif result == 'error':
            return False, message

        if 'turnstile' in resp.text.lower() and 'cf-turnstile' in resp.text:
            return False, '需要 Turnstile 验证，请更新 cf_clearance Cookie'

        return None, '续订结果未知'

    def process_server(self, server):
        """处理单个服务器"""
        server_id = server['id']
        server_name = server.get('name', f'Server-{server_id}')
        
        log(f'--- 处理服务器: {server_name} (ID: {server_id}) ---')
        
        try:
            # 获取服务器详情
            html, current_url = self.get_server_page(server_id)
            
            # 检查 URL 中是否有续订结果
            result, message = check_renew_result(current_url, html)
            if result == 'success':
                log(f'检测到已续订: {message}', 'SUCCESS')
            
            # 提取服务器信息
            server_info = get_server_info(html)
            expiry = server_info.get('expiry')
            days_left = days_until(expiry)
            
            log(f'服务器名称: {server_info.get("name", server_name)}')
            log(f'到期日期: {expiry or "未知"}')
            log(f'剩余天数: {days_left if days_left is not None else "未知"}')
            
            # 判断是否需要续订
            if not self.should_renew(days_left):
                log(f'剩余 {days_left} 天，无需续订', 'SUCCESS')
                return {
                    'server_id': server_id,
                    'server_name': server_info.get('name', server_name),
                    'expiry': expiry,
                    'days_left': days_left,
                    'action': 'skip',
                    'success': True,
                }
            
            # 执行续订
            if FORCE_RENEW:
                log('强制续订模式', 'WARNING')
            else:
                log(f'剩余 {days_left} 天 <= {RENEW_THRESHOLD_DAYS} 天，执行续订')
            
            old_expiry = expiry
            success, message = self.do_renew(server_id, html)
            
            if success:
                time.sleep(2)
                html, _ = self.get_server_page(server_id)
                new_expiry = get_expiry_from_html(html) or '未知'
                new_days = days_until(new_expiry)
                log(f'续订成功！新到期日期: {new_expiry}', 'SUCCESS')
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
                # 检查是否是"还不能续订"
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
                log(f'续订结果未知: {message}', 'WARNING')
                return {
                    'server_id': server_id,
                    'server_name': server_info.get('name', server_name),
                    'expiry': expiry,
                    'action': 'unknown',
                    'message': message,
                    'success': False,
                }
                
        except Exception as e:
            log(f'处理服务器 {server_id} 出错: {e}', 'ERROR')
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
            log('🔧 调试模式已启用', 'DEBUG')
        if FORCE_RENEW:
            log('🔧 强制续订已启用', 'WARNING')

        if not KATA_EMAIL or not KATA_PASSWORD:
            raise Exception('未设置账号信息')
        if not CF_CLEARANCE:
            log('未设置 cf_clearance，可能无法绕过 Turnstile', 'WARNING')

        self.init_session()

        try:
            # 登录
            self.login()

            # 如果登录时没有获取到服务器列表，单独获取
            if not self.servers:
                self.get_dashboard()

            if not self.servers:
                raise Exception('未找到任何服务器')

            log(f'找到 {len(self.servers)} 个服务器')
            for s in self.servers:
                log(f'  - {s.get("name", "Unknown")} (ID: {s["id"]})', 'DEBUG' if DEBUG_MODE else 'INFO')

            # 处理每个服务器
            results = []
            for i, server in enumerate(self.servers):
                if i > 0:
                    delay = random.uniform(2, 5)
                    if DEBUG_MODE:
                        log(f'等待 {delay:.1f} 秒...', 'DEBUG')
                    time.sleep(delay)
                
                result = self.process_server(server)
                results.append(result)

            # 汇总结果
            log('=' * 50)
            log('处理完成')
            
            renewed = [r for r in results if r['action'] == 'renewed']
            skipped = [r for r in results if r['action'] == 'skip']
            not_yet = [r for r in results if r['action'] == 'not_yet']
            failed = [r for r in results if r['action'] in ('failed', 'error', 'unknown')]
            
            # 构建通知消息
            msg_parts = ['📋 <b>KataBump 自动续订报告</b>\n']
            
            if renewed:
                msg_parts.append('\n✅ <b>已续订:</b>')
                for r in renewed:
                    msg_parts.append(f"  • {r['server_name']}: {r.get('old_expiry', '?')} → {r.get('new_expiry', '?')}")
            
            if skipped:
                msg_parts.append('\n📋 <b>无需续订:</b>')
                for r in skipped:
                    msg_parts.append(f"  • {r['server_name']}: {r.get('expiry', '?')} (剩余 {r.get('days_left', '?')} 天)")
            
            if not_yet:
                msg_parts.append('\nℹ️ <b>暂不能续订:</b>')
                for r in not_yet:
                    msg_parts.append(f"  • {r['server_name']}: {r.get('message', '')}")
            
            if failed:
                msg_parts.append('\n❌ <b>失败:</b>')
                for r in failed:
                    msg_parts.append(f"  • {r['server_name']}: {r.get('message', '未知错误')}")
            
            tg_notify('\n'.join(msg_parts))
            
            # 返回是否全部成功
            return len(failed) == 0

        finally:
            if self.session:
                self.session.close()


def main():
    try:
        renewer = KataBumpRenewer()
        success = renewer.run()
        log('🏁 结束')
        sys.exit(0 if success else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        if DEBUG_MODE:
            import traceback
            traceback.print_exc()
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n错误: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
