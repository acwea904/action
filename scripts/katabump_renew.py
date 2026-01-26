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


def tg_send_document(file_path, caption=''):
    """发送文件到 Telegram"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(
                f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument',
                data={'chat_id': TG_CHAT_ID, 'caption': caption},
                files={'document': f},
                timeout=60, proxies={'http': None, 'https': None}
            )
        return resp.status_code == 200
    except Exception as e:
        log(f'发送文件失败: {e}', 'WARNING')
        return False


def tg_send_html(html_content, filename, caption=''):
    """保存 HTML 并发送到 Telegram"""
    try:
        file_path = f'/tmp/{filename}'
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        return tg_send_document(file_path, caption)
    except Exception as e:
        log(f'保存/发送 HTML 失败: {e}', 'WARNING')
        return False


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


def analyze_page(html):
    """分析页面内容，返回页面类型"""
    if 'Just a moment' in html or 'challenge-platform' in html:
        return 'cloudflare'
    if 'name="password"' in html and 'name="email"' in html:
        return 'login'
    if 'servers/edit?id=' in html:
        return 'dashboard'
    if 'Expiry' in html:
        return 'server_page'
    return 'unknown'


class KataBumpRenewer:
    def __init__(self):
        self.base_url = 'https://dashboard.katabump.com'
        self.last_html = ''
        
        # 请求头 - 只使用 gzip, deflate (不用 br, zstd)
        self.headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-encoding': 'gzip, deflate',  # 移除 br, zstd
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'cache-control': 'no-cache',
            'cookie': KATA_COOKIES,
            'pragma': 'no-cache',
            'referer': 'https://dashboard.katabump.com/auth/login',
            'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
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
        self.last_html = resp.text
        
        if DEBUG_MODE:
            log(f'状态: {resp.status_code}, 长度: {len(resp.text)}', 'DEBUG')
            # 显示前200字符
            preview = resp.text[:200].replace('\n', ' ')
            log(f'预览: {preview}...', 'DEBUG')
        return resp

    def post(self, path, data):
        """POST 请求"""
        url = f'{self.base_url}{path}'
        if DEBUG_MODE:
            log(f'POST {url}', 'DEBUG')
        
        headers = self.headers.copy()
        headers['content-type'] = 'application/x-www-form-urlencoded'
        headers['origin'] = self.base_url
        
        resp = requests.post(url, data=data, headers=headers, proxies=self.proxies, timeout=60)
        self.last_html = resp.text
        
        if DEBUG_MODE:
            log(f'状态: {resp.status_code}, URL: {resp.url}', 'DEBUG')
        return resp

    def send_error_page(self, error_msg):
        """发送错误页面到 Telegram"""
        if self.last_html:
            page_type = analyze_page(self.last_html)
            caption = f'❌ KataBump 错误\n\n错误: {error_msg}\n页面类型: {page_type}\n长度: {len(self.last_html)} 字符'
            tg_send_html(self.last_html, 'katabump_error.html', caption)

    def get_servers(self):
        """获取服务器列表"""
        log('获取 Dashboard...')
        resp = self.get('/dashboard')
        html = resp.text
        
        if DEBUG_MODE:
            with open('/tmp/dashboard.html', 'w', encoding='utf-8') as f:
                f.write(html)
        
        # 分析页面
        page_type = analyze_page(html)
        log(f'页面类型: {page_type}')
        
        # 检查登录状态
        if page_type == 'login' or '/auth/login' in str(resp.url):
            self.send_error_page('Cookie 已过期')
            raise Exception('Cookie 已过期，请更新 KATA_COOKIES')
        
        if page_type == 'cloudflare':
            self.send_error_page('Cloudflare 挑战')
            raise Exception('遇到 Cloudflare 挑战，请更新 Cookie')
        
        # 解析服务器 ID
        ids = re.findall(r'servers/edit\?id=(\d+)', html)
        servers = list(dict.fromkeys(ids))  # 去重保持顺序
        
        if not servers:
            # 发送页面到 Telegram 以便调试
            self.send_error_page('未找到服务器')
            
            # 尝试提取更多信息
            title_match = re.search(r'<title>([^<]+)</title>', html)
            title = title_match.group(1) if title_match else '无标题'
            log(f'页面标题: {title}', 'WARNING')
            log(f'页面长度: {len(html)} 字符', 'WARNING')
            
            raise Exception(f'未找到服务器 (页面类型: {page_type}, 标题: {title})')
        
        log(f'找到 {len(servers)} 个服务器: {servers}', 'SUCCESS')
        return servers

    def process_server(self, server_id):
        """处理单个服务器"""
        log(f'')
        log(f'━━━ 服务器 {server_id} ━━━')
        
        # 更新 referer
        self.headers['referer'] = f'{self.base_url}/dashboard'
        
        # 获取服务器页面
        resp = self.get(f'/servers/edit?id={server_id}')
        html = resp.text
        
        if DEBUG_MODE:
            with open(f'/tmp/server_{server_id}.html', 'w', encoding='utf-8') as f:
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
        
        # 更新 referer
        self.headers['referer'] = f'{self.base_url}/servers/edit?id={server_id}'
        
        resp = self.post(f'/api-client/renew?id={server_id}', {'csrf': csrf})
        
        if DEBUG_MODE:
            with open(f'/tmp/renew_{server_id}.html', 'w', encoding='utf-8') as f:
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
