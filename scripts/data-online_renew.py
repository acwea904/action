# scripts/data-online_renew.py
import asyncio
import os
import httpx
from datetime import datetime
from playwright.async_api import async_playwright

async def send_telegram_notification(bot_token, chat_id, username, screenshot_path, success=True):
    """发送 Telegram 通知"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    status = "✅ 完成" if success else "❌ 失败"
    
    message = f"""🎁 Data Online 重启报告
⏰ {current_time}
━━━━━━━━━━━━━━━━━━
📅
├ 👤 账号: {username}
└ 重启: {status}"""
    
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

async def wait_for_login_success(page, timeout=30000):
    """等待登录成功，返回 True/False"""
    try:
        # 方法1: 等待URL变化（离开登录页面）
        await page.wait_for_function(
            "() => !window.location.pathname.includes('/login')",
            timeout=timeout
        )
        return True
    except:
        pass
    
    try:
        # 方法2: 检查是否出现用户菜单或仪表盘元素
        await page.wait_for_selector('.user-menu, .dashboard, .sidebar, nav', timeout=5000)
        return True
    except:
        pass
    
    return False

async def check_login_error(page):
    """检查是否有登录错误消息"""
    error_selectors = [
        '.error-message',
        '.alert-error',
        '.notification-error',
        '[class*="error"]',
        '[class*="Error"]'
    ]
    
    for selector in error_selectors:
        try:
            element = page.locator(selector).first
            if await element.is_visible(timeout=1000):
                text = await element.text_content()
                if text:
                    return text.strip()
        except:
            continue
    
    return None

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
    login_success = False
    
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
            
            # 等待Vue应用完全加载
            print("⏳ 等待页面完全加载...")
            await asyncio.sleep(3)
            
            # 等待登录表单出现
            try:
                await page.wait_for_selector('input', timeout=15000)
                print("  ✅ 登录表单已加载")
            except:
                print("  ❌ 登录表单未找到")
                await page.screenshot(path="error_no_form.png")
                raise Exception("登录表单未加载")
            
            await page.screenshot(path="0_login_page.png")
            print("📸 登录页面截图已保存")
            
            # 尝试多种选择器填写用户名
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
                        await element.fill('')  # 清空
                        await element.type(username, delay=50)  # 逐字符输入
                        
                        # 验证是否填写成功
                        value = await element.input_value()
                        if value == username:
                            print(f"  ✅ 用户名已填写: {username} (使用选择器: {selector})")
                            username_filled = True
                            break
                except Exception as e:
                    continue
            
            if not username_filled:
                await page.screenshot(path="error_username.png")
                raise Exception("无法填写用户名")
            
            # 尝试多种选择器填写密码
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
                        await element.fill('')  # 清空
                        await element.type(password, delay=50)  # 逐字符输入
                        
                        # 验证是否填写成功
                        value = await element.input_value()
                        if len(value) > 0:
                            print(f"  ✅ 密码已填写 (使用选择器: {selector})")
                            password_filled = True
                            break
                except Exception as e:
                    continue
            
            if not password_filled:
                await page.screenshot(path="error_password.png")
                raise Exception("无法填写密码")
            
            await page.screenshot(path="1_before_submit.png")
            
            # 点击登录按钮
            submit_clicked = False
            submit_selectors = [
                'button[type="submit"]',
                'button:has-text("Sign in")',
                'button:has-text("Login")',
                'button:has-text("登录")',
                '.Button[type="submit"]',
                'button.Button'
            ]
            
            for selector in submit_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        print(f"  ✅ 点击登录按钮 (使用选择器: {selector})")
                        submit_clicked = True
                        break
                except:
                    continue
            
            if not submit_clicked:
                # 尝试按 Enter 键提交
                await page.keyboard.press('Enter')
                print("  ⚠️ 使用 Enter 键提交")
            
            # 等待并验证登录结果
            print("⏳ 等待登录响应...")
            await asyncio.sleep(2)
            
            # 检查登录是否成功
            max_retries = 10
            for i in range(max_retries):
                await asyncio.sleep(1)
                current_url = page.url
                print(f"  🔍 当前URL: {current_url}")
                
                # 检查是否还在登录页面
                if '/login' not in current_url:
                    print("  ✅ 登录成功! URL已变化")
                    login_success = True
                    break
                
                # 检查是否有错误消息
                error_msg = await check_login_error(page)
                if error_msg:
                    print(f"  ❌ 登录失败: {error_msg}")
                    await page.screenshot(path="error_login_failed.png")
                    raise Exception(f"登录失败: {error_msg}")
                
                if i == max_retries - 1:
                    print("  ❌ 登录超时，仍在登录页面")
            
            await page.screenshot(path="2_after_login.png")
            print("📸 登录后截图已保存")
            
            if not login_success:
                raise Exception("登录失败：超时后仍在登录页面")
            
            # 访问终端页面
            terminal_url = f"{base_url}/evo/user/terminal"
            print(f"📺 访问终端: {terminal_url}")
            await page.goto(terminal_url, timeout=60000)
            await page.wait_for_load_state('networkidle')
            
            # 再次检查是否被重定向到登录页
            await asyncio.sleep(2)
            if '/login' in page.url:
                print("  ❌ 被重定向到登录页，登录状态丢失")
                await page.screenshot(path="error_redirected.png")
                raise Exception("访问终端时被重定向到登录页")
            
            print("  ✅ 成功进入终端页面")
            await asyncio.sleep(5)
            await page.screenshot(path="3_terminal_page.png")
            print("📸 终端页面截图已保存")
            
            # 执行命令
            print(f"⌨️ 执行命令: {command}")
            
            # 尝试点击终端区域
            terminal_clicked = False
            for selector in ['.xterm', '.xterm-screen', '.terminal', 'canvas', '.xterm-helper-textarea']:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=3000):
                        await element.click()
                        print(f"  ✅ 已点击终端区域: {selector}")
                        terminal_clicked = True
                        break
                except:
                    continue
            
            if not terminal_clicked:
                # 点击页面中心
                await page.mouse.click(640, 400)
                print("  ⚠️ 使用坐标点击页面中心")
            
            await asyncio.sleep(1)
            
            # 输入命令
            await page.keyboard.type(command, delay=30)
            await asyncio.sleep(0.5)
            await page.keyboard.press('Enter')
            print("  ✅ 命令已发送")
            
            await asyncio.sleep(5)
            await page.screenshot(path="final_result.png")
            print("📸 最终结果截图已保存")
            
            print("✅ 脚本执行完成!")
            
        except Exception as e:
            print(f"❌ 发生错误: {str(e)}")
            await page.screenshot(path="error_screenshot.png")
            
            # 发送失败通知
            if tg_bot_token and tg_chat_id:
                await send_telegram_notification(
                    tg_bot_token, 
                    tg_chat_id, 
                    username, 
                    "error_screenshot.png",
                    success=False
                )
            raise
        finally:
            await browser.close()
        
        # 发送成功通知
        if login_success and tg_bot_token and tg_chat_id:
            await send_telegram_notification(
                tg_bot_token, 
                tg_chat_id, 
                username, 
                "final_result.png",
                success=True
            )
        elif not tg_bot_token or not tg_chat_id:
            print("⚠️ 未设置 Telegram 配置，跳过通知")

if __name__ == '__main__':
    asyncio.run(main())
