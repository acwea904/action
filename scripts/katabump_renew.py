#!/usr/bin/env python3
"""
KataBump 自动续订 - 最终版
"""

import os
import sys
import re
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

# ==================== 工具函数 ====================

def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_path: str = ""):
    try:
        import urllib.request
        import urllib.parse
        
        token = os.environ.get("TG_BOT_TOKEN")
        chat_id = os.environ.get("TG_CHAT_ID")
        if not token or not chat_id:
            return
        
        status = "✅ 成功" if ok else "❌ 失败"
        text = "\n".join(filter(None, [
            f"🔔 KataBump：{status}",
            f"阶段：{stage}",
            f"信息：{msg}" if msg else "",
            f"时间：{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        ]))
        
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


def screenshot(page, name: str) -> str:
    path = f"./{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
    except:
        pass
    return path


# ==================== 主函数 ====================

def main():
    username = os.environ.get("KATA_USERNAME", "")
    password = os.environ.get("KATA_PASSWORD", "")
    proxy_server = os.environ.get("PROXY_SERVER", "")
    
    if not username or not password:
        print("[ERROR] 请设置 KATA_USERNAME 和 KATA_PASSWORD")
        sys.exit(1)
    
    print("[INFO] 启动浏览器...")
    
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
        
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => false });")
        
        # 拦截 API 响应
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
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            
            if "/auth/login" in page.url:
                print("[INFO] 执行登录...")
                
                page.locator("input[name='email']").fill(username)
                page.wait_for_timeout(300)
                page.locator("input[name='password']").fill(password)
                page.wait_for_timeout(300)
                
                page.locator("button[type='submit']").click()
                page.wait_for_timeout(5000)
                
                if "/auth/login" in page.url:
                    print("[ERROR] ❌ 登录失败")
                    sp = screenshot(page, "01-login-failed")
                    notify_telegram(ok=False, stage="登录失败", screenshot_path=sp)
                    sys.exit(1)
                
                print("[INFO] ✅ 登录成功")
            
            # ========== 2. 访问 Dashboard ==========
            print("[INFO] 访问 Dashboard...")
            page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            
            screenshot(page, "02-dashboard")
            
            if not servers_data:
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
                print(f"[INFO]   📦 {s['name']} (ID: {s['id']})")
            
            # ========== 3. 处理每个服务器 ==========
            results = []
            
            for server in servers_data:
                server_id = server["id"]
                server_name = server["name"]
                
                print(f"\n[INFO] ━━━ {server_name} (ID: {server_id}) ━━━")
                
                # 访问服务器详情页
                detail_url = f"{BASE_URL}/servers/edit?id={server_id}"
                print(f"[INFO] 访问: {detail_url}")
                
                try:
                    page.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"[WARN] 页面加载: {e}")
                
                screenshot(page, f"03-server-{server_id}")
                
                # ========== 步骤1: 点击底部 Renew 按钮 ==========
                print("[INFO] 步骤1: 查找底部 Renew 按钮...")
                
                # 底部的 Renew 按钮（在 Delete server 旁边）
                bottom_renew_btn = None
                
                # 尝试多种选择器
                selectors = [
                    "button:has-text('Renew'):near(:has-text('Delete server'))",
                    "button.btn-info:has-text('Renew')",
                    "button.btn-primary:has-text('Renew')",
                    "a.btn:has-text('Renew')",
                ]
                
                for sel in selectors:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            bottom_renew_btn = loc.first
                            print(f"[INFO] 找到按钮: {sel}")
                            break
                    except:
                        continue
                
                # 如果上面的选择器都不行，用更通用的方式
                if not bottom_renew_btn:
                    # 找所有包含 Renew 文字的按钮
                    all_renew = page.locator("button:has-text('Renew'), a:has-text('Renew')").all()
                    if all_renew:
                        # 取第一个（底部的）
                        bottom_renew_btn = all_renew[0]
                        print(f"[INFO] 找到 {len(all_renew)} 个 Renew 按钮，使用第一个")
                
                if not bottom_renew_btn:
                    print("[ERROR] 未找到底部 Renew 按钮")
                    results.append(f"❌ {server_name}: 未找到 Renew 按钮")
                    continue
                
                # 点击底部 Renew 按钮
                print("[INFO] 点击底部 Renew 按钮...")
                bottom_renew_btn.click()
                page.wait_for_timeout(2000)
                
                screenshot(page, f"04-dialog-{server_id}")
                
                # ========== 步骤2: 等待 Cloudflare Turnstile 验证 ==========
                print("[INFO] 步骤2: 等待 Cloudflare 验证...")
                
                # 等待验证通过（最多30秒）
                max_wait = 30
                verified = False
                
                for i in range(max_wait):
                    page_text = page.inner_text("body")
                    
                    # 检查是否显示 "成功!" 或验证通过的标志
                    if "成功" in page_text or "Success" in page_text:
                        print(f"[INFO] ✅ Cloudflare 验证通过！")
                        verified = True
                        break
                    
                    # 检查是否有绿色勾选标志
                    try:
                        # Turnstile 验证成功后会有特定的 class 或属性
                        if page.locator("[data-state='solved']").count() > 0:
                            print(f"[INFO] ✅ Cloudflare 验证通过！")
                            verified = True
                            break
                    except:
                        pass
                    
                    if i % 5 == 0:
                        print(f"[INFO] 等待验证... ({i}/{max_wait}秒)")
                    
                    page.wait_for_timeout(1000)
                
                if not verified:
                    # 再检查一次对话框中是否有 Renew 按钮可点击
                    dialog_renew = page.locator(".modal button:has-text('Renew'), .modal-content button:has-text('Renew'), div[role='dialog'] button:has-text('Renew')")
                    if dialog_renew.count() > 0:
                        print("[INFO] 对话框中找到 Renew 按钮，继续...")
                        verified = True
                    else:
                        print("[WARN] ⚠️ Cloudflare 验证超时")
                        sp = screenshot(page, f"05-cf-timeout-{server_id}")
                        results.append(f"⚠️ {server_name}: CF 验证超时")
                        notify_telegram(ok=False, stage=f"CF 验证超时 - {server_name}", screenshot_path=sp)
                        continue
                
                screenshot(page, f"05-verified-{server_id}")
                
                # ========== 步骤3: 点击对话框中的 Renew 按钮 ==========
                print("[INFO] 步骤3: 点击对话框中的 Renew 按钮...")
                
                page.wait_for_timeout(1000)
                
                # 对话框中的 Renew 按钮（蓝色，在 Close 旁边）
                dialog_renew_btn = None
                
                dialog_selectors = [
                    ".modal button.btn-primary:has-text('Renew')",
                    ".modal-content button:has-text('Renew')",
                    ".modal-footer button:has-text('Renew')",
                    "div[role='dialog'] button:has-text('Renew')",
                    ".modal button:has-text('Renew')",
                    # 更通用的：找 Close 按钮旁边的 Renew
                    "button:has-text('Renew'):right-of(button:has-text('Close'))",
                ]
                
                for sel in dialog_selectors:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            dialog_renew_btn = loc.first
                            print(f"[INFO] 找到对话框按钮: {sel}")
                            break
                    except:
                        continue
                
                # 如果还是找不到，找所有 Renew 按钮，取最后一个（对话框中的）
                if not dialog_renew_btn:
                    all_renew = page.locator("button:has-text('Renew')").all()
                    if len(all_renew) >= 2:
                        dialog_renew_btn = all_renew[-1]  # 最后一个是对话框中的
                        print(f"[INFO] 使用第 {len(all_renew)} 个 Renew 按钮")
                    elif len(all_renew) == 1:
                        dialog_renew_btn = all_renew[0]
                        print("[INFO] 只找到1个 Renew 按钮")
                
                if not dialog_renew_btn:
                    print("[ERROR] 未找到对话框中的 Renew 按钮")
                    sp = screenshot(page, f"06-no-dialog-btn-{server_id}")
                    results.append(f"❌ {server_name}: 未找到对话框 Renew 按钮")
                    continue
                
                # 点击对话框中的 Renew 按钮
                print("[INFO] 点击对话框 Renew 按钮...")
                dialog_renew_btn.click()
                page.wait_for_timeout(3000)
                
                # ========== 检查结果 ==========
                screenshot(page, f"07-result-{server_id}")
                
                result_text = page.inner_text("body").lower()
                
                # 检查成功标志
                success_keywords = ["success", "renewed", "extended", "successfully", "续订成功"]
                if any(kw in result_text for kw in success_keywords):
                    print("[INFO] 🎉 续订成功！")
                    results.append(f"🎉 {server_name}: 续订成功")
                    sp = screenshot(page, f"08-success-{server_id}")
                    notify_telegram(ok=True, stage=f"续订成功 - {server_name}", screenshot_path=sp)
                elif "error" in result_text or "failed" in result_text:
                    print("[ERROR] ❌ 续订失败")
                    results.append(f"❌ {server_name}: 续订失败")
                    sp = screenshot(page, f"08-failed-{server_id}")
                    notify_telegram(ok=False, stage=f"续订失败 - {server_name}", screenshot_path=sp)
                else:
                    # 检查对话框是否关闭（说明操作完成）
                    if page.locator(".modal:visible").count() == 0:
                        print("[INFO] ✅ 对话框已关闭，续订可能成功")
                        results.append(f"✅ {server_name}: 续订完成")
                        sp = screenshot(page, f"08-done-{server_id}")
                        notify_telegram(ok=True, stage=f"续订完成 - {server_name}", screenshot_path=sp)
                    else:
                        print("[WARN] ⚠️ 状态未知")
                        results.append(f"⚠️ {server_name}: 状态未知")
            
            # ========== 汇总 ==========
            print("\n" + "=" * 50)
            print("[INFO] 执行结果:")
            summary = "\n".join(results) if results else "无服务器"
            print(summary)
            
            notify_telegram(ok=True, stage="执行完成", msg=summary)
            print("\n[INFO] 🏁 完成")
            
        except Exception as e:
            print(f"[ERROR] {e}")
            import traceback
            traceback.print_exc()
            
            sp = screenshot(page, "99-error")
            notify_telegram(ok=False, stage="异常", msg=str(e), screenshot_path=sp if Path(sp).exists() else "")
            sys.exit(1)
            
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
