#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本
使用 cf_clearance Cookie 绕过 Cloudflare Turnstile 验证
"""

import os
import sys
import re
import json
import time
import random
import requests
import httpx
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, quote

# ================= 配置 =================

DASHBOARD_URL = 'https://dashboard.katabump.com'
SERVER_ID = os.environ.get('KATA_SERVER_ID', '')
KATA_EMAIL = os.environ.get('KATA_EMAIL', '')
KATA_PASSWORD = os.environ.get('KATA_PASSWORD', '')
CF_CLEARANCE = os.environ.get('CF_CLEARANCE', '')

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')

DEBUG_MODE = os.environ.get('DEBUG_MODE', 'false').lower() == 'true'
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'

# 续订阈值（剩余天数 <= 此值时执行续订）
RENEW_THRESHOLD_DAYS = 2

# ================= 工具函数 =================

def log(msg, level='INFO'):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    prefix = {'INFO': '📋', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'DEBUG': '🔍'}
    print(f'[{t}] {prefix.get(level, "📋")} {msg}')


def tg_notify(message):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=30
        )
        return resp.status_code == 200
    except Exception as e:
        log(f'Telegram 通知失败: {e}', 'WARNING')
        return False


def get_expiry_from_html(html):
    """从 HTML 中提取到期日期"""
    # 匹配 Expiry 行的日期
    match = re.search(r'<div[^>]*class="col-lg-3[^"]*"[^>]*>\s*Expiry\s*</div>\s*<div[^>]*>(\d{4}-\d{2}-\d{2})</div>', html, re.I | re.S)
    if match:
        return match.group(1)
    # 备用匹配
    match = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', html, re.I)
    return match.group(1) if match else None


def get_server_info(html):
    """从 HTML 提取服务器信息"""
    info = {}
    
    # 服务器名称
    match = re.search(r'<div[^>]*class="col-lg-3[^"]*"[^>]*>\s*Name\s*</div>\s*<div[^>]*>([^<]+)</div>', html, re.I | re.S)
    if match:
        info['name'] = match.group(1).strip()
    
    # 标识符
    match = re.search(r'<div[^>]*class="col-lg-3[^"]*"[^>]*>\s*Identifier\s*</div>\s*<div[^>]*>([^<]+)</div>', html, re.I | re.S)
    if match:
        info['identifier'] = match.group(1).strip()
    
    # 到期日期
    info['expiry'] = get_expiry_from_html(html)
    
    # 续订周期
    match = re.search(r'Every\s+(\d+)\s+days', html, re.I)
    if match:
        info['renew_period'] = int(match.group(1))
    
    return info


def days_until(date_str):
    """计算距离指定日期的天数"""
    if not date_str:
        return None
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


def extract_csrf_token(html):
    """从 HTML 中提取 CSRF token"""
    match = re.search(r'<input[^>]*name="csrf"[^>]*value="([^"]+)"', html, re.I)
    return match.group(1) if match else None


def check_renew_result(url):
    """检查 URL 中的续订结果"""
    if 'renew=success' in url:
        return 'success', 'Your service has been renewed.'
    
    match = re.search(r'renew-error=([^&]+)', url)
    if match:
        from urllib.parse import unquote
        error_msg = unquote(match.group(1).replace('+', ' '))
        return 'error', error_msg
    
    return 'unknown', None


# ================= 主类 =================

class KataBumpRenewer:
    def __init__(self):
        self.session = None
        self.cookies = {}
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Sec-Ch-Ua': '"Google Chrome";v="120", "Chromium";v="120", "Not A(Brand";v="24"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Sec-Ch-Ua-Platform': '"Windows"',
        }
        
    def init_session(self):
        """初始化 HTTP 会话"""
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        
        self.session = httpx.Client(
            headers=self.headers,
            proxy=proxy,
            timeout=60.0,
            follow_redirects=True,
            verify=True
        )
        
        # 设置 cf_clearance Cookie
        if CF_CLEARANCE:
            self.cookies['cf_clearance'] = CF_CLEARANCE
            log('已加载 cf_clearance Cookie', 'DEBUG' if DEBUG_MODE else 'INFO')
    
    def login(self):
        """登录 KataBump"""
        log('正在登录...')
        
        # 获取登录页面
        login_url = f'{DASHBOARD_URL}/auth/login'
        resp = self.session.get(login_url, cookies=self.cookies)
        
        if resp.status_code != 200:
            raise Exception(f'无法访问登录页面: {resp.status_code}')
        
        # 更新 cookies
        self.cookies.update(dict(resp.cookies))
        
        # 提取 CSRF token
        csrf = extract_csrf_token(resp.text)
        if not csrf:
            log('未找到 CSRF token，可能已登录', 'DEBUG')
        
        # 检查是否已登录
        if '/dashboard' in str(resp.url) or 'logout' in resp.text.lower():
            log('已处于登录状态', 'SUCCESS')
            return True
        
        # 执行登录
        login_data = {
            'email': KATA_EMAIL,
            'password': KATA_PASSWORD,
        }
        if csrf:
            login_data['csrf'] = csrf
        
        resp = self.session.post(
            login_url,
            data=login_data,
            cookies=self.cookies,
            headers={**self.headers, 'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        self.cookies.update(dict(resp.cookies))
        
        # 检查登录结果
        if '/auth/login' in str(resp.url) and 'error' in resp.text.lower():
            raise Exception('登录失败：邮箱或密码错误')
        
        if 'logout' in resp.text.lower() or '/dashboard' in str(resp.url):
            log('登录成功', 'SUCCESS')
            return True
        
        raise Exception('登录失败：未知错误')
    
    def get_server_page(self):
        """获取服务器页面"""
        server_url = f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}'
        log(f'获取服务器页面: {server_url}')
        
        resp = self.session.get(server_url, cookies=self.cookies)
        self.cookies.update(dict(resp.cookies))
        
        if resp.status_code != 200:
            raise Exception(f'无法访问服务器页面: {resp.status_code}')
        
        return resp.text, str(resp.url)
    
    def should_renew(self, days_left):
        """判断是否应该执行续订"""
        if FORCE_RENEW:
            log('强制续订模式已启用', 'WARNING')
            return True
        
        if DEBUG_MODE:
            log(f'调试模式：剩余 {days_left} 天，阈值 {RENEW_THRESHOLD_DAYS} 天', 'DEBUG')
        
        if days_left is None:
            log('无法获取剩余天数，尝试续订', 'WARNING')
            return True
        
        if days_left <= RENEW_THRESHOLD_DAYS:
            log(f'剩余 {days_left} 天 <= 阈值 {RENEW_THRESHOLD_DAYS} 天，执行续订', 'INFO')
            return True
        
        log(f'剩余 {days_left} 天 > 阈值 {RENEW_THRESHOLD_DAYS} 天，跳过续订', 'INFO')
        return False
    
    def do_renew(self, html):
        """执行续订"""
        log('正在执行续订...')
        
        # 提取 CSRF token
        csrf = extract_csrf_token(html)
        if not csrf:
            raise Exception('无法提取 CSRF token')
        
        # 构建续订请求
        renew_url = f'{DASHBOARD_URL}/api-client/renew?id={SERVER_ID}'
        
        # 准备表单数据
        form_data = {
            'csrf': csrf,
            'cf-turnstile-response': '',  # 使用 cf_clearance 时可能不需要
        }
        
        # 发送续订请求
        resp = self.session.post(
            renew_url,
            data=form_data,
            cookies=self.cookies,
            headers={
                **self.headers,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': DASHBOARD_URL,
                'Referer': f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}',
            }
        )
        
        self.cookies.update(dict(resp.cookies))
        
        # 检查结果
        final_url = str(resp.url)
        result, message = check_renew_result(final_url)
        
        if result == 'success':
            return True, message
        elif result == 'error':
            return False, message
        
        # 检查响应内容
        if 'Your service has been renewed' in resp.text:
            return True, 'Your service has been renewed.'
        
        if 'renew-error' in resp.text or "can't renew" in resp.text.lower():
            match = re.search(r"You can't renew[^.]+\.", resp.text)
            if match:
                return False, match.group(0)
            return False, '续订被拒绝'
        
        return None, '续订结果未知'
    
    def run(self):
        """主运行流程"""
        log('=' * 50)
        log('KataBump 自动续订')
        log('=' * 50)
        
        if DEBUG_MODE:
            log('🔧 调试模式已启用', 'DEBUG')
        if FORCE_RENEW:
            log('🔧 强制续订已启用', 'DEBUG')
        
        log(f'服务器 ID: {SERVER_ID}')
        
        if not SERVER_ID:
            raise Exception('未设置 KATA_SERVER_ID')
        if not KATA_EMAIL or not KATA_PASSWORD:
            raise Exception('未设置账号信息')
        
        # 初始化会话
        self.init_session()
        
        try:
            # 登录
            self.login()
            
            # 随机延迟，模拟人类行为
            delay = random.uniform(1, 3)
            log(f'等待 {delay:.1f} 秒...', 'DEBUG')
            time.sleep(delay)
            
            # 获取服务器页面
            html, current_url = self.get_server_page()
            
            # 检查 URL 中是否有续订结果（可能是之前的请求）
            result, message = check_renew_result(current_url)
            if result == 'success':
                log(f'检测到已续订: {message}', 'SUCCESS')
            
            # 提取服务器信息
            server_info = get_server_info(html)
            expiry = server_info.get('expiry', '未知')
            days_left = days_until(expiry)
            
            log(f'服务器名称: {server_info.get("name", "未知")}')
            log(f'标识符: {server_info.get("identifier", "未知")}')
            log(f'到期日期: {expiry}')
            log(f'剩余天数: {days_left if days_left is not None else "未知"}')
            
            # 判断是否需要续订
            if not self.should_renew(days_left):
                tg_notify(
                    f'📋 <b>KataBump 状态检查</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'到期: {expiry}\n'
                    f'剩余: {days_left} 天\n\n'
                    f'✅ 无需续订'
                )
                return True
            
            # 执行续订
            old_expiry = expiry
            success, message = self.do_renew(html)
            
            if success:
                # 重新获取页面确认新到期日期
                time.sleep(2)
                html, _ = self.get_server_page()
                new_expiry = get_expiry_from_html(html) or '未知'
                
                log(f'续订成功！新到期日期: {new_expiry}', 'SUCCESS')
                
                tg_notify(
                    f'✅ <b>KataBump 续订成功</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'原到期: {old_expiry}\n'
                    f'新到期: {new_expiry}'
                )
                return True
            
            elif success is False:
                log(f'续订失败: {message}', 'WARNING')
                
                # 检查是否是"还不能续订"的错误
                if "can't renew" in message.lower() or 'not yet' in message.lower():
                    log('服务器还不能续订，可能还有足够的时间', 'INFO')
                    tg_notify(
                        f'📋 <b>KataBump 续订提示</b>\n\n'
                        f'服务器: {server_info.get("name", SERVER_ID)}\n'
                        f'到期: {expiry}\n\n'
                        f'ℹ️ {message}'
                    )
                    return True  # 不算失败
                
                tg_notify(
                    f'⚠️ <b>KataBump 续订失败</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'到期: {expiry}\n\n'
                    f'❌ {message}'
                )
                return False
            
            else:
                log(f'续订结果未知: {message}', 'WARNING')
                tg_notify(
                    f'⚠️ <b>KataBump 续订状态未知</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'到期: {expiry}\n\n'
                    f'请手动检查: {DASHBOARD_URL}/servers/edit?id={SERVER_ID}'
                )
                return False
                
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
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n服务器: {SERVER_ID}\n错误: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
