#!/usr/bin/env python3
"""
KataBump 自动续订 - API + Playwright 版本
"""

import os
import sys
import re
import json
from datetime import datetime
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError as e:
    print(f"[ERROR] 缺少依赖: {e}")
    sys.exit(1)

# ==================== 配置 ====================

BASE_URL = "https://dashboard.katabump.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"
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
        text = "\n".join([
            f"🔔 KataBump：{status}",
            f"阶段：{stage}",
            f"信息：{msg}" if msg else "",
            f"时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        ])
        
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=10)
        
        if screenshot_path and Path(screenshot_path).exists():
            send_telegram_photo(token, chat_id, screenshot_path, stage)
    except Exception as e:
        print(f"[WARN] Telegram 通知失败：{e}")


def send_telegram_photo(token, chat_id, photo_path, caption):
    try:
        import urllib.request
        boundary = "----Boundary"
        with open(photo_path, "rb") as f:
            photo_data = f.read()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"screenshot.png\"\r\n"
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + photo_data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=body)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        urllib.request.urlopen(req, timeout=30)
    except:
        pass


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
    
    # 存储 API 响应
    servers_data = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        
        context_options = {
            "viewport": {"width": 1366, "height": 768},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }
        if proxy_server:
            print(f"[INFO] 使用代理: {proxy_server}")
            context_options["proxy"] = {"server": proxy_server}
        
        context = browser.new_context(**context_options)
        page = context.new_page()
        
        # 隐藏 webdriver
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false });")
        
        # ========== 拦截 API 响应 ==========
        def handle_response(response):
            nonlocal servers_data
            if "api-client/list-servers" in response.url:
                try:
                    data = response.json()
                    if isinstance(data, list):
                        servers_data = data
                        print(f"[INFO] ✅ 拦截到服务器列表: {len(data)} 个")
                except:
                    pass
        
        page.on("response", handle_response)
        
        try:
            # ========== 1. 登录 ==========
            print("[INFO] 访问登录页...")
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            
            if "/auth/login" in page.url:
                print("[INFO] 执行登录...")
                
                page.locator("input[name='email']").fill(username)
                page.wait_for_timeout(300)
                page.locator("input[name='password']").fill(password)
                page.wait_for_timeout(300)
                
                page.locator("button[type='submit']").click()
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                
                if "/auth/login" in page.url:
                    print("[ERROR] ❌ 登录失败")
                    sp = screenshot("01-login-failed")
                    page.screenshot(path=sp, full_page=True)
                    notify_telegram(ok=False, stage="登录失败", screenshot_path=sp)
                    sys.exit(1)
                
                print("[INFO] ✅ 登录成功")
            
            # ========== 2. 访问 Dashboard 触发 API ==========
            print("[INFO] 访问 Dashboard...")
            page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)  # 等待 API 请求完成
            
            sp_dashboard = screenshot("02-dashboard")
            page.screenshot(path=sp_dashboard, full_page=True)
            
            # ========== 3. 检查服务器列表 ==========
            if not servers_data:
                print("[WARN] ⚠️ 未拦截到服务器数据，尝试手动请求...")
                
                # 手动请求 API
                api_result = page.evaluate("""
                    async () => {
                        const res = await fetch('/api-client/list-servers', { credentials: 'include' });
                        return res.ok ? await res.json() : null;
                    }
                """)
                
                if api_result and isinstance(api_result, list):
                    servers_data = api_result
            
            if not servers_data:
                print("[WARN] ⚠️ 未找到任何服务器")
                notify_telegram(ok=False, stage="获取服务器", msg="账号下没有服务器")
                sys.exit(0)
            
            print(f"\n[INFO] 找到 {len(servers_data)} 个服务器:")
            for s in servers_data:
                print(f"[INFO]   📦 {s['name']} (ID: {s['id']}) - {s.get('location', 'N/A')}")
            
            # ========== 4. 处理每个服务器 ==========
            results = []
            
            for server in servers_data:
                server_id = server["id"]
                server_name = server["name"]
                
                print(f"\n[INFO] ━━━ {server_name} (ID: {server_id}) ━━━")
                
                # 访问服务器详情页
                detail_url = f"{BASE_URL}/servers/edit?id={server_id}"
                page.goto(detail_url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                
                sp_detail = screenshot(f"03-server-{server_id}")
                page.screenshot(path=sp_detail, full_page=True)
                
                # 获取页面文本
                page_text = page.inner_text("body")
                
                # 查找剩余天数
                days_left = None
                patterns = [
                    r"(\d+)\s*days?\s*(?:left|remaining)",
                    r"expires?\s*(?:in)?\s*(\d+)\s*days?",
                    r"renew\s*(?:in|every)?\s*(\d+)\s*days?",
                    r"(\d+)\s*days?\s*(?:until|before)",
                ]
                
                for pattern in patterns:
                    match = re.search(pattern, page_text, re.IGNORECASE)
                    if match:
                        days_left = int(match.group(1))
                        break
                
                if days_left is None:
                    print("[WARN] 无法获取到期时间")
                    print("[DEBUG] 页面关键词:")
                    for line in page_text.split("\n"):
                        if any(kw in line.lower() for kw in ["day", "expir", "renew"]):
                            print(f"[DEBUG]   {line.strip()[:60]}")
                    results.append(f"⚠️ {server_name}: 无法获取状态")
                    continue
                
                print(f"[INFO] 剩余: {days_left} 天")
                
                # 判断是否需要续订
                need_renew = days_left <= RENEW_THRESHOLD_DAYS or force_renew
                
                if not need_renew:
                    print("[INFO] ✅ 无需续订")
                    results.append(f"✅ {server_name}: {days_left}天后到期")
                    continue
                
                # ========== 5. 执行续订 ==========
                reason = "强制续订" if force_renew else f"剩余{days_left}天"
                print(f"[INFO] 开始续订 ({reason})...")
                
                # 查找续订按钮
                renew_btn = None
                for selector in ["button:has-text('Renew')", "a:has-text('Renew')", "button:has-text('Extend')"]:
                    if page.locator(selector).count() > 0:
                        renew_btn = page.locator(selector).first
                        print(f"[INFO] 找到按钮: {selector}")
                        break
                
                if not renew_btn:
                    print("[ERROR] 未找到续订按钮")
                    results.append(f"❌ {server_name}: 未找到续订按钮")
                    continue
                
                # 点击续订
                renew_btn.click()
                page.wait_for_timeout(3000)
                
                # 检查 CF 验证
                if "turnstile" in page.content().lower():
                    print("[WARN] ⚠️ 遇到 Cloudflare 验证")
                    sp_cf = screenshot(f"04-cf-{server_id}")
                    page.screenshot(path=sp_cf, full_page=True)
                    results.append(f"⚠️ {server_name}: CF 验证")
                    notify_telegram(ok=False, stage=f"CF 验证 - {server_name}", screenshot_path=sp_cf)
                    continue
                
                # 确认对话框
                for sel in ["button:has-text('Confirm')", "button:has-text('Yes')", ".swal2-confirm"]:
                    if page.locator(sel).count() > 0:
                        page.locator(sel).first.click()
                        page.wait_for_timeout(2000)
                        break
                
                page.wait_for_timeout(3000)
                
                sp_after = screenshot(f"05-after-{server_id}")
                page.screenshot(path=sp_after, full_page=True)
                
                # 检查结果
                if any(kw in page.inner_text("body").lower() for kw in ["success", "renewed", "extended"]):
                    print("[INFO] ✅ 续订成功！")
                    results.append(f"🎉 {server_name}: 续订成功")
                    notify_telegram(ok=True, stage=f"续订成功 - {server_name}", screenshot_path=sp_after)
                else:
                    print("[WARN] 状态未知")
                    results.append(f"⚠️ {server_name}: 状态未知")
            
            # ========== 6. 汇总 ==========
            print("\n" + "=" * 50)
            summary = "\n".join(results) if results else "无服务器"
            print(summary)
            notify_telegram(ok=True, stage="完成", msg=summary)
            
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
