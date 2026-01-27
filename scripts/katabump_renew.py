#!/usr/bin/env python3
"""
KataBump 自动续订 - Playwright 版本
"""

import os
import sys
import json
import re
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
    from nacl import encoding, public
except ImportError as e:
    print(f"[ERROR] 缺少依赖: {e}")
    sys.exit(1)

# ==================== 配置 ====================

BASE_URL = "https://katabump.com"
RENEW_THRESHOLD_DAYS = 1

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


def parse_cookie_string(cookie_str: str, domain: str) -> list:
    """解析 cookie 字符串为 Playwright 格式"""
    if not cookie_str:
        return []
    
    cookies = []
    
    # 尝试 JSON 格式
    try:
        cookies_dict = json.loads(cookie_str)
        for name, value in cookies_dict.items():
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "secure": True,
                "sameSite": "Lax"
            })
        return cookies
    except json.JSONDecodeError:
        pass
    
    # 字符串格式
    for c in cookie_str.split(";"):
        c = c.strip()
        if "=" not in c:
            continue
        
        eq_index = c.index("=")
        name = c[:eq_index].strip()
        value = c[eq_index + 1:].strip()
        
        try:
            import urllib.parse
            value = urllib.parse.unquote(value)
        except:
            pass
        
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "sameSite": "Lax"
        })
    
    return cookies


def save_cookies_for_update(cookies: list):
    """保存 cookies"""
    import base64
    
    filtered = {c["name"]: c["value"] for c in cookies if "katabump" in c.get("domain", "")}
    
    if not filtered:
        return
    
    cookies_json = json.dumps(filtered)
    Path("new_cookies.txt").write_text(cookies_json)
    print(f"[INFO] 新 cookies 已保存")
    
    token = os.environ.get("REPO_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    
    if not token or not repo:
        return
    
    try:
        import urllib.request
        
        url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        })
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            key_data = json.loads(resp.read().decode())
        
        public_key = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(cookies_json.encode())
        encrypted_value = base64.b64encode(encrypted).decode()
        
        url = f"https://api.github.com/repos/{repo}/actions/secrets/KATA_COOKIES"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_data["key_id"]
        }).encode()
        
        req = urllib.request.Request(url, data=data, method="PUT", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"
        })
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in [201, 204]:
                print("[INFO] ✅ GitHub Secret 已更新")
                
    except Exception as e:
        print(f"[WARN] 更新 Secret 失败：{e}")


def screenshot(name: str) -> str:
    return f"./{name}.png"


# ==================== 主函数 ====================

def main():
    preset_cookies = os.environ.get("KATA_COOKIES", "")
    proxy_server = os.environ.get("PROXY_SERVER", "http://127.0.0.1:8080")
    force_renew = os.environ.get("FORCE_RENEW", "false").lower() == "true"
    
    print("[INFO] 启动浏览器...")
    if proxy_server:
        print("[INFO] 使用代理: 已启用")
    
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
        
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        
        try:
            # ========== 1. 注入 Cookies ==========
            if preset_cookies:
                print("[INFO] 注入预置 Cookies...")
                cookies = parse_cookie_string(preset_cookies, ".katabump.com")
                print(f"[INFO] 解析到 {len(cookies)} 个 cookies")
                if cookies:
                    context.add_cookies(cookies)
            
            # ========== 2. 访问 Dashboard ==========
            print("[INFO] 访问 Dashboard...")
            page.goto(f"{BASE_URL}/dashboard", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)
            
            current_url = page.url
            title = page.title()
            print(f"[INFO] URL: {current_url}")
            print(f"[INFO] Title: {title}")
            
            # 截图
            sp_init = screenshot("01-initial")
            page.screenshot(path=sp_init, full_page=True)
            
            # ========== 3. 检查登录状态 ==========
            # 检查是否在登录页或 404 页
            is_login_page = "/login" in current_url or "/auth" in current_url
            is_404 = "404" in title or "Not Found" in title
            
            if is_login_page:
                print("[ERROR] ❌ 未登录，需要更新 Cookies")
                notify_telegram(
                    ok=False,
                    stage="登录检查",
                    msg="Cookies 已过期，请更新 KATA_COOKIES",
                    screenshot_path=sp_init
                )
                sys.exit(1)
            
            if is_404:
                print("[WARN] Dashboard 返回 404，尝试其他路径...")
                
                # 尝试不同的路径
                alt_paths = [
                    "/en/dashboard",
                    "/panel",
                    "/en/panel",
                    "/bots",
                    "/en/bots",
                    "/",
                ]
                
                found_dashboard = False
                for path in alt_paths:
                    print(f"[INFO] 尝试: {BASE_URL}{path}")
                    page.goto(f"{BASE_URL}{path}", wait_until="networkidle", timeout=30000)
                    page.wait_for_timeout(2000)
                    
                    new_title = page.title()
                    new_url = page.url
                    print(f"[INFO]   -> Title: {new_title}")
                    
                    if "404" not in new_title and "Not Found" not in new_title:
                        if "/login" not in new_url and "/auth" not in new_url:
                            found_dashboard = True
                            print(f"[INFO] ✅ 找到有效页面: {new_url}")
                            break
                
                if not found_dashboard:
                    print("[ERROR] 无法找到有效的 Dashboard 页面")
                    sp = screenshot("02-no-dashboard")
                    page.screenshot(path=sp, full_page=True)
                    notify_telegram(
                        ok=False,
                        stage="页面访问",
                        msg="无法访问 Dashboard，可能 Cookies 已过期",
                        screenshot_path=sp
                    )
                    sys.exit(1)
            
            print("[INFO] ✅ 已登录")
            
            # ========== 4. 截图并保存 Cookies ==========
            sp_dashboard = screenshot("02-dashboard")
            page.screenshot(path=sp_dashboard, full_page=True)
            
            new_cookies = context.cookies()
            save_cookies_for_update(new_cookies)
            
            # ========== 5. 查找服务器/Bot 列表 ==========
            print("[INFO] 查找服务器列表...")
            
            # 打印页面上所有链接，帮助调试
            all_links = page.locator("a[href]").all()
            print(f"[DEBUG] 页面上共有 {len(all_links)} 个链接")
            
            interesting_hrefs = []
            for link in all_links[:30]:  # 只看前30个
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip()[:30]
                if any(kw in href.lower() for kw in ["server", "bot", "panel", "manage", "dashboard"]):
                    interesting_hrefs.append(f"{href} ({text})")
            
            if interesting_hrefs:
                print("[DEBUG] 相关链接:")
                for h in interesting_hrefs[:10]:
                    print(f"[DEBUG]   - {h}")
            
            # 尝试多种选择器查找服务器
            servers = []
            
            # 选择器列表 - 根据常见的 Discord bot hosting 面板
            server_selectors = [
                "a[href*='/server/']",
                "a[href*='/servers/']",
                "a[href*='/bot/']",
                "a[href*='/bots/']",
                "a[href*='/panel/']",
                "a[href*='/manage/']",
                "[class*='server'] a",
                "[class*='bot'] a",
                ".card a[href]",
                "tr a[href]",  # 表格中的链接
            ]
            
            for selector in server_selectors:
                try:
                    links = page.locator(selector).all()
                    for link in links:
                        href = link.get_attribute("href") or ""
                        # 提取 ID
                        match = re.search(r"/(server|bot|panel|manage)[s]?/(\d+)", href, re.IGNORECASE)
                        if match:
                            server_id = match.group(2)
                            name = link.inner_text().strip()[:30] or f"Bot-{server_id}"
                            if server_id not in [s["id"] for s in servers]:
                                servers.append({
                                    "id": server_id,
                                    "name": name,
                                    "href": href
                                })
                except:
                    continue
            
            # 如果还是没找到，尝试点击 Dashboard 链接
            if not servers:
                print("[INFO] 尝试点击 Dashboard 链接...")
                
                dashboard_links = page.locator("a:has-text('Dashboard'), a:has-text('Manage'), a:has-text('My Bots')").all()
                
                for dash_link in dashboard_links[:3]:
                    try:
                        dash_link.click()
                        page.wait_for_load_state("networkidle", timeout=10000)
                        page.wait_for_timeout(2000)
                        
                        print(f"[INFO] 点击后 URL: {page.url}")
                        
                        # 再次查找
                        for selector in server_selectors:
                            links = page.locator(selector).all()
                            for link in links:
                                href = link.get_attribute("href") or ""
                                match = re.search(r"/(server|bot|panel|manage)[s]?/(\d+)", href, re.IGNORECASE)
                                if match:
                                    server_id = match.group(2)
                                    name = link.inner_text().strip()[:30] or f"Bot-{server_id}"
                                    if server_id not in [s["id"] for s in servers]:
                                        servers.append({
                                            "id": server_id,
                                            "name": name,
                                            "href": href
                                        })
                        
                        if servers:
                            break
                    except:
                        continue
            
            if not servers:
                print("[ERROR] 未找到任何服务器/Bot")
                sp = screenshot("03-no-servers")
                page.screenshot(path=sp, full_page=True)
                
                # 打印页面 HTML 帮助调试
                html_content = page.content()
                Path("page_content.html").write_text(html_content)
                print("[DEBUG] 页面 HTML 已保存到 page_content.html")
                
                notify_telegram(
                    ok=False,
                    stage="获取服务器",
                    msg="未找到服务器，请检查截图",
                    screenshot_path=sp
                )
                sys.exit(1)
            
            print(f"[INFO] 找到 {len(servers)} 个服务器/Bot")
            for s in servers:
                print(f"[INFO]   - {s['id']}: {s['name']} ({s['href']})")
            
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
                
                print(f"[INFO] 访问: {page.url}")
                
                # 截图
                sp_server = screenshot(f"04-server-{server_id}")
                page.screenshot(path=sp_server, full_page=True)
                
                # 获取页面文本
                page_text = page.content()
                
                # 查找到期时间
                expiry_date = None
                days_left = None
                
                patterns = [
                    r"(\d{4}-\d{2}-\d{2})\s*\(?\s*(\d+)\s*days?\s*(?:left|remaining)",
                    r"expires?\s*:?\s*(\d{4}-\d{2}-\d{2})",
                    r"expiry\s*:?\s*(\d{4}-\d{2}-\d{2})",
                    r"valid\s+until\s*:?\s*(\d{4}-\d{2}-\d{2})",
                    r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})",  # MM/DD/YYYY 或 DD-MM-YYYY
                    r"(\d+)\s*days?\s*(?:left|remaining)",  # 只有天数
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, page_text, re.IGNORECASE)
                    if match:
                        groups = match.groups()
                        
                        if len(groups) >= 2 and len(groups[0]) == 4:
                            # YYYY-MM-DD 格式
                            expiry_str = groups[0]
                            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
                            days_left = (expiry_date - datetime.utcnow()).days
                            if len(groups) >= 2:
                                try:
                                    days_left = int(groups[1])
                                except:
                                    pass
                        elif len(groups) == 1 and groups[0].isdigit():
                            # 只有天数
                            days_left = int(groups[0])
                            expiry_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                            from datetime import timedelta
                            expiry_date += timedelta(days=days_left)
                        
                        if expiry_date or days_left:
                            break
                
                if expiry_date is None and days_left is None:
                    print("[WARN] 无法获取到期时间")
                    results.append(f"⚠️ {server_name}: 无法获取状态")
                    continue
                
                if days_left is None and expiry_date:
                    days_left = (expiry_date - datetime.utcnow()).days
                
                print(f"[INFO] 到期: {expiry_date.strftime('%Y-%m-%d') if expiry_date else 'N/A'} | 剩余: {days_left} 天")
                
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
                    "button:has-text('续订')",
                    "button:has-text('续期')",
                    "[class*='renew']",
                    "button.btn-success",
                    "button.btn-primary:has-text('Renew')",
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
                    sp = screenshot(f"05-no-btn-{server_id}")
                    page.screenshot(path=sp, full_page=True)
                    results.append(f"❌ {server_name}: 未找到续订按钮")
                    continue
                
                # 截图 - 续订前
                sp_before = screenshot(f"05-before-{server_id}")
                page.screenshot(path=sp_before, full_page=True)
                
                # 点击续订
                renew_btn.click()
                page.wait_for_timeout(3000)
                
                # 检查确认对话框
                confirm_selectors = [
                    "button:has-text('Confirm')",
                    "button:has-text('Yes')",
                    "button:has-text('OK')",
                    "button:has-text('确认')",
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
                
                page.wait_for_load_state("networkidle", timeout=10000)
                page.wait_for_timeout(2000)
                
                # 截图 - 续订后
                sp_after = screenshot(f"06-after-{server_id}")
                page.screenshot(path=sp_after, full_page=True)
                
                # 检查是否有成功提示
                success_indicators = ["success", "renewed", "extended", "成功"]
                page_text_after = page.content().lower()
                
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
                    print("[WARN] 续订状态未知，请检查截图")
                    results.append(f"⚠️ {server_name}: 续订状态未知")
                    
                    notify_telegram(
                        ok=False,
                        stage=f"续订未知 - {server_name}",
                        msg="请检查截图确认状态",
                        screenshot_path=sp_after
                    )
            
            # ========== 8. 汇总报告 ==========
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
