#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本
使用 cf_clearance Cookie 绕过 Cloudflare Turnstile 验证
"""

import os
import sys
import re
import time
import random
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode, unquote

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
        # Telegram 不走代理
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
    """从 HTML 中提取到期日期"""
    patterns = [
        r'<div[^>]*class="col-lg-3[^"]*"[^>]*>\s*Expiry\s*</div>\s*<div[^>]*class="col-lg-9[^"]*"[^>]*>(\d{4}-\d{2}-\d{2})</div>',
        r'Expiry</div>\s*<div[^>]*>(\d{4}-\d{2}-\d{2})',
        r'>Expiry<[\s\S]*?>(\d{4}-\d{2}-\d{2})<',
        r'Expiry[\s\S]{0,100}?(\d{4}-\d{2}-\d{2})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            return match.group(1)
    return None


def get_server_info(html):
    """从 HTML 提取服务器信息"""
    info = {}
    
    # 服务器名称
    patterns = [
        r'<div[^>]*>\s*Name\s*</div>\s*<div[^>]*>([^<]+)</div>',
        r'>Name<[\s\S]*?>([^<]+)<',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            info['name'] = match.group(1).strip()
            break
    
    # 标识符
    patterns = [
        r'<div[^>]*>\s*Identifier\s*</div>\s*<div[^>]*>([^<]+)</div>',
        r'>Identifier<[\s\S]*?>([a-f0-9]+)<',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            info['identifier'] = match.group(1).strip()
            break
    
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
    patterns = [
        r'<input[^>]*name="csrf"[^>]*value="([^"]+)"',
        r'<input[^>]*value="([^"]+)"[^>]*name="csrf"',
        r'"csrf"\s*:\s*"([^"]+)"',
        r"'csrf'\s*:\s*'([^']+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return match.group(1)
    return None


def check_renew_result(url, html=''):
    """检查续订结果"""
    # 从 URL 检查
    if 'renew=success' in url:
        return 'success', 'Your service has been renewed.'
    
    match = re.search(r'renew-error=([^&]+)', url)
    if match:
        error_msg = unquote(match.group(1).replace('+', ' '))
        return 'error', error_msg
    
    # 从 HTML 检查
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
        self.base_headers = {
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
        self.session = requests.Session()
        self.session.headers.update(self.base_headers)
        
        # 设置代理
        proxy = os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY')
        if proxy:
            self.session.proxies = {
                'http': proxy,
                'https': proxy
            }
            log(f'使用代理: {proxy}', 'DEBUG' if DEBUG_MODE else 'INFO')
        
        # 预设 cf_clearance Cookie（用于绕过 Turnstile）
        if CF_CLEARANCE:
            self.session.cookies.set('cf_clearance', CF_CLEARANCE, domain='dashboard.katabump.com')
            log('已预设 cf_clearance Cookie', 'DEBUG' if DEBUG_MODE else 'INFO')
    
    def login(self):
        """登录 KataBump"""
        log('正在登录...')
        
        # 第一步：获取登录页面
        login_url = f'{DASHBOARD_URL}/auth/login'
        resp = self.session.get(login_url, timeout=60)
        
        if resp.status_code != 200:
            raise Exception(f'无法访问登录页面: {resp.status_code}')
        
        if DEBUG_MODE:
            log(f'登录页面 URL: {resp.url}', 'DEBUG')
            log(f'Cookies: {dict(self.session.cookies)}', 'DEBUG')
        
        # 检查是否已登录（被重定向到 dashboard）
        if '/dashboard' in str(resp.url) or 'logout' in resp.text.lower():
            log('已处于登录状态', 'SUCCESS')
            return True
        
        # 提取 CSRF token
        csrf = extract_csrf_token(resp.text)
        if DEBUG_MODE:
            log(f'CSRF Token: {csrf[:20] if csrf else "None"}...', 'DEBUG')
        
        # 第二步：提交登录表单
        login_data = {
            'email': KATA_EMAIL,
            'password': KATA_PASSWORD,
        }
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
            log(f'登录后 URL: {resp.url}', 'DEBUG')
            log(f'登录后 Cookies: {dict(self.session.cookies)}', 'DEBUG')
        
        # 检查登录结果
        final_url = str(resp.url)
        
        if '/auth/login' in final_url:
            # 还在登录页面，检查错误
            if 'error' in resp.text.lower() or 'invalid' in resp.text.lower():
                raise Exception('登录失败：邮箱或密码错误')
            if 'turnstile' in resp.text.lower() or 'captcha' in resp.text.lower():
                raise Exception('登录失败：需要验证码，请更新 cf_clearance')
            raise Exception('登录失败：未知原因')
        
        if 'logout' in resp.text.lower() or '/dashboard' in final_url:
            log('登录成功', 'SUCCESS')
            return True
        
        # 尝试访问 dashboard 确认登录状态
        resp = self.session.get(f'{DASHBOARD_URL}/dashboard', timeout=60)
        if 'logout' in resp.text.lower():
            log('登录成功', 'SUCCESS')
            return True
        
        raise Exception('登录失败：无法确认登录状态')
    
    def get_server_page(self):
        """获取服务器页面"""
        server_url = f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}'
        log(f'获取服务器页面...')
        
        resp = self.session.get(server_url, timeout=60)
        
        if resp.status_code != 200:
            raise Exception(f'无法访问服务器页面: {resp.status_code}')
        
        if DEBUG_MODE:
            log(f'服务器页面 URL: {resp.url}', 'DEBUG')
            # 保存 HTML 用于调试
            with open('/tmp/server_page.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
            log('已保存页面到 /tmp/server_page.html', 'DEBUG')
        
        # 检查是否被重定向到登录页
        if '/auth/login' in str(resp.url):
            raise Exception('会话已过期，需要重新登录')
        
        return resp.text, str(resp.url)
    
    def should_renew(self, days_left):
        """判断是否应该执行续订"""
        if FORCE_RENEW:
            log('强制续订模式已启用', 'WARNING')
            return True
        
        if DEBUG_MODE:
            log(f'剩余 {days_left} 天，阈值 {RENEW_THRESHOLD_DAYS} 天', 'DEBUG')
        
        if days_left is None:
            log('无法获取剩余天数，尝试续订', 'WARNING')
            return True
        
        if days_left <= RENEW_THRESHOLD_DAYS:
            log(f'剩余 {days_left} 天 <= 阈值 {RENEW_THRESHOLD_DAYS} 天，执行续订')
            return True
        
        log(f'剩余 {days_left} 天 > 阈值 {RENEW_THRESHOLD_DAYS} 天，跳过续订')
        return False
    
    def do_renew(self, html):
        """执行续订"""
        log('正在执行续订...')
        
        # 提取 CSRF token（从 renew modal 中）
        # 查找 renew-modal 中的 csrf
        modal_match = re.search(r'id="renew-modal"[\s\S]*?name="csrf"[^>]*value="([^"]+)"', html, re.I)
        if modal_match:
            csrf = modal_match.group(1)
        else:
            # 备用：从整个页面提取
            csrf = extract_csrf_token(html)
        
        if not csrf:
            if DEBUG_MODE:
                log('HTML 片段:', 'DEBUG')
                log(html[:2000], 'DEBUG')
            raise Exception('无法提取 CSRF token')
        
        if DEBUG_MODE:
            log(f'续订 CSRF: {csrf[:30]}...', 'DEBUG')
        
        # 构建续订请求
        renew_url = f'{DASHBOARD_URL}/api-client/renew?id={SERVER_ID}'
        
        # 准备表单数据
        # cf_clearance 会自动通过 Cookie 发送，绕过 Turnstile
        form_data = {
            'csrf': csrf,
        }
        
        if DEBUG_MODE:
            log(f'续订 URL: {renew_url}', 'DEBUG')
            log(f'表单数据: {form_data}', 'DEBUG')
        
        # 发送续订请求
        resp = self.session.post(
            renew_url,
            data=form_data,
            headers={
                **self.base_headers,
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': DASHBOARD_URL,
                'Referer': f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}',
            },
            timeout=60,
            allow_redirects=True
        )
        
        if DEBUG_MODE:
            log(f'续订响应 URL: {resp.url}', 'DEBUG')
            log(f'续订响应状态: {resp.status_code}', 'DEBUG')
            # 保存响应
            with open('/tmp/renew_response.html', 'w', encoding='utf-8') as f:
                f.write(resp.text)
            log('已保存响应到 /tmp/renew_response.html', 'DEBUG')
        
        # 检查结果
        final_url = str(resp.url)
        result, message = check_renew_result(final_url, resp.text)
        
        if result == 'success':
            return True, message
        elif result == 'error':
            return False, message
        
        # 进一步检查响应内容
        if 'alert-success' in resp.text:
            return True, 'Your service has been renewed.'
        
        if 'alert-danger' in resp.text or 'alert-warning' in resp.text:
            # 提取错误消息
            match = re.search(r'alert[^>]*>([^<]+)<', resp.text)
            if match:
                return False, match.group(1).strip()
        
        # 检查是否需要 Turnstile
        if 'turnstile' in resp.text.lower() or 'captcha' in resp.text.lower():
            return False, '需要 Turnstile 验证，请更新 cf_clearance Cookie'
        
        return None, '续订结果未知，请手动检查'
    
    def run(self):
        """主运行流程"""
        log('=' * 50)
        log('KataBump 自动续订')
        log('=' * 50)
        
        if DEBUG_MODE:
            log('🔧 调试模式已启用', 'DEBUG')
        if FORCE_RENEW:
            log('🔧 强制续订已启用', 'WARNING')
        
        log(f'服务器 ID: {SERVER_ID}')
        
        if not SERVER_ID:
            raise Exception('未设置 KATA_SERVER_ID')
        if not KATA_EMAIL or not KATA_PASSWORD:
            raise Exception('未设置账号信息')
        if not CF_CLEARANCE:
            log('未设置 cf_clearance，可能无法绕过 Turnstile', 'WARNING')
        
        # 初始化会话
        self.init_session()
        
        try:
            # 登录
            self.login()
            
            # 随机延迟，模拟人类行为
            delay = random.uniform(1, 3)
            if DEBUG_MODE:
                log(f'等待 {delay:.1f} 秒...', 'DEBUG')
            time.sleep(delay)
            
            # 获取服务器页面
            html, current_url = self.get_server_page()
            
            # 检查 URL 中是否有续订结果
            result, message = check_renew_result(current_url, html)
            if result == 'success':
                log(f'检测到已续订: {message}', 'SUCCESS')
            
            # 提取服务器信息
            server_info = get_server_info(html)
            expiry = server_info.get('expiry')
            days_left = days_until(expiry)
            
            log(f'服务器名称: {server_info.get("name", "未知")}')
            log(f'标识符: {server_info.get("identifier", "未知")}')
            log(f'到期日期: {expiry or "未知"}')
            log(f'剩余天数: {days_left if days_left is not None else "未知"}')
            
            # 判断是否需要续订
            if not self.should_renew(days_left):
                msg = (
                    f'📋 <b>KataBump 状态检查</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'到期: {expiry or "未知"}\n'
                    f'剩余: {days_left} 天\n\n'
                    f'✅ 无需续订'
                )
                tg_notify(msg)
                return True
            
            # 执行续订
            old_expiry = expiry
            success, message = self.do_renew(html)
            
            if success:
                # 重新获取页面确认新到期日期
                time.sleep(2)
                html, _ = self.get_server_page()
                new_expiry = get_expiry_from_html(html) or '未知'
                new_days = days_until(new_expiry)
                
                log(f'续订成功！新到期日期: {new_expiry}', 'SUCCESS')
                
                tg_notify(
                    f'✅ <b>KataBump 续订成功</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'原到期: {old_expiry or "未知"}\n'
                    f'新到期: {new_expiry}\n'
                    f'剩余: {new_days} 天'
                )
                return True
            
            elif success is False:
                log(f'续订失败: {message}', 'WARNING')
                
                # 检查是否是"还不能续订"的错误
                if message and ("can't renew" in message.lower() or 'not yet' in message.lower() or 'able to' in message.lower()):
                    log('服务器还不能续订，可能还有足够的时间')
                    tg_notify(
                        f'📋 <b>KataBump 续订提示</b>\n\n'
                        f'服务器: {server_info.get("name", SERVER_ID)}\n'
                        f'到期: {expiry or "未知"}\n\n'
                        f'ℹ️ {message}'
                    )
                    return True  # 不算失败
                
                tg_notify(
                    f'⚠️ <b>KataBump 续订失败</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'到期: {expiry or "未知"}\n\n'
                    f'❌ {message}'
                )
                return False
            
            else:
                log(f'续订结果未知: {message}', 'WARNING')
                tg_notify(
                    f'⚠️ <b>KataBump 续订状态未知</b>\n\n'
                    f'服务器: {server_info.get("name", SERVER_ID)}\n'
                    f'到期: {expiry or "未知"}\n\n'
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
        import traceback
        if DEBUG_MODE:
            traceback.print_exc()
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n服务器: {SERVER_ID}\n错误: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
