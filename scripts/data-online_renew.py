# scripts/data-online_renew.py
import asyncio
import os
import httpx
from datetime import datetime
from playwright.async_api import async_playwright

async def send_telegram_notification(bot_token, chat_id, username, screenshot_path, status="success", error_msg=None, command=None):
    """发送 Telegram 通知"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    if status == "success":
        status_text = "✅ 完成"
    elif status == "disabled":
        status_text = "🚫 账户已禁用"
    elif status == "wrong_password":
        status_text = "🔑 密码错误"
    else:
        status_text = f"❌ 失败: {error_msg or '未知错误'}"
    
    message = f"""🎁 Data Online 重启报告
⏰ {current_time}
━━━━━━━━━━━━━━━━━━
├ 👤 账号: {username}
├ 📝 命令: <code>{command or '无'}</code>
└ 状态: {status_text}"""
    
    async with httpx.AsyncClient() as client:
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

async def check_login_status(page):
    """检查登录状态，返回状态码和消息"""
    current_url = page.url
    
    if 'account-disabled' in current_url:
        return 'disabled', '账户已禁用'
    
    if 'wrong-password' in current_url or 'invalid' in current_url:
        return 'wrong_password', '密码错误'
    
    if '/login' not in current_url:
        return 'success', '登录成功'
    
    try:
        page_text = await page.text_content('body')
        if page_text:
            page_text_lower = page_text.lower()
            if 'disabled' in page_text_lower or '禁用' in page_text:
                return 'disabled', '账户已禁用'
            if 'wrong password' in page_text_lower or 'invalid' in page_text_lower:
                return 'wrong_password', '密码错误'
    except:
        pass
    
    return 'pending', '等待中'

async def main():
    # 从环境变量获取配置
    username = os.environ.get('DATA_USERNAME')
    password = os.environ.get('DATA_PASSWORD')
    command = os.environ.get('DATA_COMMAND', '')  # 自定义命令
    tg_bot_token = os.environ.get('TG_BOT_TOKEN')
    tg_chat_id = os.environ.get('TG_CHAT_ID')
    
    # 验证必需参数
    if not username:
        print("❌ 错误: DATA_USERNAME 环境变量未设置")
        exit(1)
    
    if not password:
        print("❌ 错误: DATA_PASSWORD 环境变量未设置")
        exit(1)
    
    if not command:
        print("❌ 错误: DATA_COMMAND 环境变量未设置")
        exit(1)
    
    base_url = "https://sv66.dataonline.vn:2222"
    final_status = "failed"
    error_message = None
    screenshot_file = "error_screenshot.png"
    
    print(f"📋 配置信息:")
    print(f"  👤 用户名: {username}")
    print(f"  📝 命令: {command}")
    
    async with async_playwright() as p:
        print("🚀 启动浏览器...")
        browser = await p.chromium.launch(
            headless=True,
            args=['--ignore-certificate-errors', '--no-sandbox']
        )
        
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        
        try:
            # 访问登录页面
            login_url = f"{base_url}/evo/login"
            print(f"🌐 访问: {login_url}")
            await page.goto(login_url, timeout=60000)
            await page.wait_for_load_state('networkidle')
            
            print("⏳ 等待页面完全加载...")
            await asyncio.sleep(3)
            
            try:
                await page.wait_for_selector('input', timeout=15000)
                print("  ✅ 登录表单已加载")
            except:
                print("  ❌ 登录表单未找到")
                await page.screenshot(path="error_no_form.png")
                screenshot_file = "error_no_form.png"
                raise Exception("登录表单未加载")
            
            await page.screenshot(path="0_login_page.png")
            
            # 填写用户名
            print("🔐 正在登录...")
            username_filled = False
            username_selectors = [
                '#username input',
                'input[placeholder*="username" i]',
                'input[name="username"]',
                'input[type="text"]',
                '.Input__Text',
                'div.Input input'
            ]
            
            for selector in username_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        await element.fill('')
                        await element.type(username, delay=50)
                        value = await element.input_value()
                        if value == username:
                            print(f"  ✅ 用户名已填写")
                            username_filled = True
                            break
                except:
                    continue
            
            if not username_filled:
                await page.screenshot(path="error_username.png")
                screenshot_file = "error_username.png"
                raise Exception("无法填写用户名")
            
            # 填写密码
            password_filled = False
            password_selectors = [
                '#password input',
                'input[type="password"]',
                'input[placeholder*="password" i]',
                '.InputPassword__Input',
                'div.InputPassword input'
            ]
            
            for selector in password_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        await element.fill('')
                        await element.type(password, delay=50)
                        value = await element.input_value()
                        if len(value) > 0:
                            print(f"  ✅ 密码已填写")
                            password_filled = True
                            break
                except:
                    continue
            
            if not password_filled:
                await page.screenshot(path="error_password.png")
                screenshot_file = "error_password.png"
                raise Exception("无法填写密码")
            
            # 点击登录按钮
            submit_selectors = [
                'button[type="submit"]',
                'button:has-text("Sign in")',
                'button:has-text("Login")',
                '.Button[type="submit"]'
            ]
            
            for selector in submit_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        print(f"  ✅ 点击登录按钮")
                        break
                except:
                    continue
            
            # 检查登录结果
            print("⏳ 等待登录响应...")
            await asyncio.sleep(3)
            
            max_retries = 10
            for i in range(max_retries):
                await asyncio.sleep(1)
                status, message = await check_login_status(page)
                current_url = page.url
                print(f"  🔍 状态: {status} - {message}")
                
                if status == 'disabled':
                    print("  🚫 账户已禁用!")
                    final_status = "disabled"
                    await page.screenshot(path="account_disabled.png")
                    screenshot_file = "account_disabled.png"
                    break
                
                elif status == 'wrong_password':
                    print("  🔑 密码错误!")
                    final_status = "wrong_password"
                    await page.screenshot(path="wrong_password.png")
                    screenshot_file = "wrong_password.png"
                    break
                
                elif status == 'success':
                    print("  ✅ 登录成功!")
                    final_status = "success"
                    break
                
                if i == max_retries - 1:
                    print("  ⚠️ 登录超时")
                    error_message = "登录超时"
            
            await page.screenshot(path="after_login.png")
            
            # 账户禁用或密码错误，直接结束
            if final_status in ['disabled', 'wrong_password']:
                print(f"⚠️ 无法继续执行: {final_status}")
            
            # 登录成功才继续执行终端操作
            elif final_status == 'success':
                terminal_url = f"{base_url}/evo/user/terminal"
                print(f"📺 访问终端: {terminal_url}")
                await page.goto(terminal_url, timeout=60000)
                await page.wait_for_load_state('networkidle')
                
                await asyncio.sleep(2)
                if '/login' in page.url:
                    print("  ❌ 被重定向到登录页")
                    final_status = "failed"
                    error_message = "会话失效"
                else:
                    print("  ✅ 成功进入终端页面")
                    await asyncio.sleep(5)
                    await page.screenshot(path="terminal_page.png")
                    
                    # 执行命令
                    print(f"⌨️ 执行命令: {command}")
                    
                    for selector in ['.xterm', '.xterm-screen', '.terminal', 'canvas']:
                        try:
                            element = page.locator(selector).first
                            if await element.is_visible(timeout=3000):
                                await element.click()
                                print(f"  ✅ 已点击终端区域")
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
                    screenshot_file = "final_result.png"
                    print("📸 最终结果截图已保存")
            
            print(f"📋 最终状态: {final_status}")
            
        except Exception as e:
            print(f"❌ 发生错误: {str(e)}")
            error_message = str(e)
            try:
                await page.screenshot(path="error_screenshot.png")
            except:
                pass
        
        finally:
            await browser.close()
        
        # 发送通知
        if tg_bot_token and tg_chat_id:
            await send_telegram_notification(
                tg_bot_token, 
                tg_chat_id, 
                username, 
                screenshot_file,
                status=final_status,
                error_msg=error_message,
                command=command
            )
        else:
            print("⚠️ 未设置 Telegram 配置，跳过通知")
        
        if final_status in ['disabled', 'wrong_password']:
            print(f"⚠️ 脚本结束: {final_status}")
            exit(0)
        elif final_status != 'success':
            exit(1)

if __name__ == '__main__':
    asyncio.run(main())
