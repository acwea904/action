#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

DASHBOARD_URL = "https://dashboard.katabump.com"
SERVER_ID = os.getenv("KATA_SERVER_ID", "")
EMAIL = os.getenv("KATA_EMAIL", "")
PASSWORD = os.getenv("KATA_PASSWORD", "")
SCREENSHOT_DIR = os.getenv("SCREENSHOT_DIR", "/tmp")

TURNSTILE_SITEKEY = "0x4AAAAAAA1IssKDXD0TRMjP"

def log(msg):
    tz = timezone(timedelta(hours=8))
    print(f"[{datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

async def preload_cloudflare(page):
    log("🛡 预热 Cloudflare Challenge")
    try:
        await page.goto(
            "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/cmg/1",
            wait_until="domcontentloaded",
            timeout=30000
        )
        await page.wait_for_timeout(2000)

        cookies = await page.context.cookies()
        cfuvid = [c for c in cookies if c["name"] == "_cfuvid"]
        if cfuvid:
            log(f"✅ _cfuvid 已生成: {cfuvid[0]['value'][:25]}...")
        else:
            log("⚠️ 未检测到 _cfuvid（可能已指纹绑定）")
    except Exception as e:
        log(f"⚠️ CF 预热失败: {e}")

def extract_expiry(html):
    m = re.search(r"Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})", html)
    return m.group(1) if m else None

def days_left(date_str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return (d - datetime.now()).days
    except:
        return None

async def run():
    if not all([EMAIL, PASSWORD, SERVER_ID]):
        raise Exception("缺少必要环境变量")

    server_url = f"{DASHBOARD_URL}/servers/edit?id={SERVER_ID}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            proxy={"server": "socks5://127.0.0.1:1080"},
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,900"
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US"
        )

        page = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            window.chrome = { runtime: {} };
        """)

        log("🔐 登录")
        await page.goto(f"{DASHBOARD_URL}/auth/login")
        await page.fill('input[type="email"]', EMAIL)
        await page.fill('input[type="password"]', PASSWORD)
        await page.click('button[type="submit"]')
        await page.wait_for_timeout(4000)

        if "/auth/login" in page.url:
            await page.screenshot(path=f"{SCREENSHOT_DIR}/login_failed.png", full_page=True)
            raise Exception("登录失败")

        log("✅ 登录成功")

        log("📄 打开服务器页面")
        await page.goto(server_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # 🔥 Cloudflare 预热（关键）
        await preload_cloudflare(page)

        html = await page.content()
        old_expiry = extract_expiry(html)
        log(f"📅 当前到期: {old_expiry}")

        log("🖱 点击 Renew")
        await page.click('button[data-bs-target="#renew-modal"]')
        await page.wait_for_timeout(2000)

        # 检查 Turnstile
        turnstile = page.locator('.cf-turnstile, iframe[src*="turnstile"]')
        if await turnstile.count() > 0:
            log("🛡 Turnstile 存在，等待自动完成")
            for i in range(20):
                await page.wait_for_timeout(1000)
                if await turnstile.count() == 0:
                    log("✅ Turnstile 已自动通过")
                    break
            else:
                await page.screenshot(path=f"{SCREENSHOT_DIR}/turnstile_failed.png", full_page=True)
                log("❌ Turnstile 未通过")
                return
        else:
            log("✅ 无 Turnstile")

        log("🖱 提交续订")
        await page.click('#renew-modal button[type="submit"]')
        await page.wait_for_timeout(5000)

        await page.goto(server_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        html = await page.content()
        new_expiry = extract_expiry(html)

        if new_expiry and new_expiry != old_expiry:
            log(f"🎉 续订成功，新到期: {new_expiry}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/success.png", full_page=True)
        else:
            log(f"⚠️ 续订结果未知: {new_expiry}")
            await page.screenshot(path=f"{SCREENSHOT_DIR}/result.png", full_page=True)

        await browser.close()

def main():
    log("=" * 50)
    log(" KataBump 自动续订")
    log("=" * 50)
    asyncio.run(run())
    log("🏁 完成")

if __name__ == "__main__":
    main()
