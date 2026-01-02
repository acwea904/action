#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Weirdhost 自动续期脚本 (Playwright 版)
cron: 0 9,21 * * *
new Env('Weirdhost续期');
"""

import os
import sys
import requests
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright

# 配置
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
        # 使用 chromium，模拟真实浏览器
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
            ]
        )
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='ko-KR',
        )
        
        # 设置 Cookie
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
            # ========== 访问服务器页面 ==========
            log('🔐 访问服务器页面...')
            server_url = f'{DASHBOARD_URL}/server/{SERVER_ID}/'
            
            page.goto(server_url, wait_until='networkidle', timeout=60000)
            
            # 等待 CF 验证通过
            log('⏳ 等待页面加载...')
            page.wait_for_timeout(5000)
            
            current_url = page.url
            log(f'📍 当前URL: {current_url}')
            
            # 检查是否需要登录
            if 'login' in current_url.lower():
                raise Exception('Cookie 已过期，请更新 WEIRDHOST_COOKIE')
            
            # 检查 CF 挑战页面
            if 'challenge' in current_url or 'cdn-cgi' in current_url:
                log('⏳ 检测到 CF 验证，等待通过...')
                page.wait_for_url(f'**/server/{SERVER_ID}/**', timeout=30000)
            
            log('✅ 页面加载成功')
            
            # ========== 点击续期按钮 ==========
            log('🔄 查找续期按钮...')
            
            # 等待按钮出现 (시간추가 = 添加时间)
            btn_selector = "span:has-text('시간추가')"
            
            try:
                page.wait_for_selector(btn_selector, timeout=15000)
                log('✅ 找到续期按钮')
                
                # 点击按钮
                page.click(btn_selector)
                log('🖱 已点击续期按钮')
                
                # 等待响应
                page.wait_for_timeout(3000)
                
                # 检查结果
                log('🎉 续期操作完成！')
                send_telegram(
                    f'✅ Weirdhost 续期成功\n\n'
                    f'🖥 服务器: <code>{SERVER_ID}</code>\n'
                    f'🔗 <a href="{server_url}">查看详情</a>'
                )
                
            except Exception as e:
                log(f'⚠️ 未找到续期按钮: {e}')
                
                # 截图保存
                page.screenshot(path='debug.png')
                log('📸 已保存截图 debug.png')
                
                send_telegram(
                    f'⚠️ Weirdhost 续期失败\n\n'
                    f'🖥 服务器: <code>{SERVER_ID}</code>\n'
                    f'❗ 未找到续期按钮\n\n'
                    f'👉 <a href="{server_url}">手动续期</a>'
                )
        
        except Exception as e:
            log(f'❌ 错误: {e}')
            
            try:
                page.screenshot(path='error.png')
                log('📸 已保存错误截图')
            except:
                pass
            
            send_telegram(
                f'❌ Weirdhost 续期失败\n\n'
                f'🖥 服务器: <code>{SERVER_ID}</code>\n'
                f'❗ {e}'
            )
            raise
        
        finally:
            browser.close()


def main():
    log('=' * 50)
    log('   Weirdhost 自动续期脚本')
    log('=' * 50)
    
    if not WEIRDHOST_COOKIE:
        log('❌ 请设置 WEIRDHOST_COOKIE')
        sys.exit(1)
    
    run()
    log('🏁 完成')


if __name__ == '__main__':
    main()
