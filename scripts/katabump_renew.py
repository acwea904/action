#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本 (Playwright + 代理)
"""

import os
import sys
import re
import time
import random
import base64
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import unquote
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ================= 配置 =================

KATA_COOKIES = os.environ.get('KATA_COOKIES', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
FORCE_RENEW = os.environ.get('FORCE_RENEW', 'false').lower() == 'true'
RENEW_THRESHOLD_DAYS = 2

REPO_TOKEN = os.environ.get('REPO_TOKEN', '')
GITHUB_REPOSITORY = os.environ.get('GITHUB_REPOSITORY', '')

PROFILE_DIR = os.environ.get('PROFILE_DIR', 'pw_profiles/katabump')
PROXY_SERVER = os.environ.get('PROXY_SERVER', '')

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
        proxies = {'http': PROXY_SERVER, 'https': PROXY_SERVER} if PROXY_SERVER else None
        requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            proxies=proxies,
            timeout=30
        )
    except Exception as e:
        log(f'TG 通知失败: {e}', 'WARNING')


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
        self.context = None
        self.page = None
        self.results = []

    async def init_browser(self, playwright):
        """初始化浏览器"""
        log(f'初始化浏览器 Profile: {PROFILE_DIR}')
        
        if PROXY_SERVER:
            log(f'使用代理: {PROXY_SERVER}')
        
        Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
        
        proxy_config = {'server': PROXY_SERVER} if PROXY_SERVER else None
        
        self.context = await playwright.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            proxy=proxy_config,
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
        
        await self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            window.chrome = { runtime: {} };
        """)
        
        self.page = await self.context.new_page()

    async def clear_and_set_cookies(self):
        """清除旧 cookies 并设置新的"""
        if not KATA_COOKIES:
            return
        
        log('清除旧 Cookies...')
        
        # 清除所有 katabump 相关的 cookies
        try:
            await self.context.clear_cookies()
        except:
            pass
        
        log('设置新 Cookies...')
        cookies = []
        for item in KATA_COOKIES.split(';'):
            item = item.strip()
            if '=' in item:
                key, value = item.split('=', 1)
                key = key.strip()
                value = value.strip()
                
                cookie = {
                    'name': key,
                    'value': value,
                    'domain': '.katabump.com',  # 使用 . 前缀支持子域名
                    'path': '/',
                }
                
                # cf_clearance 需要特殊处理
                if key == 'cf_clearance':
                    cookie['sameSite'] = 'None'
                    cookie['secure'] = True
                
                cookies.append(cookie)
        
        if cookies:
            await self.context.add_cookies(cookies)
            log(f'已设置 {len(cookies)} 个 cookies')

    async def navigate_with_retry(self, url, max_retries=3):
        """带重试的导航"""
        for attempt in range(max_retries):
            try:
                # 使用 domcontentloaded 而不是 networkidle，避免超时
                response = await self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
                await self.page.wait_for_timeout(2000)
                return response
            except Exception as e:
                error_msg = str(e)
                log(f'导航尝试 {attempt + 1}/{max_retries} 失败: {error_msg}', 'WARNING')
                
                if 'ERR_TOO_MANY_REDIRECTS' in error_msg:
                    # 清除 cookies 重试
                    log('检测到重定向循环，清除 cookies 重试...')
                    await self.context.clear_cookies()
                    await self.page.wait_for_timeout(1000)
                    
                    # 重新设置 cookies
                    if KATA_COOKIES:
                        await self.clear_and_set_cookies()
                
                if attempt == max_retries - 1:
                    raise
                
                await self.page.wait_for_timeout(2000)
        
        return None

    async def wait_for_cloudflare(self, timeout=60):
        """等待 Cloudflare 验证"""
        log('检查 Cloudflare...')
        
        start = time.time()
        while time.time() - start < timeout:
            try:
                content = await self.page.content()
            except:
                await self.page.wait_for_timeout(2000)
                continue
            
            if 'Just a moment' in content or 'Checking your browser' in content:
                log('等待 Cloudflare 验证...')
                await self.page.wait_for_timeout(3000)
                continue
            
            # 检查 Turnstile
            turnstile = await self.page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if turnstile:
                log('检测到 Turnstile，等待...')
                await self.page.wait_for_timeout(3000)
                continue
            
            log('Cloudflare 通过', 'SUCCESS')
            return True
        
        log('Cloudflare 超时', 'ERROR')
        return False

    async def check_login_status(self):
        """检查登录状态"""
        current_url = self.page.url
        
        if '/auth/login' in current_url:
            return False
        
        # 检查页面是否有登录表单
        login_form = await self.page.query_selector('input[name="password"]')
        if login_form:
            return False
        
        return True

    async def get_servers(self):
        """获取服务器列表"""
        log('获取服务器列表...')
        
        # 先设置 cookies
        await self.clear_and_set_cookies()
        
        # 导航到 dashboard
        await self.navigate_with_retry(f'{BASE_URL}/dashboard')
        
        if not await self.wait_for_cloudflare():
            raise Exception('Cloudflare 验证失败')
        
        if not await self.check_login_status():
            raise Exception('未登录，请更新 KATA_COOKIES')
        
        # 调用 API
        try:
            response = await self.page.evaluate("""
                async () => {
                    try {
                        const resp = await fetch('/api-client/list-servers');
                        return await resp.json();
                    } catch (e) {
                        return { error: e.message };
                    }
                }
            """)
        except Exception as e:
            raise Exception(f'API 调用失败: {e}')
        
        if isinstance(response, dict) and 'error' in response:
            raise Exception(f"API 错误: {response['error']}")
        
        if not isinstance(response, list):
            raise Exception('API 返回格式错误')
        
        if not response:
            log('没有服务器', 'WARNING')
            return []
        
        log(f'找到 {len(response)} 个服务器', 'SUCCESS')
        
        servers = []
        for s in response:
            info = {'id': s.get('id'), 'name': s.get('name', f"Server-{s.get('id')}")}
            log(f"  - {info['id']}: {info['name']}")
            servers.append(info)
        
        return servers

    async def get_server_expiry(self, server_id):
        """获取到期时间"""
        await self.navigate_with_retry(f'{BASE_URL}/servers/edit?id={server_id}')
        await self.wait_for_cloudflare()
        
        content = await self.page.content()
        m = re.search(r'Expiry[\s\S]{0,200}?(\d{4}-\d{2}-\d{2})', content)
        return m.group(1) if m else None

    async def do_renew(self, server_id):
        """执行续订"""
        # 点击 Renew 按钮
        renew_btn = await self.page.query_selector('button[data-bs-target="#renew-modal"]')
        if not renew_btn:
            # 尝试其他选择器
            renew_btn = await self.page.query_selector('button:has-text("Renew")')
        
        if not renew_btn:
            return False, '找不到续订按钮'
        
        await renew_btn.click()
        await self.page.wait_for_timeout(1500)
        
        # 等待模态框
        try:
            await self.page.wait_for_selector('#renew-modal.show, .modal.show', timeout=5000)
        except:
            return False, '模态框未打开'
        
        # 等待 Turnstile
        log('等待 Turnstile...')
        for _ in range(20):
            await self.page.wait_for_timeout(1500)
            
            token = await self.page.evaluate("""
                () => {
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    return input ? input.value : null;
                }
            """)
            
            if token:
                log('Turnstile 完成', 'SUCCESS')
                break
        
        # 点击提交
        submit_btn = await self.page.query_selector('#renew-modal button[type="submit"], .modal.show button[type="submit"]')
        if not submit_btn:
            return False, '找不到提交按钮'
        
        await submit_btn.click()
        
        # 等待响应
        await self.page.wait_for_timeout(3000)
        
        # 等待可能的跳转
        try:
            await self.page.wait_for_load_state('networkidle', timeout=10000)
        except:
            pass
        
        url = self.page.url
        
        if 'renew=success' in url:
            return True, None
        
        if 'renew-error=' in url:
            m = re.search(r'renew-error=([^&]+)', url)
            msg = unquote(m.group(1).replace('+', ' ')) if m else '未知错误'
            return False, msg
        
        content = await self.page.content()
        if 'has been renewed' in content.lower() or 'successfully' in content.lower():
            return True, None
        
        return False, '未知响应'

    async def process_server(self, server_info):
        """处理服务器"""
        server_id = server_info['id']
        name = server_info['name']
        
        log(f'')
        log(f'━━━ {name} (ID: {server_id}) ━━━')
        
        expiry = await self.get_server_expiry(server_id)
        days = days_until(expiry)
        
        log(f'到期: {expiry or "未知"} | 剩余: {days if days is not None else "?"} 天')
        
        if not FORCE_RENEW and days is not None and days > RENEW_THRESHOLD_DAYS:
            return {'id': server_id, 'name': name, 'expiry': expiry, 'days': days, 'action': 'skip', 'ok': True}
        
        log('执行续订...')
        ok, err = await self.do_renew(server_id)
        
        if ok:
            new_expiry = await self.get_server_expiry(server_id)
            return {'id': server_id, 'name': name, 'old': expiry, 'new': new_expiry or '?', 'action': 'renewed', 'ok': True}
        
        if err and ("can't renew" in err.lower() or 'not yet' in err.lower()):
            return {'id': server_id, 'name': name, 'expiry': expiry, 'days': days, 'action': 'not_yet', 'msg': err, 'ok': True}
        
        return {'id': server_id, 'name': name, 'expiry': expiry, 'action': 'failed', 'msg': err or '失败', 'ok': False}

    async def save_cookies_to_secret(self):
        """保存 cookies"""
        if not REPO_TOKEN or not GITHUB_REPOSITORY:
            return
        
        try:
            from nacl import encoding, public
            
            cookies = await self.context.cookies()
            cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies if 'katabump.com' in c.get('domain', '')])
            
            if not cookie_str:
                return
            
            log('保存 Cookies...')
            
            headers = {
                'Authorization': f'Bearer {REPO_TOKEN}',
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28'
            }
            
            proxies = {'http': PROXY_SERVER, 'https': PROXY_SERVER} if PROXY_SERVER else None
            
            resp = requests.get(
                f'https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key',
                headers=headers, proxies=proxies, timeout=30
            )
            
            if resp.status_code != 200:
                return
            
            key_data = resp.json()
            public_key = public.PublicKey(key_data['key'].encode("utf-8"), encoding.Base64Encoder())
            sealed_box = public.SealedBox(public_key)
            encrypted = base64.b64encode(sealed_box.encrypt(cookie_str.encode("utf-8"))).decode("utf-8")
            
            resp = requests.put(
                f'https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/KATA_COOKIES',
                headers=headers, proxies=proxies,
                json={'encrypted_value': encrypted, 'key_id': key_data['key_id']},
                timeout=30
            )
            
            if resp.status_code in (201, 204):
                log('Cookies 已保存', 'SUCCESS')
        except Exception as e:
            log(f'保存 Cookies 失败: {e}', 'WARNING')

    async def run(self):
        """主函数"""
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
                
                await self.save_cookies_to_secret()
                
            finally:
                await self.context.close()
        
        self.print_summary()
        return all(r['ok'] for r in self.results)

    def print_summary(self):
        """汇总"""
        log('')
        log('=' * 50)
        log('汇总')
        
        renewed = [r for r in self.results if r['action'] == 'renewed']
        skipped = [r for r in self.results if r['action'] == 'skip']
        not_yet = [r for r in self.results if r['action'] == 'not_yet']
        failed = [r for r in self.results if r['action'] in ('failed', 'error', 'unknown')]
        
        for r in renewed:
            log(f"✅ {r['name']}: {r.get('old')} → {r.get('new')}")
        for r in skipped:
            log(f"📋 {r['name']}: {r.get('expiry')} ({r.get('days')}天)")
        for r in not_yet:
            log(f"ℹ️ {r['name']}: {r.get('expiry')} ({r.get('days')}天) - 暂不能续订")
        for r in failed:
            log(f"❌ {r['name']}: {r.get('msg', '失败')}")
        
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
        ok = await KataBumpRenewer().run()
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
