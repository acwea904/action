#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

DASHBOARD_URL = 'https://hub.weirdhost.xyz'
SERVER_ID = os.environ.get('SERVER_ID', '734ad0d1')
WEIRDHOST_COOKIE = os.environ.get('WEIRDHOST_COOKIE', '')
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TG_CHAT_ID = os.environ.get('TG_CHAT_ID', '')


def log(msg):
    tz = timezone(timedelta(hours=8))
    t = datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')
    print(f'[{t}] {msg}')


def send_telegram(message):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    try:
        requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=30
        )
        log('✅ Telegram 通知已发送')
        return True
    except Exception as e:
        log(f'❌ Telegram 错误: {e}')
    return False


def run():
    log('🚀 Weirdhost 自动续期')
    log(f'🖥 服务器 ID: {SERVER_ID}')
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
        )
        
        context.add_cookies([{
            'name': 'remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d',
            'value': WEIRDHOST_COOKIE,
            'domain': 'hub.weirdhost.xyz',
            'path': '/',
            'secure': True,
            'httpOnly': True,
        }])
        
        page = context.new_page()
        
        try:
            log('🔐 访问服务器页面...')
            server_url = f'{DASHBOARD_URL}/server/{SERVER_ID}/'
            page.goto(server_url, wait_until='networkidle', timeout=60000)
            page.wait_for_timeout(5000)
            
            current_url = page.url
            log(f'📍 当前URL: {current_url}')
            
            if 'login' in current_url.lower():
                raise Exception('Cookie 已过期')
            
            log('🔄 查找续期按钮...')
            btn = page.locator("span:has-text('시간추가')").first
            btn.wait_for(timeout=15000)
            btn.click()
            log('🖱 已点击续期按钮')
            
            page.wait_for_timeout(3000)
            
            log('🎉 续期完成！')
            send_telegram(f'✅ Weirdhost 续期成功\n🖥 服务器: <code>{SERVER_ID}</code>')
            
        except Exception as e:
            log(f'❌ 错误: {e}')
            page.screenshot(path='error.png')
            send_telegram(f'❌ Weirdhost 续期失败\n🖥 服务器: <code>{SERVER_ID}</code>\n❗ {e}')
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    log('=' * 40)
    if not WEIRDHOST_COOKIE:
        log('❌ 请设置 WEIRDHOST_COOKIE')
        sys.exit(1)
    run()
    log('🏁 完成')
