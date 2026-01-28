#!/usr/bin/env python3
"""
KataBump 自动续订 - SeleniumBase UC Mode 版本
支持 Cloudflare Turnstile 绕过
"""

import os
import sys
import time
import platform
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

# ==================== 配置 ====================

BASE_URL = "https://dashboard.katabump.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"

# ==================== 工具函数 ====================

def notify_telegram(ok: bool, stage: str, msg: str = "", screenshot_path: str = ""):
    """发送 Telegram 通知"""
    try:
        import urllib.request
        
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


def send_telegram_photo(token: str, chat_id: str, photo_path: str, caption: str):
    """发送 Telegram 图片"""
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
    except Exception as e:
        print(f"[WARN] 发送图片失败: {e}")


def screenshot(sb, name: str) -> str:
    """保存截图"""
    path = f"./{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[INFO] 截图已保存: {path}")
    except Exception as e:
        print(f"[WARN] 截图失败: {e}")
    return path


def wait_for_cloudflare(sb, timeout: int = 60) -> bool:
    """等待并处理 Cloudflare 验证"""
    print("[INFO] 检查 Cloudflare 验证...")
    
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            page_source = sb.get_page_source().lower()
            current_url = sb.get_current_url().lower()
            
            success_indicators = [
                "login" in current_url and "challenge" not in current_url,
                "dashboard" in current_url,
                "servers/edit" in current_url,
                "email" in page_source and "password" in page_source,
                "sign in" in page_source,
                "your server" in page_source,
            ]
            
            if any(success_indicators):
                print("[INFO] ✅ Cloudflare 验证通过")
                return True
            
            try:
                cookies = sb.get_cookies()
                if any(c.get("name") == "cf_clearance" for c in cookies):
                    print("[INFO] ✅ 已获取 cf_clearance cookie")
                    time.sleep(2)
                    return True
            except:
                pass
            
            time.sleep(1)
            
        except Exception as e:
            print(f"[WARN] 检查状态时出错: {e}")
            time.sleep(1)
    
    print("[WARN] ⚠️ Cloudflare 验证超时")
    return False


def check_turnstile_completed(sb) -> bool:
    """检查 Turnstile 是否已完成验证"""
    try:
        result = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return {completed: false, reason: 'no_modal'};
            
            // 检查 cf-turnstile-response 输入框
            const responseInput = modal.querySelector('input[name="cf-turnstile-response"]');
            if (responseInput && responseInput.value && responseInput.value.length > 20) {
                return {completed: true, reason: 'has_response', length: responseInput.value.length};
            }
            
            // 检查所有隐藏输入
            const hiddenInputs = modal.querySelectorAll('input[type="hidden"]');
            for (const input of hiddenInputs) {
                if (input.name && input.name.includes('turnstile') && input.value && input.value.length > 20) {
                    return {completed: true, reason: 'hidden_input', length: input.value.length};
                }
            }
            
            return {completed: false, reason: 'waiting'};
        """)
        
        print(f"[DEBUG] Turnstile 检查结果: {result}")
        return result and result.get('completed', False)
        
    except Exception as e:
        print(f"[DEBUG] 检查 Turnstile 状态出错: {e}")
        return False


def wait_for_turnstile_in_modal(sb, timeout: int = 60) -> bool:
    """等待对话框中的 Turnstile 验证完成"""
    print("[INFO] 等待对话框中的 Turnstile 验证...")
    
    start_time = time.time()
    clicked = False
    
    while time.time() - start_time < timeout:
        # 检查是否已完成
        if check_turnstile_completed(sb):
            print("[INFO] ✅ Turnstile 验证已完成")
            return True
        
        # 只点击一次
        if not clicked:
            print("[INFO] 尝试点击 Turnstile...")
            try:
                sb.uc_gui_click_captcha()
                print("[INFO] uc_gui_click_captcha 执行完成")
                clicked = True
                time.sleep(3)
            except Exception as e:
                print(f"[WARN] 点击 Turnstile 失败: {e}")
                clicked = True  # 避免重复尝试
        
        time.sleep(1)
    
    # 最终检查
    if check_turnstile_completed(sb):
        print("[INFO] ✅ Turnstile 验证已完成（最终检查）")
        return True
    
    print("[WARN] ⚠️ Turnstile 验证超时")
    return False


def submit_renew_form(sb) -> bool:
    """提交续订表单"""
    print("[INFO] 提交续订表单...")
    
    try:
        result = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return 'no_modal';
            
            const submitBtn = modal.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.click();
                return 'clicked';
            }
            
            return 'no_button';
        """)
        
        print(f"[DEBUG] 点击结果: {result}")
        
        if result == 'clicked':
            time.sleep(3)
            return True
            
    except Exception as e:
        print(f"[ERROR] 提交表单失败: {e}")
    
    return False


def check_renew_result(sb) -> dict:
    """检查续订结果"""
    try:
        time.sleep(2)
        
        current_url = sb.get_current_url()
        
        # 检查 URL 参数 - 最可靠的方式
        if "renew=success" in current_url:
            # 获取到期日期
            expiry_date = sb.execute_script("""
                const text = document.body.innerText;
                const match = text.match(/Expiry[\\s\\S]*?(\\d{4}-\\d{2}-\\d{2})/);
                return match ? match[1] : '';
            """) or ""
            return {"success": True, "error": False, "expiry_date": expiry_date}
        
        if "renew-error=" in current_url:
            parsed = urllib.parse.urlparse(current_url)
            params = urllib.parse.parse_qs(parsed.query)
            error_msg = params.get("renew-error", ["未知错误"])[0]
            return {"success": False, "error": True, "message": error_msg}
        
        # 检查页面内容
        page_source = sb.get_page_source()
        
        if "alert-success" in page_source and "renewed" in page_source.lower():
            return {"success": True, "error": False}
        
        if "please complete the captcha" in page_source.lower():
            return {"success": False, "error": True, "message": "Captcha 验证失败"}
        
        # 检查对话框是否关闭
        modal_closed = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            return !modal || !modal.classList.contains('show');
        """)
        
        return {"success": modal_closed, "error": False, "modal_closed": modal_closed}
        
    except Exception as e:
        print(f"[WARN] 检查结果时出错: {e}")
        return {"success": False, "error": True, "message": str(e)}


def fetch_servers_api(sb) -> List[Dict]:
    """通过 API 获取服务器列表"""
    try:
        result = sb.execute_script("""
            return fetch('/api-client/list-servers', { credentials: 'include' })
                .then(res => res.ok ? res.json() : null)
                .catch(() => null);
        """)
        if result and isinstance(result, list):
            return result
    except Exception as e:
        print(f"[WARN] API 获取服务器列表失败: {e}")
    return []


# ==================== 主函数 ====================

def main():
    username = os.environ.get("KATA_USERNAME", "")
    password = os.environ.get("KATA_PASSWORD", "")
    proxy_server = os.environ.get("PROXY_SERVER", "")
    
    if not username or not password:
        print("[ERROR] 请设置 KATA_USERNAME 和 KATA_PASSWORD")
        sys.exit(1)
    
    print("[INFO] ========================================")
    print("[INFO] KataBump 自动续订 - SeleniumBase UC Mode")
    print(f"[INFO] 系统: {platform.system()} {platform.release()}")
    print("[INFO] ========================================")
    
    results = []
    
    try:
        from seleniumbase import SB
        
        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": False,
            "uc_cdp_events": True,
        }
        
        if proxy_server:
            try:
                import urllib.request
                proxy_handler = urllib.request.ProxyHandler({
                    'http': proxy_server, 
                    'https': proxy_server
                })
                opener = urllib.request.build_opener(proxy_handler)
                opener.open("http://httpbin.org/ip", timeout=5)
                print(f"[INFO] 使用代理: {proxy_server}")
                sb_kwargs["proxy"] = proxy_server
            except:
                print("[WARN] 代理不可用，直接连接")
        
        with SB(**sb_kwargs) as sb:
            print("[INFO] 浏览器已启动")
            
            # ========== 1. 登录 ==========
            print("\n[INFO] ===== 步骤 1: 登录 =====")
            print(f"[INFO] 访问: {LOGIN_URL}")
            
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            time.sleep(5)
            
            if not wait_for_cloudflare(sb, timeout=60):
                sp = screenshot(sb, "01-cf-failed")
                notify_telegram(ok=False, stage="Cloudflare 验证失败", screenshot_path=sp)
                sys.exit(1)
            
            screenshot(sb, "01-login-page")
            
            current_url = sb.get_current_url()
            page_source = sb.get_page_source().lower()
            
            if "/auth/login" in current_url or ("email" in page_source and "password" in page_source):
                print("[INFO] 执行登录...")
                
                try:
                    sb.wait_for_element("input[name='email']", timeout=10)
                    
                    sb.uc_click("input[name='email']")
                    sb.type("input[name='email']", username)
                    time.sleep(0.5)
                    
                    sb.uc_click("input[name='password']")
                    sb.type("input[name='password']", password)
                    time.sleep(0.5)
                    
                    screenshot(sb, "02-login-filled")
                    
                    sb.uc_click("button[type='submit']")
                    time.sleep(5)
                    
                    wait_for_cloudflare(sb, timeout=30)
                    
                except Exception as e:
                    print(f"[ERROR] 登录操作失败: {e}")
                    sp = screenshot(sb, "02-login-error")
                    notify_telegram(ok=False, stage="登录操作失败", msg=str(e), screenshot_path=sp)
                    sys.exit(1)
                
                time.sleep(3)
                current_url = sb.get_current_url()
                
                if "/auth/login" not in current_url:
                    print("[INFO] ✅ 登录成功")
                else:
                    print("[ERROR] ❌ 登录失败")
                    sp = screenshot(sb, "02-login-failed")
                    notify_telegram(ok=False, stage="登录失败", screenshot_path=sp)
                    sys.exit(1)
            else:
                print("[INFO] ✅ 已登录状态")
            
            # ========== 2. 获取服务器列表 ==========
            print("\n[INFO] ===== 步骤 2: 获取服务器列表 =====")
            print(f"[INFO] 访问: {DASHBOARD_URL}")
            
            sb.uc_open_with_reconnect(DASHBOARD_URL, reconnect_time=4)
            time.sleep(3)
            
            wait_for_cloudflare(sb, timeout=30)
            screenshot(sb, "03-dashboard")
            
            servers_data = fetch_servers_api(sb)
            
            if not servers_data:
                print("[WARN] ⚠️ 未找到任何服务器")
                screenshot(sb, "03-no-servers")
                notify_telegram(ok=False, stage="获取服务器", msg="账号下没有服务器")
                sys.exit(0)
            
            print(f"\n[INFO] 找到 {len(servers_data)} 个服务器:")
            for s in servers_data:
                print(f"[INFO]   📦 {s.get('name', 'Unknown')} (ID: {s.get('id', 'N/A')})")
            
            # ========== 3. 处理每个服务器 ==========
            print("\n[INFO] ===== 步骤 3: 续订服务器 =====")
            
            for idx, server in enumerate(servers_data):
                server_id = server.get("id")
                server_name = server.get("name", "Unknown")
                
                print(f"\n[INFO] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print(f"[INFO] [{idx+1}/{len(servers_data)}] 处理: {server_name}")
                print(f"[INFO] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                detail_url = f"{BASE_URL}/servers/edit?id={server_id}"
                print(f"[INFO] 访问: {detail_url}")
                
                try:
                    sb.uc_open_with_reconnect(detail_url, reconnect_time=4)
                    time.sleep(3)
                    wait_for_cloudflare(sb, timeout=30)
                except Exception as e:
                    print(f"[WARN] 页面加载异常: {e}")
                
                screenshot(sb, f"04-server-{server_id}")
                
                # 检查是否可以续订
                can_renew = sb.execute_script("""
                    const text = document.body.innerText.toLowerCase();
                    return !text.includes("can't renew") && !text.includes("cannot renew");
                """)
                
                if not can_renew:
                    print("[INFO] ⏳ 当前无法续订（未到续订时间）")
                    results.append(f"⏳ {server_name}: 未到续订时间")
                    continue
                
                # 点击 Renew 按钮打开对话框
                print("[INFO] 点击 Renew 按钮打开对话框...")
                
                try:
                    clicked = sb.execute_script("""
                        const btn = document.querySelector('button[data-bs-target="#renew-modal"]');
                        if (btn) {
                            btn.click();
                            return true;
                        }
                        
                        const buttons = document.querySelectorAll('button');
                        for (const b of buttons) {
                            if (b.textContent.toLowerCase().includes('renew') && !b.closest('.modal')) {
                                b.click();
                                return true;
                            }
                        }
                        return false;
                    """)
                    
                    if not clicked:
                        print("[ERROR] 未找到 Renew 按钮")
                        results.append(f"❌ {server_name}: 未找到 Renew 按钮")
                        continue
                        
                except Exception as e:
                    print(f"[ERROR] 点击 Renew 按钮失败: {e}")
                    results.append(f"❌ {server_name}: 点击按钮失败")
                    continue
                
                time.sleep(2)
                screenshot(sb, f"05-dialog-{server_id}")
                
                # 等待 Turnstile 验证完成
                if not wait_for_turnstile_in_modal(sb, timeout=60):
                    sp = screenshot(sb, f"06-turnstile-timeout-{server_id}")
                    results.append(f"⚠️ {server_name}: Turnstile 验证超时")
                    
                    # 关闭对话框
                    try:
                        sb.execute_script("""
                            const closeBtn = document.querySelector('#renew-modal .btn-close');
                            if (closeBtn) closeBtn.click();
                        """)
                    except:
                        pass
                    continue
                
                screenshot(sb, f"06-turnstile-passed-{server_id}")
                time.sleep(1)
                
                # 提交表单
                if not submit_renew_form(sb):
                    print("[ERROR] 提交表单失败")
                    screenshot(sb, f"07-submit-failed-{server_id}")
                    results.append(f"❌ {server_name}: 提交表单失败")
                    continue
                
                time.sleep(3)
                screenshot(sb, f"08-result-{server_id}")
                
                # 检查结果
                result = check_renew_result(sb)
                print(f"[DEBUG] 续订结果: {result}")
                
                if result.get("success") and not result.get("error"):
                    print("[INFO] 🎉 续订成功！")
                    expiry = result.get("expiry_date", "")
                    if expiry:
                        print(f"[INFO] 到期日期: {expiry}")
                        results.append(f"🎉 {server_name}: 续订成功 (到期: {expiry})")
                    else:
                        results.append(f"🎉 {server_name}: 续订成功")
                    sp = screenshot(sb, f"09-success-{server_id}")
                    notify_telegram(ok=True, stage=f"续订成功 - {server_name}", 
                                   msg=f"到期: {expiry}" if expiry else "", screenshot_path=sp)
                else:
                    error_msg = result.get("message", "未知错误")
                    print(f"[ERROR] ❌ 续订失败: {error_msg}")
                    results.append(f"❌ {server_name}: {error_msg}")
                    sp = screenshot(sb, f"09-failed-{server_id}")
                    notify_telegram(ok=False, stage=f"续订失败 - {server_name}", 
                                   msg=error_msg, screenshot_path=sp)
                
                time.sleep(2)
            
            # ========== 汇总 ==========
            print("\n" + "=" * 50)
            print("[INFO] 执行结果汇总:")
            print("=" * 50)
            
            summary = "\n".join(results) if results else "无服务器处理"
            print(summary)
            
            success_count = sum(1 for r in results if "🎉" in r)
            fail_count = sum(1 for r in results if "❌" in r)
            skip_count = sum(1 for r in results if "⏳" in r or "⚠️" in r)
            
            notify_telegram(
                ok=(fail_count == 0),
                stage="执行完成",
                msg=f"成功: {success_count}, 失败: {fail_count}, 跳过: {skip_count}\n{summary}"
            )
            
            print("\n[INFO] 🏁 全部完成")
            
            if fail_count > 0:
                sys.exit(1)
            
    except ImportError as e:
        print(f"[ERROR] 缺少依赖: {e}")
        print("[INFO] 请安装: pip install seleniumbase")
        sys.exit(1)
        
    except Exception as e:
        print(f"[ERROR] 发生异常: {e}")
        import traceback
        traceback.print_exc()
        
        notify_telegram(ok=False, stage="异常", msg=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
