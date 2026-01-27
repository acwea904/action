#!/usr/bin/env python3
"""
KataBump 自动续订 - Playwright 版本
使用账号密码登录（登录页无 CF 验证）
"""

import os
import sys
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as e:
    print(f"[ERROR] 缺少依赖: {e}")
    sys.exit(1)

# ==================== 配置 ====================

BASE_URL = "https://dashboard.katabump.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
RENEW_THRESHOLD_DAYS = 2

# ==================== 工具函数 ====================

def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_path: str = ""):
    """发送 Telegram 通知"""
    try:
        import urllib.request
        import urllib.parse
        
        token = os.environ.get("TG_BOT_TOKEN")
        chat_id = os.environ.get("TG_CHAT_ID")
        if not token or not chat_id:
            return
        
        status = "✅ 成功" if ok else "❌ 失败"
        text_lines = [
            f"🔔 KataBump 自动续订：{status}",
            f"阶段：{stage}",
        ]
        if msg:
            text_lines.append(f"信息：{msg}")
        text_lines.append(f"时间：{datetime.utcnow().isoformat()}")
        
        text = "\n".join(text_lines)
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true"
        }).encode()
        
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
        
        if screenshot_path and Path(screenshot_path).exists():
            send_telegram_photo(token, chat_id, screenshot_path, f"截图（{stage}）")
            
    except Exception as e:
        print(f"[WARN] Telegram 通知失败：{e}")


def send_telegram_photo(token: str, chat_id: str, photo_path: str, caption: str):
    """发送截图到 Telegram"""
    try:
        import urllib.request
        
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="screenshot.png"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
        
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        req = urllib.request.Request(url, data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=30)
        
    except Exception as e:
        print(f"[WARN] 发送截图失败：{e}")


def screenshot(name: str) -> str:
    return f"./{name}.png"


# ==================== 主函数 ====================

def main():
    # 获取账号密码
    username = os.environ.get("KATA_USERNAME", "")
    password = os.environ.get("KATA_PASSWORD", "")
    proxy_server = os.environ.get("PROXY_SERVER", "")
    force_renew = os.environ.get("FORCE_RENEW", "false").lower() == "true"
    
    if not username or not password:
        print("[ERROR] 请设置 KATA_USERNAME 和 KATA_PASSWORD")
        sys.exit(1)
    
    print("[INFO] 启动浏览器...")
    if proxy_server:
        print(f"[INFO] 使用代理: {proxy_server}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage"
            ]
        )
        
        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "locale": "en-US",
            "timezone_id": "America/New_York"
        }
        
        if proxy_server:
            context_options["proxy"] = {"server": proxy_server}
        
        context = browser.new_context(**context_options)
        page = context.new_page()
        
        # 反检测
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        
        try:
            # ========== 1. 访问登录页 ==========
            print("[INFO] 访问登录页...")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            
            print(f"[INFO] URL: {page.url}")
            print(f"[INFO] Title: {page.title()}")
            
            sp_login = screenshot("01-login-page")
            page.screenshot(path=sp_login, full_page=True)
            
            # ========== 2. 检查是否已登录 ==========
            if "/auth/login" not in page.url:
                print("[INFO] ✅ 已登录（可能有有效 session）")
            else:
                # ========== 3. 执行登录 ==========
                print("[INFO] 执行登录...")
                
                # 查找并填写用户名/邮箱
                email_selectors = [
                    "input[name='email']",
                    "input[type='email']",
                    "input[name='username']",
                    "input[placeholder*='mail']",
                    "input[placeholder*='user']",
                ]
                
                email_input = None
                for selector in email_selectors:
                    if page.locator(selector).count() > 0:
                        email_input = page.locator(selector).first
                        print(f"[INFO] 找到邮箱输入框: {selector}")
                        break
                
                if not email_input:
                    print("[ERROR] 未找到邮箱输入框")
                    sys.exit(1)
                
                # 查找密码输入框
                password_selectors = [
                    "input[name='password']",
                    "input[type='password']",
                ]
                
                password_input = None
                for selector in password_selectors:
                    if page.locator(selector).count() > 0:
                        password_input = page.locator(selector).first
                        print(f"[INFO] 找到密码输入框: {selector}")
                        break
                
                if not password_input:
                    print("[ERROR] 未找到密码输入框")
                    sys.exit(1)
                
                # 填写表单
                email_input.click()
                page.wait_for_timeout(300)
                email_input.fill(username)
                
                page.wait_for_timeout(500)
                
                password_input.click()
                page.wait_for_timeout(300)
                password_input.fill(password)
                
                page.wait_for_timeout(500)
                
                sp_filled = screenshot("02-form-filled")
                page.screenshot(path=sp_filled, full_page=True)
                
                # 查找登录按钮
                login_btn_selectors = [
                    "button[type='submit']",
                    "button:has-text('Login')",
                    "button:has-text('Sign in')",
                    "button:has-text('Log in')",
                    "input[type='submit']",
                ]
                
                login_btn = None
                for selector in login_btn_selectors:
                    if page.locator(selector).count() > 0:
                        login_btn = page.locator(selector).first
                        print(f"[INFO] 找到登录按钮: {selector}")
                        break
                
                if not login_btn:
                    print("[ERROR] 未找到登录按钮")
                    sys.exit(1)
                
                # 点击登录
                print("[INFO] 点击登录...")
                login_btn.click()
                
                # 等待登录完成
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                
                print(f"[INFO] 登录后 URL: {page.url}")
                
                sp_after_login = screenshot("03-after-login")
                page.screenshot(path=sp_after_login, full_page=True)
                
                # 检查登录是否成功
                if "/auth/login" in page.url:
                    print("[ERROR] ❌ 登录失败，请检查账号密码")
                    
                    # 检查错误信息
                    error_text = page.locator(".error, .alert-danger, [class*='error']").first
                    if error_text.count() > 0:
                        print(f"[ERROR] 错误信息: {error_text.inner_text()}")
                    
                    notify_telegram(
                        ok=False,
                        stage="登录失败",
                        msg="账号密码错误或登录被拒绝",
                        screenshot_path=sp_after_login
                    )
                    sys.exit(1)
                
                print("[INFO] ✅ 登录成功")
            
            # ========== 4. 访问 Dashboard ==========
            print("[INFO] 访问 Dashboard...")
            page.goto(BASE_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            
            sp_dashboard = screenshot("04-dashboard")
            page.screenshot(path=sp_dashboard, full_page=True)
            
            print(f"[INFO] Dashboard URL: {page.url}")
            
            # ========== 5. 获取服务器列表 ==========
            print("[INFO] 获取服务器列表...")
            
            # 打印调试信息
            all_links = page.locator("a[href]").all()
            print(f"[DEBUG] 页面共有 {len(all_links)} 个链接")
            
            servers = []
            
            # 尝试多种选择器
            server_selectors = [
                "a[href*='/server/']",
                "a[href*='/servers/']",
                "a[href*='/bot/']",
                "a[href*='/bots/']",
                "a[href*='/panel/']",
            ]
            
            for selector in server_selectors:
                try:
                    links = page.locator(selector).all()
                    if links:
                        print(f"[DEBUG] 选择器 {selector}: 找到 {len(links)} 个")
                    
                    for link in links:
                        href = link.get_attribute("href") or ""
                        match = re.search(r"/(server|bot|panel)[s]?/([a-zA-Z0-9]+)", href)
                        if match:
                            server_id = match.group(2)
                            name = link.inner_text().strip()[:30] or f"Server-{server_id}"
                            if server_id not in [s["id"] for s in servers]:
                                servers.append({
                                    "id": server_id,
                                    "name": name,
                                    "href": href
                                })
                except Exception as e:
                    print(f"[DEBUG] 选择器出错: {e}")
            
            # 调试：打印所有链接
            if not servers:
                print("[DEBUG] 未找到服务器，打印所有链接:")
                for link in all_links[:20]:
                    href = link.get_attribute("href") or ""
                    text = link.inner_text().strip()[:40]
                    if href and not href.startswith("#") and not href.startswith("javascript"):
                        print(f"[DEBUG]   {href} -> {text}")
                
                # 保存 HTML
                Path("page.html").write_text(page.content())
                print("[DEBUG] 页面 HTML 已保存到 page.html")
            
            if not servers:
                print("[WARN] 未找到服务器，尝试发送通知并退出")
                
                notify_telegram(
                    ok=False,
                    stage="获取服务器",
                    msg="未找到服务器，请检查截图",
                    screenshot_path=sp_dashboard
                )
                sys.exit(1)
            
            print(f"[INFO] 找到 {len(servers)} 个服务器")
            for s in servers:
                print(f"[INFO]   - {s['id']}: {s['name']}")
            
            # ========== 6. 处理每个服务器 ==========
            results = []
            
            for server in servers:
                server_id = server["id"]
                server_name = server["name"]
                server_href = server["href"]
                
                print(f"\n[INFO] ━━━ {server_name} (ID: {server_id}) ━━━")
                
                # 访问服务器页面
                full_url = server_href if server_href.startswith("http") else f"{BASE_URL}{server_href}"
                page.goto(full_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                
                print(f"[INFO] URL: {page.url}")
                
                sp_server = screenshot(f"05-server-{server_id}")
                page.screenshot(path=sp_server, full_page=True)
                
                # 获取页面文本
                page_text = page.content()
                
                # 查找到期时间
                expiry_date = None
                days_left = None
                
                patterns = [
                    r"(\d{4}-\d{2}-\d{2})\s*\(?\s*(\d+)\s*days?\s*(?:left|remaining)",
                    r"expires?\s*[:\s]*(\d{4}-\d{2}-\d{2})",
                    r"expiry\s*[:\s]*(\d{4}-\d{2}-\d{2})",
                    r"valid\s+until\s*[:\s]*(\d{4}-\d{2}-\d{2})",
                    r"(\d+)\s*days?\s*(?:left|remaining|until)",
                    r"renew\s+in\s+(\d+)\s*days?",
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, page_text, re.IGNORECASE)
                    if match:
                        groups = match.groups()
                        
                        if groups[0] and "-" in groups[0]:
                            expiry_str = groups[0]
                            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
                            days_left = (expiry_date - datetime.utcnow()).days
                            
                            if len(groups) >= 2 and groups[1]:
                                try:
                                    days_left = int(groups[1])
                                except:
                                    pass
                        elif groups[0] and groups[0].isdigit():
                            days_left = int(groups[0])
                            expiry_date = datetime.utcnow() + timedelta(days=days_left)
                        
                        if days_left is not None:
                            break
                
                if days_left is None:
                    print("[WARN] 无法获取到期时间")
                    results.append(f"⚠️ {server_name}: 无法获取状态")
                    continue
                
                expiry_str = expiry_date.strftime('%Y-%m-%d') if expiry_date else "N/A"
                print(f"[INFO] 到期: {expiry_str} | 剩余: {days_left} 天")
                
                # 判断是否需要续订
                need_renew = days_left <= RENEW_THRESHOLD_DAYS or force_renew
                
                if not need_renew:
                    print("[INFO] ✅ 无需续订")
                    results.append(f"✅ {server_name}: {days_left}天后到期")
                    continue
                
                # ========== 7. 执行续订 ==========
                reason = "强制续订" if force_renew else f"剩余{days_left}天"
                print(f"[INFO] 开始续订 ({reason})...")
                
                # 查找续订按钮
                renew_btn = None
                btn_selectors = [
                    "button:has-text('Renew')",
                    "a:has-text('Renew')",
                    "button:has-text('Extend')",
                    "a:has-text('Extend')",
                    "[class*='renew']",
                ]
                
                for selector in btn_selectors:
                    try:
                        if page.locator(selector).count() > 0:
                            renew_btn = page.locator(selector).first
                            print(f"[INFO] 找到续订按钮: {selector}")
                            break
                    except:
                        continue
                
                if not renew_btn:
                    print("[ERROR] 未找到续订按钮")
                    results.append(f"❌ {server_name}: 未找到续订按钮")
                    continue
                
                # 截图 - 续订前
                sp_before = screenshot(f"06-before-{server_id}")
                page.screenshot(path=sp_before, full_page=True)
                
                # 点击续订
                renew_btn.click()
                page.wait_for_timeout(3000)
                
                # 检查是否遇到 CF 验证
                if "challenge" in page.url or "cf-" in page.content().lower():
                    print("[WARN] ⚠️ 遇到 Cloudflare 验证")
                    sp_cf = screenshot(f"07-cf-challenge-{server_id}")
                    page.screenshot(path=sp_cf, full_page=True)
                    
                    results.append(f"⚠️ {server_name}: 遇到 CF 验证，需要手动续订")
                    
                    notify_telegram(
                        ok=False,
                        stage=f"CF 验证 - {server_name}",
                        msg="续订时遇到 Cloudflare 验证",
                        screenshot_path=sp_cf
                    )
                    continue
                
                # 检查确认对话框
                confirm_selectors = [
                    "button:has-text('Confirm')",
                    "button:has-text('Yes')",
                    "button:has-text('OK')",
                    ".modal button.btn-primary",
                    ".swal2-confirm",
                ]
                
                for selector in confirm_selectors:
                    try:
                        if page.locator(selector).count() > 0:
                            print(f"[INFO] 点击确认: {selector}")
                            page.locator(selector).first.click()
                            page.wait_for_timeout(2000)
                            break
                    except:
                        continue
                
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(2000)
                
                # 截图 - 续订后
                sp_after = screenshot(f"08-after-{server_id}")
                page.screenshot(path=sp_after, full_page=True)
                
                # 检查成功提示
                page_text_after = page.content().lower()
                success_indicators = ["success", "renewed", "extended", "successfully"]
                
                is_success = any(ind in page_text_after for ind in success_indicators)
                
                if is_success:
                    print(f"[INFO] ✅ 续订成功！")
                    results.append(f"🎉 {server_name}: 续订成功")
                    
                    notify_telegram(
                        ok=True,
                        stage=f"续订成功 - {server_name}",
                        msg="续订操作已完成",
                        screenshot_path=sp_after
                    )
                else:
                    print("[WARN] 续订状态未知")
                    results.append(f"⚠️ {server_name}: 续订状态未知")
                    
                    notify_telegram(
                        ok=False,
                        stage=f"续订未知 - {server_name}",
                        msg="请检查截图",
                        screenshot_path=sp_after
                    )
            
            # ========== 8. 汇总 ==========
            print("\n[INFO] " + "=" * 50)
            print("[INFO] 完成")
            
            summary = "\n".join(results)
            print(f"\n{summary}")
            
            if results:
                notify_telegram(ok=True, stage="执行完成", msg=summary)
            
            print("[INFO] 🏁 结束")
            
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            
            sp = screenshot("99-error")
            try:
                page.screenshot(path=sp, full_page=True)
            except:
                pass
            
            notify_telegram(
                ok=False,
                stage="异常",
                msg=str(e),
                screenshot_path=sp if Path(sp).exists() else ""
            )
            sys.exit(1)
            
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
