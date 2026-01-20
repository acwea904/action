import asyncio
import os
from playwright.async_api import async_playwright

async def main():
    # 从环境变量获取凭据
    username = os.environ.get('DATA_USERNAME', 'apiorgvm')
    password = os.environ.get('DATA_PASSWORD')
    
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
            # 1. 访问登录页面
            print(f"🌐 访问: {base_url}")
            await page.goto(base_url, timeout=60000)
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(2)
            
            # 2. 登录
            print("🔐 正在登录...")
            
            # 填写用户名 - 使用精确选择器
            await page.fill('div.Input#username input.Input__Text', username)
            print(f"  ✅ 用户名已填写: {username}")
            
            # 填写密码
            await page.fill('div.InputPassword#password input.InputPassword__Input', password)
            print("  ✅ 密码已填写")
            
            # 点击登录按钮
            await page.click('button.Button[type="submit"]')
            print("  ✅ 点击登录按钮")
            
            # 等待登录完成
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(3)
            
            # 截图确认登录成功
            await page.screenshot(path="after_login.png")
            print("📸 登录后截图已保存")
            
            # 3. 导航到终端页面
            terminal_url = f"{base_url}/evo/user/terminal"
            print(f"📺 访问终端: {terminal_url}")
            await page.goto(terminal_url, timeout=60000)
            await page.wait_for_load_state('networkidle')
            await asyncio.sleep(5)
            
            # 截图终端页面
            await page.screenshot(path="terminal_page.png")
            print("📸 终端页面截图已保存")
            
            # 4. 在终端中执行命令
            print(f"⌨️ 执行命令: {command}")
            
            # 尝试点击终端区域激活
            terminal_selectors = [
                '.xterm',
                '.xterm-screen',
                '.terminal',
                'canvas',
                '.xterm-helper-textarea'
            ]
            
            clicked = False
            for selector in terminal_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click()
                        clicked = True
                        print(f"  ✅ 已点击终端区域: {selector}")
                        break
                except:
                    continue
            
            if not clicked:
                # 尝试点击页面中心
                await page.mouse.click(640, 400)
                print("  ⚠️ 点击页面中心激活终端")
            
            await asyncio.sleep(1)
            
            # 输入命令
            await page.keyboard.type(command, delay=30)
            await asyncio.sleep(0.5)
            await page.keyboard.press('Enter')
            print("  ✅ 命令已发送")
            
            # 等待命令执行
            await asyncio.sleep(5)
            
            # 最终截图
            await page.screenshot(path="final_result.png")
            print("📸 最终结果截图已保存")
            
            print("✅ 脚本执行完成!")
            
        except Exception as e:
            print(f"❌ 发生错误: {str(e)}")
            await page.screenshot(path="error_screenshot.png")
            raise
        
        finally:
            await browser.close()

if __name__ == '__main__':
    asyncio.run(main())
