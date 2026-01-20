import asyncio
import os
import httpx
from datetime import datetime
from playwright.async_api import async_playwright

async def send_telegram_notification(bot_token, chat_id, username, screenshot_path):
    """发送 Telegram 通知"""
    
    # 获取当前时间
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 构建消息
    message = f"""🎁 Data Online 重启报告
⏰ {current_time}
━━━━━━━━━━━━━━━━━━
📅
├ 👤 账号: {username}
└ 重启: ✅ 完成"""
    
    async with httpx.AsyncClient() as client:
        # 发送图片和消息
        url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        
        with open(screenshot_path, 'rb') as photo:
            files = {'photo': ('result.png', photo, 'image/png')}
            data = {
                'chat_id': chat_id,
                'caption': message,
                'parse_mode': 'HTML'
            }
            
            response = await client.post(url, data=data, files=files)
            
            if response.status_code == 200:
                print("📨 Telegram 通知发送成功!")
            else:
                print(f"❌ Telegram 通知发送失败: {response.text}")

async def main():
    # 从环境变量获取凭据
    username = os.environ.get('DATA_USERNAME', 'apiorgvm')
    password = os.environ.get('DATA_PASSWORD')
    tg_bot_token = os.environ.get('TG_BOT_TOKEN')
    tg_chat_id = os.environ.get('TG_CHAT_ID')
    
    if not password:
        print("❌ 错误: DATA_PASSWORD 环境变量未设置")
        exit(1)
    
    base_url = "https://sv66.dataonline.vn:2222"
    command = 'pgrep -f "npm" >/dev/null || nohup ./npm -c config.yml >/dev/null 2>&1 &'
    
    async with async_playwright() as p:
        print("🚀 启动浏览器...")
        browser = await p.chromium.launch(
            headless=True,
            args=['--ignore-certificate-errors', '--no-sandbox']
        )
        
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        
        try:
            print(f"🌐 访问: {base_url}")
            await page.goto(base_url, timeout=60000)
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            
            print("🔐 正在登录...")
            await page.fill('div.Input#username input.Input__Text', username)
            print(f"  ✅ 用户名已填写: {username}")
            
            await page.fill('div.InputPassword#password input.InputPassword__Input', password)
            print("  ✅ 密码已填写")
            
            await page.click('button.Button[type="submit"]')
            print("  ✅ 点击登录按钮")
            
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(3)
            await page.screenshot(path="1_after_login.png")
            print("📸 登录后截图已保存")
            
            terminal_url = f"{base_url}/evo/user/terminal"
            print(f"📺 访问终端: {terminal_url}")
            await page.goto(terminal_url, timeout=60000)
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(5)
            await page.screenshot(path="2_terminal_page.png")
            print("📸 终端页面截图已保存")
            
            print(f"⌨️ 执行命令: {command}")
            
            for selector in ['.xterm', '.xterm-screen', '.terminal', 'canvas']:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        print(f"  ✅ 已点击终端区域: {selector}")
                        break
                except:
                    continue
            else:
                await page.mouse.click(640, 400)
            
            await asyncio.sleep(1)
            await page.keyboard.type(command, delay=30)
            await asyncio.sleep(0.5)
            await page.keyboard.press('Enter')
            print("  ✅ 命令已发送")
            
            await asyncio.sleep(5)
            await page.screenshot(path="final_result.png")
            print("📸 最终结果截图已保存")
            
            print("✅ 脚本执行完成!")
            
            # 发送 Telegram 通知
            if tg_bot_token and tg_chat_id:
                await send_telegram_notification(
                    tg_bot_token, 
                    tg_chat_id, 
                    username, 
                    "final_result.png"
                )
            else:
                print("⚠️ 未设置 Telegram 配置，跳过通知")
            
        except Exception as e:
            print(f"❌ 发生错误: {str(e)}")
            await page.screenshot(path="error_screenshot.png")
            raise
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
