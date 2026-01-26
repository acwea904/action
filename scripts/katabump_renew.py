#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import asyncio
import requests
import time
import random
import math
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

# ================= 配置 =================

DASHBOARD_URL = 'https://dashboard.katabump.com'
SERVER_ID = os.environ.get('KATA_SERVER_ID') or ''
KATA_EMAIL = os.environ.get('KATA_EMAIL') or ''
KATA_PASSWORD = os.environ.get('KATA_PASSWORD') or ''

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ''
TG_CHAT_ID = os.environ.get('TG_USER_ID') or ''

SCREENSHOT_DIR = os.environ.get('SCREENSHOT_DIR') or '/tmp'


# ================= 工具函数 =================

def log(msg):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def tg_notify(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=20
        )
    except:
        pass


def tg_notify_photo(photo_path, caption=''):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        with open(photo_path, 'rb') as f:
            requests.post(
                f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto',
                data={'chat_id': TG_CHAT_ID, 'caption': caption},
                files={'photo': f},
                timeout=30
            )
    except:
        pass


def get_expiry_from_text(text):
    match = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', text, re.I)
    return match.group(1) if match else None


def days_until(date_str):
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


# ================= 人类行为模拟 =================

async def human_pause(min_ms=200, max_ms=900):
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_mouse_move(page, start, end, steps=25):
    x1, y1 = start
    x2, y2 = end

    for i in range(steps):
        t = i / steps
        curve = math.sin(t * math.pi) * random.uniform(-12, 12)
        x = x1 + (x2 - x1) * t + curve
        y = y1 + (y2 - y1) * t
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.01, 0.04))


async def human_click(page, locator):
    box = await locator.bounding_box()
    if not box:
        return False

    x = box['x'] + box['width'] / 2 + random.uniform(-5, 5)
    y = box['y'] + box['height'] / 2 + random.uniform(-5, 5)

    await human_mouse_move(
        page,
        (random.randint(0, 300), random.randint(0, 300)),
        (x, y)
    )
    await human_pause(200, 600)
    await page.mouse.down()
    await human_pause(80, 160)
    await page.mouse.up()
    return True


# ================= 主逻辑 =================

async def run():
    log('🚀 KataBump 自动续订（人类行为版）')
    log(f'🖥 服务器 ID: {SERVER_ID}')

    if not SERVER_ID:
        raise Exception('未设置 KATA_SERVER_ID')

    server_url = f'{DASHBOARD_URL}/servers/edit?id={SERVER_ID}'

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--window-size=1280,900',
            ]
        )

        context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='en-US',
            timezone_id='America/New_York',
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        )

        page = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            window.chrome = { runtime: {} };
        """)

        try:
            # 登录
            log('🔐 登录中...')
            await page.goto(f'{DASHBOARD_URL}/auth/login', timeout=60000)
            await human_pause(800, 1500)

            await page.fill('input[type="email"]', KATA_EMAIL)
            await human_pause()
            await page.fill('input[type="password"]', KATA_PASSWORD)
            await human_pause()

            await page.click('button[type="submit"]')
            await page.wait_for_timeout(4000)

            if '/auth/login' in page.url:
                raise Exception('登录失败')

            log('✅ 登录成功')

            # 打开服务器页
            await page.goto(server_url, timeout=60000)
            await page.wait_for_timeout(3000)

            content = await page.content()
            old_expiry = get_expiry_from_text(content) or '未知'
            days = days_until(old_expiry)
            log(f'📅 当前到期: {old_expiry} (剩余 {days} 天)')

            # 打开 Renew 模态框
            renew_btn = page.locator('button[data-bs-target="#renew-modal"]').first
            await renew_btn.click()
            await page.wait_for_timeout(2000)

            modal = page.locator('#renew-modal')
            await modal.wait_for(state='visible', timeout=5000)

            # ===== Turnstile 人类行为 =====
            log('🧠 检查 Turnstile')

            turnstile = modal.locator('iframe[src*="turnstile"]')
            turnstile_token = None

            if await turnstile.count() > 0:
                log('🛡 检测到 Turnstile')

                iframe = page.frame_locator('#renew-modal iframe[src*="turnstile"]').first
                checkbox = iframe.locator('input[type="checkbox"], .cb-i, #cf-stage').first

                await checkbox.wait_for(timeout=10000)
                await checkbox.scroll_into_view_if_needed()
                await human_pause(800, 1600)

                await human_click(page, checkbox)
                log('🖱 已模拟人类点击 Turnstile')

                response_input = page.locator('input[name="cf-turnstile-response"]')

                for i in range(30):
                    await asyncio.sleep(1)
                    if await response_input.count() > 0:
                        val = await response_input.get_attribute('value') or ''
                        if len(val) > 20:
                            turnstile_token = val
                            log('✅ Turnstile 验证通过')
                            break

                if not turnstile_token:
                    screenshot = os.path.join(SCREENSHOT_DIR, 'turnstile_failed.png')
                    await page.screenshot(path=screenshot, full_page=True)

                    if days is not None and days <= 3:
                        tg_notify_photo(
                            screenshot,
                            f'⚠️ Turnstile 未通过\n服务器: {SERVER_ID}\n到期: {old_expiry}\n👉 {server_url}'
                        )
                    return
            else:
                log('✅ 无 Turnstile')

            # 提交续订
            await human_pause(1000, 2000)
            await modal.locator('button[type="submit"]').click()
            await page.wait_for_timeout(5000)

            await page.goto(server_url)
            await page.wait_for_timeout(3000)

            content = await page.content()
            new_expiry = get_expiry_from_text(content) or '未知'

            screenshot = os.path.join(SCREENSHOT_DIR, 'result.png')
            await page.screenshot(path=screenshot, full_page=True)

            if new_expiry > old_expiry:
                log(f'🎉 续订成功，新到期: {new_expiry}')
                tg_notify_photo(
                    screenshot,
                    f'✅ KataBump 续订成功\n服务器: {SERVER_ID}\n原到期: {old_expiry}\n新到期: {new_expiry}'
                )
            else:
                log('⚠️ 续订未确认')
                if days is not None and days <= 2:
                    tg_notify_photo(
                        screenshot,
                        f'⚠️ 请检查续订状态\n服务器: {SERVER_ID}\n到期: {new_expiry}\n👉 {server_url}'
                    )

        except Exception as e:
            log(f'❌ 错误: {e}')
            try:
                screenshot = os.path.join(SCREENSHOT_DIR, 'error.png')
                await page.screenshot(path=screenshot, full_page=True)
                tg_notify_photo(screenshot, f'❌ 出错: {e}')
            except:
                pass
            tg_notify(f'❌ KataBump 出错\n服务器: {SERVER_ID}\n{e}')
            raise

        finally:
            await browser.close()


def main():
    log('=' * 50)
    log(' KataBump 自动续订（人类行为版）')
    log('=' * 50)

    if not KATA_EMAIL or not KATA_PASSWORD:
        log('❌ 缺少账号信息')
        sys.exit(1)

    asyncio.run(run())
    log('🏁 结束')


if __name__ == '__main__':
    main()
