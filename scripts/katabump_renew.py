#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KataBump 自动续订"""

import os
import sys
import re
import asyncio
import requests
from datetime import datetime, timezone, timedelta
from playwright.async_api import async_playwright

DASHBOARD_URL = 'https://dashboard.katabump.com'
KATA_EMAIL = os.environ.get('KATA_EMAIL', '')
KATA_PASSWORD = os.environ.get('KATA_PASSWORD', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_USER_ID', '')
SCREENSHOT_DIR = os.environ.get('SCREENSHOT_DIR', '/tmp')
PROXY_SERVER = os.environ.get('PROXY_SERVER', '')

CF_CHALLENGE_URL = 'https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/cmg/1'


def log(msg):
    t = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def send_telegram(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
                      json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'}, timeout=30)
    except:
        pass


def get_expiry(text):
    match = re.search(r'Expiry[\s\S]*?(\d{4}-\d{2}-\d{2})', text, re.IGNORECASE)
    return match.group(1) if match else None


def days_until(date_str):
    try:
        exp = datetime.strptime(date_str, '%Y-%m-%d')
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (exp - today).days
    except:
        return None


def parse_renew_error(url):
    if 'renew-error' not in url:
        return None
    from urllib.parse import unquote
    match = re.search(r'renew-error=([^&]+)', url)
    return unquote(match.group(1).replace('+', ' ')) if match else '续订受限'


def parse_servers(html):
    servers = []
    for match in re.finditer(r'/servers/edit\?id=([a-zA-Z0-9-]+)', html):
        sid = match.group(1)
        if sid not in [s['id'] for s in servers]:
            servers.append({'id': sid})
    return servers


async def refresh_cf_cookie(context):
    """刷新 CF Cookie"""
    log('🔄 刷新 CF Cookie...')
    page = await context.new_page()
    try:
        await page.goto(DASHBOARD_URL, timeout=30000)
        await page.wait_for_timeout(1000)
        await page.goto(CF_CHALLENGE_URL, timeout=30000)
        await page.wait_for_timeout(2000)
        cookies = await context.cookies()
        cfuvid = next((c['value'] for c in cookies if c['name'] == '_cfuvid'), None)
        log(f'✅ CF Cookie OK' if cfuvid else '⚠️ 未获取到 _cfuvid')
    except Exception as e:
        log(f'⚠️ CF Cookie 失败: {e}')
    finally:
        await page.close()


async def renew_server(page, server_id):
    """续订单个服务器"""
    log(f'📦 处理: {server_id}')
    
    await page.goto(f'{DASHBOARD_URL}/servers/edit?id={server_id}', timeout=60000)
    await page.wait_for_timeout(2000)
    
    url = page.url
    content = await page.content()
    expiry = get_expiry(content) or '未知'
    days = days_until(expiry)
    log(f'📅 到期: {expiry} (剩余 {days} 天)')
    
    error = parse_renew_error(url)
    if error:
        log(f'⏳ {error}')
        return {'id': server_id, 'expiry': expiry, 'days': days, 'status': 'limited', 'error': error}
    
    # 点击 Renew
    renew_btn = page.locator('button[data-bs-target="#renew-modal"], button:has-text("Renew")')
    if await renew_btn.count() == 0:
        return {'id': server_id, 'expiry': expiry, 'days': days, 'status': 'no_button'}
    
    await renew_btn.first.click()
    await page.wait_for_timeout(2000)
    
    # 等待模态框并提交
    modal = page.locator('#renew-modal')
    try:
        await modal.wait_for(state='visible', timeout=5000)
        await page.wait_for_timeout(1000)
        await page.locator('#renew-modal button[type="submit"]').first.click()
        await page.wait_for_timeout(5000)
    except:
        return {'id': server_id, 'expiry': expiry, 'days': days, 'status': 'modal_error'}
    
    # 检查结果
    if 'renew=success' in page.url:
        new_expiry = get_expiry(await page.content()) or '未知'
        log(f'🎉 成功！{expiry} → {new_expiry}')
        return {'id': server_id, 'expiry': new_expiry, 'days': days_until(new_expiry), 'status': 'success', 'old_expiry': expiry}
    
    error = parse_renew_error(page.url)
    if error:
        return {'id': server_id, 'expiry': expiry, 'days': days, 'status': 'error', 'error': error}
    
    # 重新检查
    await page.goto(f'{DASHBOARD_URL}/servers/edit?id={server_id}', timeout=60000)
    new_expiry = get_expiry(await page.content()) or expiry
    if new_expiry > expiry:
        log(f'🎉 成功！{expiry} → {new_expiry}')
        return {'id': server_id, 'expiry': new_expiry, 'days': days_until(new_expiry), 'status': 'success', 'old_expiry': expiry}
    
    return {'id': server_id, 'expiry': expiry, 'days': days, 'status': 'unknown'}


async def run():
    log('🚀 KataBump 自动续订')
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        
        context_options = {'viewport': {'width': 1280, 'height': 900}}
        if PROXY_SERVER:
            context_options['proxy'] = {'server': PROXY_SERVER}
        
        context = await browser.new_context(**context_options)
        
        try:
            await refresh_cf_cookie(context)
            page = await context.new_page()
            
            # 登录
            log('🔐 登录...')
            await page.goto(f'{DASHBOARD_URL}/auth/login', timeout=60000)
            await page.locator('input[name="email"]').fill(KATA_EMAIL)
            await page.locator('input[name="password"]').fill(KATA_PASSWORD)
            await page.locator('button[type="submit"]').first.click()
            await page.wait_for_timeout(3000)
            
            if '/auth/login' in page.url:
                raise Exception('登录失败')
            log('✅ 登录成功')
            
            # 获取服务器列表
            await page.goto(f'{DASHBOARD_URL}/servers', timeout=60000)
            await page.wait_for_timeout(2000)
            servers = parse_servers(await page.content())
            log(f'📦 找到 {len(servers)} 个服务器')
            
            if not servers:
                return
            
            # 续订
            results = []
            for server in servers:
                results.append(await renew_server(page, server['id']))
                await page.wait_for_timeout(1000)
            
            # 通知
            success = [r for r in results if r['status'] == 'success']
            msg = f'📊 KataBump\n✅ 成功: {len(success)}/{len(results)}'
            for r in success:
                msg += f"\n• {r['id'][:8]}... → {r['expiry']}"
            send_telegram(msg)
            
        except Exception as e:
            log(f'❌ {e}')
            send_telegram(f'❌ KataBump 出错: {e}')
            raise
        finally:
            await browser.close()


def main():
    if not KATA_EMAIL or not KATA_PASSWORD:
        log('❌ 请设置 KATA_EMAIL 和 KATA_PASSWORD')
        sys.exit(1)
    asyncio.run(run())


if __name__ == '__main__':
    main()
