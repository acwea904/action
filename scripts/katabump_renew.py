#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KataBump 自动续订脚本"""

import os
import re
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

DASHBOARD_URL = 'https://dashboard.katabump.com'
SERVER_ID = os.environ.get('KATA_SERVER_ID', '')
KATA_EMAIL = os.environ.get('KATA_EMAIL', '')
KATA_PASSWORD = os.environ.get('KATA_PASSWORD', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')
SCREENSHOT_DIR = os.environ.get('SCREENSHOT_DIR', '/tmp')
HTTP_PROXY = os.environ.get('HTTP_PROXY', '')


def log(msg):
    t = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def tg_notify_photo(photo_path, caption=''):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        with open(photo_path, 'rb') as f:
            requests.post(f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto',
                          data={'chat_id': TG_CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'},
                          files={'photo': f}, timeout=60)
    except:
        pass


def get_expiry(text):
    m = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', text, re.IGNORECASE)
    return m.group(1) if m else None


def days_until(date_str):
    try:
        return (datetime.strptime(date_str, '%Y-%m-%d') - datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).days
    except:
        return None


async def run():
    log(f'🚀 KataBump 自动续订 (服务器: {SERVER_ID})')
    server_url = f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}'

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-blink-features=AutomationControlled']
        )
        
        context = await browser.new_context(
            proxy={'server': HTTP_PROXY} if HTTP_PROXY else None,
            viewport={'width': 1280, 'height': 900},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        )
        
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        
        try:
            # 登录
            log('🔐 正在登录...')
            await page.goto(f'{DASHBOARD_URL}/auth/login', timeout=60000)
            await page.wait_for_timeout(2000)
            
            await page.locator('input[name="email"], input[type="email"]').fill(KATA_EMAIL)
            await page.locator('input[name="password"], input[type="password"]').fill(KATA_PASSWORD)
            await page.locator('button[type="submit"]').first.click()
            await page.wait_for_timeout(4000)
            
            if '/auth/login' in page.url:
                raise Exception('登录失败')
            log('✅ 登录成功')

            # 打开服务器页面
            log('📄 打开服务器页面')
            await page.goto(server_url, timeout=60000, wait_until='domcontentloaded')
            await page.wait_for_timeout(3000)
            
            old_expiry = get_expiry(await page.content()) or '未知'
            days = days_until(old_expiry)
            log(f'📅 当前到期: {old_expiry} (剩余 {days} 天)')

            # 点击 Renew 按钮
            log('🔄 点击 Renew 按钮...')
            renew_btn = page.locator('a:has-text("Renew"), button:has-text("Renew")').first
            
            if await renew_btn.count() == 0:
                log('⚠️ 未找到 Renew 按钮')
                await page.screenshot(path=f'{SCREENSHOT_DIR}/no_button.png', full_page=True)
                tg_notify_photo(f'{SCREENSHOT_DIR}/no_button.png', '⚠️ 未找到 Renew 按钮')
            else:
                await renew_btn.click()
                await page.wait_for_timeout(5000)
                
                # 检查结果
                current_url = page.url
                content = await page.content()
                
                log(f'📡 当前 URL: {current_url}')
                
                # 检查错误信息
                if 'renew-error' in current_url:
                    import urllib.parse
                    error = urllib.parse.unquote(current_url.split('renew-error=')[1].split('&')[0])
                    m = re.search(r'in (\d+) day', error)
                    if m:
                        log(f'⚠️ 还需等待 {m.group(1)} 天才能续订')
                    else:
                        log(f'⚠️ {error}')
                    await page.screenshot(path=f'{SCREENSHOT_DIR}/result.png', full_page=True)
                    tg_notify_photo(f'{SCREENSHOT_DIR}/result.png', f'⚠️ {error}')
                elif 'renew-success' in current_url:
                    log('✅ 续订成功')
                else:
                    # 检查页面内容
                    new_expiry = get_expiry(content) or '未知'
                    if new_expiry != old_expiry:
                        log(f'🎉 续订成功！{old_expiry} → {new_expiry}')
                        await page.screenshot(path=f'{SCREENSHOT_DIR}/result.png', full_page=True)
                        tg_notify_photo(f'{SCREENSHOT_DIR}/result.png', f'✅ 续订成功\n{old_expiry} → {new_expiry}')
                    else:
                        log(f'ℹ️ 到期时间未变: {new_expiry}')
                        await page.screenshot(path=f'{SCREENSHOT_DIR}/result.png', full_page=True)

        except Exception as e:
            log(f'❌ 错误: {e}')
            try:
                await page.screenshot(path=f'{SCREENSHOT_DIR}/error.png', full_page=True)
                tg_notify_photo(f'{SCREENSHOT_DIR}/error.png', f'❌ 出错: {e}')
            except:
                pass
            raise
        finally:
            await browser.close()


def main():
    log('=' * 50)
    log('   KataBump 自动续订')
    log('=' * 50)
    log(f'📧 邮箱: {KATA_EMAIL[:3]}***')
    log(f'🖥 服务器: {SERVER_ID}')
    log(f'🌐 代理: {HTTP_PROXY or "未配置"}')
    asyncio.run(run())
    log('🏁 完成')


if __name__ == '__main__':
    main()
