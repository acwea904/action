#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KataBump 自动续订脚本 (代理 + CF Cookie 预热)
"""

import os
import sys
import re
import asyncio
import requests
import time
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

# 配置
DASHBOARD_URL = 'https://dashboard.katabump.com'
SERVER_ID = os.environ.get('KATA_SERVER_ID') or ''
KATA_EMAIL = os.environ.get('KATA_EMAIL') or ''
KATA_PASSWORD = os.environ.get('KATA_PASSWORD') or ''
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ''
TG_CHAT_ID = os.environ.get('TG_CHAT_ID') or os.environ.get('TG_USER_ID') or ''
CAPSOLVER_KEY = os.environ.get('CAPSOLVER_KEY') or ''
SCREENSHOT_DIR = os.environ.get('SCREENSHOT_DIR') or '/tmp'
PROXY_SERVER = os.environ.get('PROXY_SERVER') or ''  # socks5://127.0.0.1:1080

# Cloudflare 验证相关
CF_CHALLENGE_URL = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/cmg/1'
TURNSTILE_SITEKEY = '0x4AAAAAAA1IssKDXD0TRMjP'


def log(msg):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def tg_notify(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        proxies = {'https': PROXY_SERVER} if PROXY_SERVER else None
        requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=30,
            proxies=proxies
        )
        return True
    except:
        return False


def tg_notify_photo(photo_path, caption=''):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        proxies = {'https': PROXY_SERVER} if PROXY_SERVER else None
        with open(photo_path, 'rb') as f:
            requests.post(
                f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto',
                data={'chat_id': TG_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'},
                files={'photo': f},
                timeout=60,
                proxies=proxies
            )
        return True
    except:
        return False


def get_expiry_from_text(text):
    match = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', text, re.IGNORECASE)
    return match.group(1) if match else None


def days_until(date_str):
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


async def warmup_cf_cookie(context, page):
    """
    预热 Cloudflare Cookie
    访问 CF challenge 页面，触发验证并获取 _cfuvid cookie
    """
    log('🔥 预热 Cloudflare Cookie...')
    
    try:
        # 先访问目标网站主页，建立 session
        log('📄 访问 KataBump 主页...')
        await page.goto(DASHBOARD_URL, timeout=60000, wait_until='domcontentloaded')
        await page.wait_for_timeout(3000)
        
        # 检查当前 cookies
        cookies = await context.cookies()
        cf_cookies = [c for c in cookies if 'cf' in c['name'].lower()]
        log(f'📋 当前 CF Cookies: {[c["name"] for c in cf_cookies]}')
        
        # 如果遇到 CF 验证页面，等待通过
        page_content = await page.content()
        if 'Just a moment' in page_content or 'Checking your browser' in page_content:
            log('🛡 检测到 CF 验证页面，等待通过...')
            
            for i in range(30):
                await page.wait_for_timeout(2000)
                page_content = await page.content()
                
                if 'Just a moment' not in page_content and 'Checking your browser' not in page_content:
                    log(f'✅ CF 验证通过 ({(i+1)*2}秒)')
                    break
                
                if i % 5 == 4:
                    log(f'⏳ 继续等待 CF 验证... ({(i+1)*2}秒)')
                    
                    # 尝试点击可能存在的验证按钮
                    try:
                        verify_btn = page.locator('input[type="button"], button:has-text("Verify")')
                        if await verify_btn.count() > 0:
                            await verify_btn.first.click()
                            log('🖱 点击验证按钮')
                    except:
                        pass
            else:
                log('⚠️ CF 验证超时，继续尝试...')
        
        # 再次检查 cookies
        cookies = await context.cookies()
        cf_cookies = [c for c in cookies if 'cf' in c['name'].lower()]
        cfuvid = next((c for c in cookies if c['name'] == '_cfuvid'), None)
        
        if cfuvid:
            log(f'✅ 获取到 _cfuvid Cookie')
            log(f'📋 Cookie 域: {cfuvid.get("domain")}')
        else:
            log('⚠️ 未获取到 _cfuvid，继续执行...')
        
        # 访问 CF challenge 端点预热
        log('🔄 访问 CF Challenge 端点...')
        try:
            await page.goto(CF_CHALLENGE_URL, timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            log(f'⚠️ 访问 challenge 端点: {e}')
        
        # 返回主站
        await page.goto(DASHBOARD_URL, timeout=60000, wait_until='domcontentloaded')
        await page.wait_for_timeout(2000)
        
        # 最终检查 cookies
        cookies = await context.cookies()
        cf_cookies = [c for c in cookies if 'cf' in c['name'].lower()]
        log(f'✅ CF Cookie 预热完成，共 {len(cf_cookies)} 个 CF 相关 Cookie')
        
        return True
        
    except Exception as e:
        log(f'⚠️ Cookie 预热失败: {e}')
        return False


async def handle_turnstile(page, modal_selector='#renew-modal'):
    """
    处理 Turnstile 验证码
    """
    log('🔍 检查 Turnstile 验证码...')
    
    turnstile = page.locator(f'{modal_selector} .cf-turnstile, {modal_selector} [data-sitekey]')
    
    if await turnstile.count() == 0:
        log('✅ 无需验证码')
        return True
    
    log('🛡 检测到 Turnstile 验证码')
    await page.wait_for_timeout(2000)
    
    # 方法1: 等待自动通过
    log('⏳ 等待 Turnstile 自动验证...')
    response_input = page.locator(f'{modal_selector} input[name="cf-turnstile-response"]')
    
    for i in range(45):
        await page.wait_for_timeout(1000)
        
        # 检查是否已获取 token
        if await response_input.count() > 0:
            current_value = await response_input.get_attribute('value') or ''
            if len(current_value) > 20:
                log(f'✅ Turnstile 验证成功 ({i+1}秒)')
                return True
        
        # 每5秒尝试点击一次
        if i % 5 == 2:
            try:
                # 尝试点击 Turnstile iframe 中的 checkbox
                turnstile_iframe = page.frame_locator(f'{modal_selector} iframe[src*="turnstile"]').first
                checkbox = turnstile_iframe.locator('input[type="checkbox"], .cb-i, .mark')
                if await checkbox.count() > 0:
                    await checkbox.first.click(force=True)
                    log('🖱 点击 Turnstile checkbox')
            except:
                pass
        
        if i % 5 == 4:
            log(f'⏳ 继续等待... ({i+1}秒)')
    
    # 方法2: 使用 Capsolver (如果配置了)
    if CAPSOLVER_KEY:
        log('🔄 尝试使用 Capsolver...')
        token = solve_turnstile_capsolver(page.url, TURNSTILE_SITEKEY)
        if token:
            await page.evaluate('''(token) => {
                document.querySelectorAll('input[name="cf-turnstile-response"]').forEach(i => {
                    i.value = token;
                });
            }''', token)
            log('✅ Token 已注入')
            return True
    
    log('❌ Turnstile 验证失败')
    return False


def solve_turnstile_capsolver(page_url, sitekey):
    """使用 Capsolver 解决 Turnstile"""
    if not CAPSOLVER_KEY:
        return None
    
    log('🔄 使用 Capsolver 解决 Turnstile...')
    try:
        proxies = {'https': PROXY_SERVER} if PROXY_SERVER else None
        resp = requests.post('https://api.capsolver.com/createTask', json={
            'clientKey': CAPSOLVER_KEY,
            'task': {
                'type': 'AntiTurnstileTaskProxyLess',
                'websiteURL': page_url,
                'websiteKey': sitekey
            }
        }, timeout=30, proxies=proxies)
        result = resp.json()
        
        if result.get('errorId') != 0:
            log(f'❌ Capsolver 错误: {result.get("errorDescription")}')
            return None
        
        task_id = result.get('taskId')
        log(f'📋 任务 ID: {task_id}')
        
        for i in range(60):
            time.sleep(2)
            resp = requests.post('https://api.capsolver.com/getTaskResult', json={
                'clientKey': CAPSOLVER_KEY,
                'taskId': task_id
            }, timeout=30, proxies=proxies)
            result = resp.json()
            
            if result.get('status') == 'ready':
                log('✅ Capsolver 解决成功')
                return result.get('solution', {}).get('token')
            elif result.get('status') == 'failed':
                log(f'❌ Capsolver 失败: {result.get("errorDescription")}')
                return None
        
        log('❌ Capsolver 超时')
        return None
    except Exception as e:
        log(f'❌ Capsolver 错误: {e}')
        return None


async def run():
    log('🚀 KataBump 自动续订')
    log(f'🖥 服务器 ID: {SERVER_ID}')
    log(f'🌐 代理: {PROXY_SERVER or "无"}')
    
    if not SERVER_ID:
        raise Exception('未设置 KATA_SERVER_ID')
    
    server_url = f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}'
    
    async with async_playwright() as p:
        # 浏览器启动参数
        browser_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-blink-features=AutomationControlled',
            '--disable-infobars',
            '--window-size=1280,900',
        ]
        
        browser = await p.chromium.launch(
            headless=True,
            args=browser_args
        )
        
        # 配置代理
        context_options = {
            'viewport': {'width': 1280, 'height': 900},
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'locale': 'en-US',
            'timezone_id': 'America/New_York',
        }
        
        # 添加代理配置
        if PROXY_SERVER:
            context_options['proxy'] = {'server': PROXY_SERVER}
            log(f'✅ 已配置代理: {PROXY_SERVER}')
        
        context = await browser.new_context(**context_options)
        page = await context.new_page()
        
        # 反检测脚本
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'permissions', {
                get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
            });
            // 隐藏 automation 标记
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)
        
        try:
            # ========== 预热 CF Cookie ==========
            await warmup_cf_cookie(context, page)
            
            # ========== 登录 ==========
            log('🔐 正在登录...')
            await page.goto(f'{DASHBOARD_URL}/auth/login', timeout=60000)
            await page.wait_for_timeout(2000)
            
            # 检查是否有 CF 验证页面
            page_content = await page.content()
            if 'Just a moment' in page_content:
                log('⏳ 等待 CF 验证...')
                for i in range(20):
                    await page.wait_for_timeout(2000)
                    page_content = await page.content()
                    if 'Just a moment' not in page_content:
                        log('✅ CF 验证通过')
                        break
            
            # 填写登录表单
            await page.locator('input[name="email"], input[type="email"]').fill(KATA_EMAIL)
            await page.locator('input[name="password"], input[type="password"]').fill(KATA_PASSWORD)
            await page.wait_for_timeout(500)
            await page.locator('button[type="submit"], input[type="submit"]').first.click()
            
            await page.wait_for_timeout(4000)
            
            try:
                await page.wait_for_url('**/dashboard**', timeout=15000)
            except:
                pass
            
            if '/auth/login' in page.url:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'login_failed.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, '❌ 登录失败')
                raise Exception('登录失败')
            
            log('✅ 登录成功')
            
            # ========== 打开服务器页面 ==========
            log('📄 打开服务器页面')
            await page.goto(server_url, timeout=60000, wait_until='domcontentloaded')
            
            try:
                await page.locator('button[data-bs-target="#renew-modal"]').wait_for(timeout=20000)
                log('✅ 页面加载完成')
            except:
                await page.wait_for_timeout(5000)
            
            page_content = await page.content()
            old_expiry = get_expiry_from_text(page_content) or '未知'
            days = days_until(old_expiry)
            log(f'📅 当前到期: {old_expiry} (剩余 {days} 天)')
            
            # ========== 点击 Renew 按钮 ==========
            log('🔍 查找 Renew 按钮...')
            main_renew_btn = page.locator('button[data-bs-target="#renew-modal"]')
            if await main_renew_btn.count() == 0:
                main_renew_btn = page.locator('button.btn-outline-primary:has-text("Renew")')
            
            if await main_renew_btn.count() == 0:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'no_renew.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, f'❌ 未找到 Renew 按钮\n服务器: {SERVER_ID}')
                raise Exception('未找到 Renew 按钮')
            
            log('🖱 点击 Renew 按钮...')
            await main_renew_btn.first.click()
            await page.wait_for_timeout(2000)
            
            # ========== 等待模态框 ==========
            modal = page.locator('#renew-modal')
            try:
                await modal.wait_for(state='visible', timeout=5000)
                log('✅ 模态框已打开')
            except:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'modal_error.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, '❌ 模态框未打开')
                raise Exception('模态框未打开')
            
            # ========== 处理 Turnstile ==========
            turnstile_ok = await handle_turnstile(page, '#renew-modal')
            
            if not turnstile_ok:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'turnstile_failed.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                
                if days is not None and days <= 3:
                    tg_notify_photo(screenshot_path, 
                        f'⚠️ 需要手动续订\n'
                        f'服务器: {SERVER_ID}\n'
                        f'到期: {old_expiry} (剩余 {days} 天)\n\n'
                        f'👉 {server_url}')
                else:
                    log(f'ℹ️ 剩余 {days} 天，暂不紧急')
                return
            
            # ========== 提交续订 ==========
            log('🖱 点击确认 Renew...')
            submit_btn = page.locator('#renew-modal button[type="submit"]')
            if await submit_btn.count() == 0:
                submit_btn = page.locator('#renew-modal .modal-footer button.btn-primary')
            
            await submit_btn.first.click()
            
            log('⏳ 等待服务器响应...')
            await page.wait_for_timeout(5000)
            
            try:
                await page.wait_for_load_state('domcontentloaded', timeout=15000)
            except:
                pass
            
            # ========== 检查结果 ==========
            log('🔍 检查续订结果...')
            current_url = page.url
            page_content = await page.content()
            screenshot_path = os.path.join(SCREENSHOT_DIR, 'result.png')
            await page.screenshot(path=screenshot_path, full_page=True)
            
            if 'renew=success' in current_url:
                new_expiry = get_expiry_from_text(page_content) or '未知'
                log(f'🎉 续订成功！新到期: {new_expiry}')
                tg_notify_photo(screenshot_path, 
                    f'✅ KataBump 续订成功\n'
                    f'服务器: {SERVER_ID}\n'
                    f'原到期: {old_expiry}\n'
                    f'新到期: {new_expiry}')
                
            elif 'renew-error' in current_url:
                error_match = re.search(r'renew-error=([^&]+)', current_url)
                error_msg = '未知错误'
                if error_match:
                    from urllib.parse import unquote
                    error_msg = unquote(error_match.group(1).replace('+', ' '))
                
                log(f'⚠️ 续订受限: {error_msg}')
                if days is not None and days <= 2:
                    tg_notify_photo(screenshot_path, 
                        f'ℹ️ KataBump 续订提醒\n'
                        f'服务器: {SERVER_ID}\n'
                        f'到期: {old_expiry} (剩余 {days} 天)\n'
                        f'📝 {error_msg}')
            else:
                # 重新检查
                log('🔄 重新检查到期时间...')
                await page.goto(server_url, timeout=60000, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)
                
                page_content = await page.content()
                new_expiry = get_expiry_from_text(page_content) or '未知'
                
                if new_expiry != '未知' and old_expiry != '未知' and new_expiry > old_expiry:
                    log(f'🎉 续订成功！新到期: {new_expiry}')
                    screenshot_path = os.path.join(SCREENSHOT_DIR, 'success.png')
                    await page.screenshot(path=screenshot_path, full_page=True)
                    tg_notify_photo(screenshot_path, 
                        f'✅ KataBump 续订成功\n'
                        f'服务器: {SERVER_ID}\n'
                        f'原到期: {old_expiry}\n'
                        f'新到期: {new_expiry}')
                else:
                    log(f'ℹ️ 到期时间: {new_expiry}')
                    if days is not None and days <= 2:
                        tg_notify_photo(screenshot_path, 
                            f'⚠️ 请检查续订状态\n'
                            f'服务器: {SERVER_ID}\n'
                            f'到期: {new_expiry} (剩余 {days} 天)\n\n'
                            f'👉 {server_url}')
        
        except Exception as e:
            log(f'❌ 错误: {e}')
            try:
                screenshot_path = os.path.join(SCREENSHOT_DIR, 'error.png')
                await page.screenshot(path=screenshot_path, full_page=True)
                tg_notify_photo(screenshot_path, f'❌ 出错: {e}')
            except:
                pass
            tg_notify(f'❌ KataBump 出错\n🖥 {SERVER_ID}\n❗ {e}')
            raise
        
        finally:
            await browser.close()


def main():
    log('=' * 50)
    log('   KataBump 自动续订 (代理版)')
    log('=' * 50)
    
    if not KATA_EMAIL or not KATA_PASSWORD:
        log('❌ 请设置 KATA_EMAIL 和 KATA_PASSWORD')
        sys.exit(1)
    
    if not SERVER_ID:
        log('❌ 请设置 KATA_SERVER_ID')
        sys.exit(1)
    
    log(f'📧 邮箱: {KATA_EMAIL[:3]}***')
    log(f'🖥 服务器: {SERVER_ID}')
    log(f'🌐 代理: {PROXY_SERVER or "无"}')
    log(f'🔑 Capsolver: {"已配置" if CAPSOLVER_KEY else "未配置"}')
    
    asyncio.run(run())
    log('🏁 完成')


if __name__ == '__main__':
    main()
