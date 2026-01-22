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
            headless=False,
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
            await page.wait_for_timeout(5000)
            
            old_expiry = get_expiry(await page.content()) or '未知'
            days = days_until(old_expiry)
            log(f'📅 当前到期: {old_expiry} (剩余 {days} 天)')

            # 点击 Renew 按钮打开模态框
            log('🖱 点击 Renew 按钮...')
            renew_btn = page.locator('button.btn-primary:has-text("Renew"), a.btn-primary:has-text("Renew")').first
            await renew_btn.wait_for(state='visible', timeout=10000)
            await renew_btn.click()
            await page.wait_for_timeout(2000)

            # 等待模态框出现
            await page.wait_for_selector('.modal.show', timeout=10000)
            log('✅ 模态框已打开')

            await page.screenshot(path=f'{SCREENSHOT_DIR}/modal.png', full_page=True)

            # 等待 Turnstile 验证完成
            log('⏳ 等待验证...')
            for i in range(60):
                await page.wait_for_timeout(1000)
                try:
                    val = await page.locator('.modal.show input[name="cf-turnstile-response"]').get_attribute('value', timeout=1000) or ''
                    if len(val) > 20:
                        log(f'✅ 验证完成 ({i+1}秒)')
                        break
                except:
                    pass
                if i % 10 == 9:
                    log(f'⏳ 等待中... ({i+1}秒)')
            else:
                raise Exception('验证超时')


            # 提交表单
            log('🖱 提交续订...')
            await page.locator('.modal.show form[action*="renew"] button[type="submit"], .modal.show button:has-text("Renew")').click()
            await page.wait_for_timeout(5000)

            # 检查结果
            await page.screenshot(path=f'{SCREENSHOT_DIR}/result.png', full_page=True)
            
            new_expiry = get_expiry(await page.content()) or '未知'
            if 'success' in page.url.lower() or new_expiry != old_expiry:
                log(f'🎉 续订成功！新到期: {new_expiry}')
                tg_notify_photo(f'{SCREENSHOT_DIR}/result.png', f'✅ 续订成功\n新到期: {new_expiry}')
            else:
                log(f'ℹ️ 到期时间: {new_expiry}')

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
