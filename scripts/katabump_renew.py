#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本 (Playwright 版本)
使用持久化浏览器 Profile 保持登录状态和 cf_clearance
"""

import os
import sys
import re
import time
import json
import random
import base64
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ================= 配置 =================

KATA_COOKIES = os.environ.get('KATA_COOKIES', '')  # 初始 cookies（可选，用于首次登录）
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'
RENEW_THRESHOLD_DAYS = 2

# GitHub 相关
REPO_TOKEN = os.environ.get('REPO_TOKEN', '')
GITHUB_REPOSITORY = os.environ.get('GITHUB_REPOSITORY', '')

# Playwright Profile 目录
PROFILE_DIR = os.environ.get('PROFILE_DIR', 'pw_profiles/katabump')

# 基础 URL
BASE_URL = 'https://dashboard.katabump.com'


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
            timeout=30
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


class KataBumpRenewer:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.results = []

    async def init_browser(self, playwright):
        """初始化浏览器，使用持久化 Profile"""
        log(f'初始化浏览器 Profile: {PROFILE_DIR}')
        
        # 确保目录存在
        Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        
        # 启动持久化上下文
        self.context = await playwright.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
            ],
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            ignore_https_errors=True,
        )
        
        # 注入反检测脚本
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
        """)
        
        self.page = await self.context.new_page()
        
        # 如果有初始 cookies，注入它们
        if KATA_COOKIES and not await self.check_login():
            await self.inject_cookies()

    async def inject_cookies(self):
        """注入初始 cookies"""
        log('注入初始 Cookies...')
        cookies = []
        for item in KATA_COOKIES.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                cookies.append({
                    'name': key.strip(),
                    'value': value.strip(),
                    'domain': 'dashboard.katabump.com',
                    'path': '/'
                })
        
        if cookies:
            await self.context.add_cookies(cookies)
            log(f'已注入 {len(cookies)} 个 cookies')

    async def check_login(self):
        """检查是否已登录"""
        try:
            await self.page.goto(f'{BASE_URL}/dashboard', wait_until='networkidle', timeout=30000)
            await self.page.wait_for_timeout(2000)
            
            # 检查是否在登录页
            if '/auth/login' in self.page.url:
                return False
            
            # 检查是否有登录表单
            login_form = await self.page.query_selector('input[name="password"]')
            if login_form:
                return False
            
            return True
        except:
            return False

    async def wait_for_cloudflare(self):
        """等待 Cloudflare 验证完成"""
        log('检查 Cloudflare 验证...')
        
        for _ in range(30):
            content = await self.page.content()
            
            # 检查是否还在 Cloudflare 验证页面
            if 'Just a moment' in content or 'Checking your browser' in content:
                log('等待 Cloudflare 验证...')
                await self.page.wait_for_timeout(2000)
                continue
            
            # 检查是否有 Turnstile iframe
            turnstile = await self.page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if turnstile:
                log('检测到 Turnstile，等待自动完成...')
                await self.page.wait_for_timeout(3000)
                continue
            
            # 验证通过
            log('Cloudflare 验证通过', 'SUCCESS')
            return True
        
        log('Cloudflare 验证超时', 'ERROR')
        return False

    async def get_servers(self):
        """获取服务器列表"""
        log('获取服务器列表...')
        
        await self.page.goto(f'{BASE_URL}/dashboard', wait_until='networkidle', timeout=30000)
        
        if not await self.wait_for_cloudflare():
            raise Exception('Cloudflare 验证失败')
        
        # 检查登录状态
        if '/auth/login' in self.page.url:
            raise Exception('未登录，请更新 KATA_COOKIES 或手动登录')
        
        # 调用 API 获取服务器列表
        response = await self.page.evaluate("""
            async () => {
                const resp = await fetch('/api-client/list-servers');
                return await resp.json();
            }
        """)
        
        if not isinstance(response, list):
            raise Exception('API 返回格式错误')
        
        if not response:
            log('没有服务器', 'WARNING')
            return []
        
        log(f'找到 {len(response)} 个服务器', 'SUCCESS')
        
        servers = []
        for s in response:
            info = {
                'id': s.get('id'),
                'name': s.get('name', f"Server-{s.get('id')}"),
            }
            log(f"  - {info['id']}: {info['name']}")
            servers.append(info)
        
        return servers

    async def get_server_expiry(self, server_id):
        """获取服务器到期时间"""
        await self.page.goto(f'{BASE_URL}/servers/edit?id={server_id}', wait_until='networkidle', timeout=30000)
        await self.wait_for_cloudflare()
        
        content = await self.page.content()
        
        # 提取到期时间
        m = re.search(r'Expiry[\s\S]{0,200}?(\d{4}-\d{2}-\d{2})', content)
        return m.group(1) if m else None

    async def click_renew_button(self):
        """点击续订按钮"""
        # 找到并点击 Renew 按钮打开模态框
        renew_btn = await self.page.query_selector('button[data-bs-target="#renew-modal"]')
        if not renew_btn:
            return False, '找不到续订按钮'
        
        await renew_btn.click()
        await self.page.wait_for_timeout(1000)
        
        # 等待模态框出现
        modal = await self.page.wait_for_selector('#renew-modal.show', timeout=5000)
        if not modal:
            return False, '模态框未打开'
        
        return True, None

    async def handle_turnstile(self):
        """处理 Turnstile 验证码"""
        log('检查 Turnstile 验证码...')
        
        # 等待 Turnstile 加载
        await self.page.wait_for_timeout(2000)
        
        # 检查是否有 Turnstile
        turnstile_frame = await self.page.query_selector('iframe[src*="challenges.cloudflare.com"]')
        
        if turnstile_frame:
            log('等待 Turnstile 自动完成...')
            
            # 等待 Turnstile 完成（最多 30 秒）
            for _ in range(15):
                await self.page.wait_for_timeout(2000)
                
                # 检查是否已获取 token
                token = await self.page.evaluate("""
                    () => {
                        const input = document.querySelector('input[name="cf-turnstile-response"]');
                        return input ? input.value : null;
                    }
                """)
                
                if token:
                    log('Turnstile 验证完成', 'SUCCESS')
                    return True
            
            log('Turnstile 验证超时', 'WARNING')
        
        return True  # 即使没有 token 也尝试提交

    async def submit_renew(self):
        """提交续订表单"""
        # 找到模态框中的提交按钮
        submit_btn = await self.page.query_selector('#renew-modal button[type="submit"]')
        if not submit_btn:
            return False, '找不到提交按钮'
        
        # 点击提交
        await submit_btn.click()
        
        # 等待页面跳转
        try:
            await self.page.wait_for_url('**/servers/edit**', timeout=15000)
        except PlaywrightTimeout:
            pass
        
        await self.page.wait_for_timeout(2000)
        
        # 检查结果
        current_url = self.page.url
        
        if 'renew=success' in current_url:
            return True, None
        
        if 'renew-error=' in current_url:
            m = re.search(r'renew-error=([^&]+)', current_url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            return False, msg
        
        # 检查页面内容
        content = await self.page.content()
        if 'has been renewed' in content.lower():
            return True, None
        
        return False, '未知响应'

    async def process_server(self, server_info):
        """处理单个服务器"""
        server_id = server_info['id']
        name = server_info['name']
        
        log(f'')
        log(f'━━━ {name} (ID: {server_id}) ━━━')
        
        # 获取到期时间
        expiry = await self.get_server_expiry(server_id)
        days = days_until(expiry)
        
        log(f'到期: {expiry or "未知"} | 剩余: {days if days is not None else "?"} 天')
        
        # 判断是否需要续订
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            return {'id': server_id, 'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        # 执行续订
        log('执行续订...')
        
        # 点击续订按钮
        ok, err = await self.click_renew_button()
        if not ok:
            log(f'点击续订按钮失败: {err}', 'ERROR')
            return {'id': server_id, 'name': name, 'action': 'error', 'msg': err, 'ok': False}
        
        # 处理 Turnstile
        await self.handle_turnstile()
        
        # 提交续订
        ok, err = await self.submit_renew()
        
        if ok:
            # 获取新的到期时间
            new_expiry = await self.get_server_expiry(server_id)
            return {'id': server_id, 'name': name, 'old': expiry, 'new': new_expiry or '?', 'action': 'renewed', 'ok': True}
        
        # 检查是否是"暂不能续订"
        if err and ("can't renew" in err.lower() or 'not yet' in err.lower()):
            return {'id': server_id, 'name': name, 'expiry': expiry, 'days': days, 'action': 'not_yet', 'msg': err, 'ok': True}
        
        return {'id': server_id, 'name': name, 'expiry': expiry, 'action': 'failed', 'msg': err or '未知错误', 'ok': False}

    async def save_cookies_to_secret(self):
        """保存 cookies 到 GitHub Secret"""
        if not REPO_TOKEN or not GITHUB_REPOSITORY:
            return
        
        try:
            from nacl import encoding, public
            
            cookies = await self.context.cookies()
            cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies if 'katabump.com' in c.get('domain', '')])
            
            if not cookie_str:
                return
            
            log('保存 Cookies 到 GitHub Secret...')
            
            headers = {
                'Authorization': f'Bearer {REPO_TOKEN}',
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'
            }
            
            # 获取公钥
            resp = requests.get(
                f'https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key',
                headers=headers, timeout=30
            )
            
            if resp.status_code != 200:
                return
            
            key_data = resp.json()
            
            # 加密
            public_key = public.PublicKey(key_data['key'].encode("utf-8"), encoding.Base64Encoder())
            sealed_box = public.SealedBox(public_key)
            encrypted = base64.b64encode(sealed_box.encrypt(cookie_str.encode("utf-8"))).decode("utf-8")
            
            # 更新 Secret
            resp = requests.put(
                f'https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/KATA_COOKIES',
                headers=headers,
                json={'encrypted_value': encrypted, 'key_id': key_data['key_id']},
                timeout=30
            )
            
            if resp.status_code in (201, 204):
                log('Cookies 已保存', 'SUCCESS')
        except Exception as e:
            log(f'保存 Cookies 失败: {e}', 'WARNING')

    async def run(self):
        """主运行函数"""
        log('=' * 50)
        log('KataBump 自动续订 (Playwright)')
        log('=' * 50)
        
        if FORCE_RENEW:
            log('强制续订模式', 'WARNING')
        
        async with async_playwright() as playwright:
            await self.init_browser(playwright)
            
            try:
                servers = await self.get_servers()
                
                if not servers:
                    tg_notify('📋 <b>KataBump</b>\n\n没有服务器')
                    return True
                
                for i, server_info in enumerate(servers):
                    if i > 0:
                        await self.page.wait_for_timeout(random.randint(2000, 4000))
                    self.results.append(await self.process_server(server_info))
                
                # 保存 cookies
                await self.save_cookies_to_secret()
                
            finally:
                await self.context.close()
        
        # 输出汇总
        self.print_summary()
        
        return all(r['ok'] for r in self.results)

    def print_summary(self):
        """打印汇总"""
        log('')
        log('=' * 50)
        log('汇总')
        
        renewed = [r for r in self.results if r['action'] == 'renewed']
        skipped = [r for r in self.results if r['action'] == 'skip']
        not_yet = [r for r in self.results if r['action'] == 'not_yet']
        failed = [r for r in self.results if r['action'] in ('failed', 'error', 'unknown')]
        
        # 控制台输出
        for r in renewed:
            log(f"✅ {r['name']}: {r.get('old')} → {r.get('new')}")
        for r in skipped:
            log(f"📋 {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        for r in not_yet:
            log(f"ℹ️ {r['name']}: {r.get('expiry')} ({r.get('days')}天) - 暂不能续订")
        for r in failed:
            log(f"❌ {r['name']}: {r.get('msg', '失败')}")
        
        # Telegram 通知
        msg = ['📋 <b>KataBump 续订报告</b>\n']
        
        for r in renewed:
            msg.append(f"✅ {r['name']}: {r.get('old')} → {r.get('new')}")
        for r in skipped:
            msg.append(f"📋 {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        for r in not_yet:
            msg.append(f"ℹ️ {r['name']}: {r.get('expiry')} ({r.get('days')}天) - 暂不能续订")
        for r in failed:
            msg.append(f"❌ {r['name']}: {r.get('msg', '失败')}")
        
        tg_notify('\n'.join(msg))


async def main():
    try:
        renewer = KataBumpRenewer()
        ok = await renewer.run()
        log('🏁 结束')
        sys.exit(0 if ok else 1)
    except Exception as e:
        log(f'错误: {e}', 'ERROR')
        import traceback
        traceback.print_exc()
        tg_notify(f'❌ <b>KataBump 出错</b>\n\n{e}')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
