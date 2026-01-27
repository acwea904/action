#!/usr/bin/env python3
"""
KataBump 自动续订 - Playwright 版本
"""

import os
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs

try:
    from playwright.sync_api import sync_playwright
except ImportError as e:
    print(f"[ERROR] 缺少依赖: {e}")
    sys.exit(1)

# ==================== 配置 ====================

BASE_URL = "https://dashboard.katabump.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
SERVERS_URL = f"{BASE_URL}/servers"
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
        
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)
        
        try:
            # ========== 1. 登录 ==========
            print("[INFO] 访问登录页...")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            
            print(f"[INFO] URL: {page.url}")
            
            if "/auth/login" in page.url:
                print("[INFO] 执行登录...")
                
                page.locator("input[name='email']").fill(username)
                page.wait_for_timeout(300)
                page.locator("input[name='password']").fill(password)
                page.wait_for_timeout(300)
                
                page.locator("button[type='submit']").click()
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                
                print(f"[INFO] 登录后 URL: {page.url}")
                
                if "/auth/login" in page.url:
                    print("[ERROR] ❌ 登录失败")
                    sp = screenshot("01-login-failed")
                    page.screenshot(path=sp, full_page=True)
                    notify_telegram(ok=False, stage="登录失败", screenshot_path=sp)
                    sys.exit(1)
                
                print("[INFO] ✅ 登录成功")
            
            # ========== 2. 访问服务器列表页 ==========
            print("[INFO] 访问服务器列表...")
            page.goto(SERVERS_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            
            print(f"[INFO] URL: {page.url}")
            
            sp_servers = screenshot("02-servers-page")
            page.screenshot(path=sp_servers, full_page=True)
            
            # ========== 3. 解析服务器表格 ==========
            print("[INFO] 解析服务器列表...")
            
            servers = []
            
            # 方法1: 从表格行解析
            rows = page.locator("table tbody tr").all()
            print(f"[DEBUG] 找到 {len(rows)} 个表格行")
            
            for row in rows:
                try:
                    # 获取 ID（第一列）
                    server_id = row.locator("td").nth(0).inner_text().strip()
                    
                    # 获取名称（第二列）
                    server_name = row.locator("td").nth(1).inner_text().strip()
                    
                    # 获取链接
                    link = row.locator("a[href*='edit']").first
                    href = link.get_attribute("href") if link.count() > 0 else ""
                    
                    if server_id and server_id.isdigit():
                        servers.append({
                            "id": server_id,
                            "name": server_name or f"Server-{server_id}",
                            "href": href or f"/servers/edit?id={server_id}"
                        })
                        print(f"[DEBUG] 找到服务器: ID={server_id}, Name={server_name}")
                except Exception as e:
                    print(f"[DEBUG] 解析行出错: {e}")
                    continue
            
            # 方法2: 从链接解析（备用）
            if not servers:
                print("[DEBUG] 尝试从链接解析...")
                links = page.locator("a[href*='edit?id=']").all()
                
                for link in links:
                    href = link.get_attribute("href") or ""
                    
                    # 解析 ?id=xxx
                    parsed = urlparse(href)
                    params = parse_qs(parsed.query)
                    
                    if "id" in params:
                        server_id = params["id"][0]
                        server_name = link.inner_text().strip() or f"Server-{server_id}"
                        
                        if server_id not in [s["id"] for s in servers]:
                            servers.append({
                                "id": server_id,
                                "name": server_name,
                                "href": href
                            })
            
            # ========== 4. 检查结果 ==========
            if not servers:
                print("[WARN] ⚠️ 未找到任何服务器")
                Path("page.html").write_text(page.content())
                notify_telegram(ok=False, stage="获取服务器", msg="未找到服务器", screenshot_path=sp_servers)
                sys.exit(0)
            
            print(f"[INFO] 找到 {len(servers)} 个服务器:")
            for s in servers:
                print(f"[INFO]   - ID: {s['id']} | 名称: {s['name']}")
            
            # ========== 5. 处理每个服务器 ==========
            results = []
            
            for server in servers:
                server_id = server["id"]
                server_name = server["name"]
                server_href = server["href"]
                
                print(f"\n[INFO] ━━━ {server_name} (ID: {server_id}) ━━━")
                
                # 访问服务器详情页
                full_url = server_href if server_href.startswith("http") else f"{BASE_URL}{server_href}"
                page.goto(full_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                
                print(f"[INFO] URL: {page.url}")
                
                sp_detail = screenshot(f"03-server-{server_id}")
                page.screenshot(path=sp_detail, full_page=True)
                
                # 获取页面文本
                page_text = page.inner_text("body")
                
                # 查找到期时间 / 剩余天数
                days_left = None
                
                # 模式匹配
                patterns = [
                    r"(\d+)\s*days?\s*(?:left|remaining)",
                    r"expires?\s*(?:in)?\s*(\d+)\s*days?",
                    r"renew\s*(?:in|every)?\s*(\d+)\s*days?",
                    r"valid\s*(?:for)?\s*(\d+)\s*days?",
                    r"(\d+)\s*days?\s*(?:until|before)",
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, page_text, re.IGNORECASE)
                    if match:
                        days_left = int(match.group(1))
                        print(f"[DEBUG] 匹配到: {match.group(0)}")
                        break
                
                # 查找日期格式
                if days_left is None:
                    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", page_text)
                    if date_match:
                        try:
                            expiry_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
                            days_left = (expiry_date - datetime.utcnow()).days
                        except:
                            pass
                
                if days_left is None:
                    print("[WARN] 无法获取到期时间")
                    
                    # 打印页面文本帮助调试
                    print("[DEBUG] 页面文本片段:")
                    for line in page_text.split("\n"):
                        if any(kw in line.lower() for kw in ["day", "expir", "renew", "valid"]):
                            print(f"[DEBUG]   {line.strip()[:80]}")
                    
                    results.append(f"⚠️ {server_name}: 无法获取状态")
                    continue
                
                print(f"[INFO] 剩余: {days_left} 天")
                
                # 判断是否需要续订
                need_renew = days_left <= RENEW_THRESHOLD_DAYS or force_renew
                
                if not need_renew:
                    print("[INFO] ✅ 无需续订")
                    results.append(f"✅ {server_name}: {days_left}天后到期")
                    continue
                
                # ========== 6. 执行续订 ==========
                reason = "强制续订" if force_renew else f"剩余{days_left}天"
                print(f"[INFO] 开始续订 ({reason})...")
                
                # 查找续订按钮
                renew_btn = None
                btn_selectors = [
                    "button:has-text('Renew')",
                    "a:has-text('Renew')",
                    "button:has-text('Extend')",
                    "a:has-text('Extend')",
                    "input[value*='Renew']",
                    "button.btn-success",
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
                    
                    # 打印所有按钮帮助调试
                    buttons = page.locator("button, a.btn, input[type='submit']").all()
                    print("[DEBUG] 页面按钮:")
                    for btn in buttons:
                        text = btn.inner_text().strip()[:30]
                        print(f"[DEBUG]   {text}")
                    
                    results.append(f"❌ {server_name}: 未找到续订按钮")
                    continue
                
                # 点击续订
                sp_before = screenshot(f"04-before-{server_id}")
                page.screenshot(path=sp_before, full_page=True)
                
                renew_btn.click()
                page.wait_for_timeout(3000)
                
                # 检查 CF 验证
                page_content = page.content().lower()
                if "challenge" in page.url or "turnstile" in page_content:
                    print("[WARN] ⚠️ 遇到 Cloudflare 验证")
                    sp_cf = screenshot(f"05-cf-{server_id}")
                    page.screenshot(path=sp_cf, full_page=True)
                    
                    results.append(f"⚠️ {server_name}: 遇到 CF 验证")
                    notify_telegram(ok=False, stage=f"CF 验证 - {server_name}", screenshot_path=sp_cf)
                    continue
                
                # 检查确认对话框
                confirm_selectors = ["button:has-text('Confirm')", "button:has-text('Yes')", "button:has-text('OK')", ".swal2-confirm"]
                
                for selector in confirm_selectors:
                    try:
                        if page.locator(selector).count() > 0:
                            print(f"[INFO] 点击确认: {selector}")
                            page.locator(selector).first.click()
                            page.wait_for_timeout(2000)
                            break
                    except:
                        continue
                
                page.wait_for_timeout(3000)
                
                sp_after = screenshot(f"06-after-{server_id}")
                page.screenshot(path=sp_after, full_page=True)
                
                # 检查结果
                result_text = page.inner_text("body").lower()
                if any(kw in result_text for kw in ["success", "renewed", "extended"]):
                    print("[INFO] ✅ 续订成功！")
                    results.append(f"🎉 {server_name}: 续订成功")
                    notify_telegram(ok=True, stage=f"续订成功 - {server_name}", screenshot_path=sp_after)
                else:
                    print("[WARN] 续订状态未知")
                    results.append(f"⚠️ {server_name}: 状态未知")
            
            # ========== 7. 汇总 ==========
            print("\n" + "=" * 50)
            summary = "\n".join(results) if results else "无服务器需要处理"
            print(summary)
            
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
            
            notify_telegram(ok=False, stage="异常", msg=str(e), screenshot_path=sp if Path(sp).exists() else "")
            sys.exit(1)
            
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
