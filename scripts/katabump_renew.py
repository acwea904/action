#!/usr/bin/env python3
"""
KataBump 自动续订 - SeleniumBase UC Mode 版本
支持 Cloudflare Turnstile 绕过
"""

import os
import sys
import time
import platform
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

# ==================== 配置 ====================

BASE_URL = "https://dashboard.katabump.com"
LOGIN_URL = f"{BASE_URL}/auth/login"
DASHBOARD_URL = f"{BASE_URL}/dashboard"

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


def is_linux() -> bool:
    """检测是否为 Linux 系统"""
    return platform.system().lower() == "linux"


def setup_display():
    """设置 Linux 虚拟显示"""
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            print("[INFO] Linux: 已启动虚拟显示 (Xvfb)")
            return display
        except ImportError:
            print("[ERROR] 请安装: pip install pyvirtualdisplay")
            print("[ERROR] 以及: apt-get install -y xvfb")
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] 启动虚拟显示失败: {e}")
            sys.exit(1)
    return None


def screenshot(sb, name: str) -> str:
    """保存截图"""
    path = f"./{name}.png"
    try:
        sb.save_screenshot(path)
        print(f"[INFO] 截图已保存: {path}")
    except Exception as e:
        print(f"[WARN] 截图失败: {e}")
    return path


def wait_for_cloudflare(sb, timeout: int = 30) -> bool:
    """
    等待并处理 Cloudflare 验证
    返回 True 表示验证通过或无需验证
    """
    print("[INFO] 检查 Cloudflare 验证...")
    
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            page_source = sb.get_page_source().lower()
            
            # Cloudflare 验证指标
            cf_indicators = [
                "turnstile",
                "challenges.cloudflare",
                "just a moment",
                "verify you are human",
                "checking your browser",
                "cf-challenge"
            ]
            
            # 检查是否有 Cloudflare 验证
            has_cf = any(indicator in page_source for indicator in cf_indicators)
            
            if has_cf:
                print("[INFO] 检测到 Cloudflare 验证，尝试自动处理...")
                try:
                    # 使用 SeleniumBase UC Mode 的自动点击功能
                    sb.uc_gui_click_captcha()
                    time.sleep(3)
                except Exception as e:
                    print(f"[WARN] 点击验证码: {e}")
                
                # 等待一下再检查
                time.sleep(2)
            else:
                # 没有 Cloudflare 验证，检查页面是否正常加载
                if "login" in page_source or "dashboard" in page_source or "server" in page_source:
                    print("[INFO] ✅ Cloudflare 验证通过或无需验证")
                    return True
                
                # 检查是否有 cf_clearance cookie
                cookies = sb.get_cookies()
                if any(c.get("name") == "cf_clearance" for c in cookies):
                    print("[INFO] ✅ 已获取 cf_clearance cookie")
                    return True
            
            time.sleep(1)
            
        except Exception as e:
            print(f"[WARN] 检查 Cloudflare 状态时出错: {e}")
            time.sleep(1)
    
    print("[WARN] ⚠️ Cloudflare 验证超时")
    return False


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


def click_element_safe(sb, selector: str, description: str = "") -> bool:
    """安全点击元素"""
    try:
        if sb.is_element_visible(selector):
            sb.click(selector)
            print(f"[INFO] 点击成功: {description or selector}")
            return True
    except Exception as e:
        print(f"[WARN] 点击失败 ({description or selector}): {e}")
    return False


def find_and_click_renew_button(sb, button_type: str = "bottom") -> bool:
    """
    查找并点击 Renew 按钮
    button_type: "bottom" (页面底部) 或 "dialog" (对话框中)
    """
    selectors = []
    
    if button_type == "bottom":
        # 页面底部的 Renew 按钮
        selectors = [
            "button.btn-info:contains('Renew')",
            "button.btn-primary:contains('Renew')",
            "a.btn:contains('Renew')",
            "button:contains('Renew')",
        ]
    else:
        # 对话框中的 Renew 按钮
        selectors = [
            ".modal button.btn-primary:contains('Renew')",
            ".modal-content button:contains('Renew')",
            ".modal-footer button:contains('Renew')",
            "div[role='dialog'] button:contains('Renew')",
            ".modal button:contains('Renew')",
        ]
    
    for selector in selectors:
        try:
            # 使用 XPath 作为备选
            xpath_selector = f"//button[contains(text(), 'Renew')]"
            
            if sb.is_element_visible(selector):
                sb.click(selector)
                print(f"[INFO] 点击 {button_type} Renew 按钮成功")
                return True
        except:
            pass
    
    # 尝试 XPath
    try:
        if button_type == "dialog":
            xpath = "//div[contains(@class, 'modal')]//button[contains(text(), 'Renew')]"
        else:
            xpath = "//button[contains(text(), 'Renew')]"
        
        if sb.is_element_visible(xpath):
            sb.click(xpath)
            print(f"[INFO] 通过 XPath 点击 {button_type} Renew 按钮成功")
            return True
    except:
        pass
    
    return False


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
    
    # Linux 虚拟显示
    display = setup_display()
    
    results = []
    
    try:
        # 导入 SeleniumBase
        from seleniumbase import SB
        
        # 配置 SeleniumBase 参数
        sb_kwargs = {
            "uc": True,  # 启用 UC Mode (反检测)
            "test": True,
            "locale": "en",
            "headless": False if is_linux() else True,  # Linux 使用虚拟显示，不用 headless
        }
        
        # 添加代理
        if proxy_server:
            print(f"[INFO] 使用代理: {proxy_server}")
            sb_kwargs["proxy"] = proxy_server
        
        with SB(**sb_kwargs) as sb:
            print("[INFO] 浏览器已启动")
            
            # ========== 1. 登录 ==========
            print("\n[INFO] ===== 步骤 1: 登录 =====")
            print(f"[INFO] 访问: {LOGIN_URL}")
            
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=5.0)
            time.sleep(3)
            
            # 处理 Cloudflare
            if not wait_for_cloudflare(sb, timeout=30):
                sp = screenshot(sb, "01-cf-failed")
                notify_telegram(ok=False, stage="Cloudflare 验证失败", screenshot_path=sp)
                sys.exit(1)
            
            screenshot(sb, "01-login-page")
            
            # 检查是否需要登录
            current_url = sb.get_current_url()
            if "/auth/login" in current_url:
                print("[INFO] 执行登录...")
                
                try:
                    # 填写登录表单
                    sb.type("input[name='email']", username)
                    time.sleep(0.5)
                    sb.type("input[name='password']", password)
                    time.sleep(0.5)
                    
                    screenshot(sb, "02-login-filled")
                    
                    # 点击登录按钮
                    sb.click("button[type='submit']")
                    time.sleep(5)
                    
                    # 再次处理可能的 Cloudflare
                    wait_for_cloudflare(sb, timeout=20)
                    
                except Exception as e:
                    print(f"[ERROR] 登录操作失败: {e}")
                    sp = screenshot(sb, "02-login-error")
                    notify_telegram(ok=False, stage="登录操作失败", msg=str(e), screenshot_path=sp)
                    sys.exit(1)
                
                # 检查登录结果
                current_url = sb.get_current_url()
                if "/auth/login" in current_url:
                    print("[ERROR] ❌ 登录失败")
                    sp = screenshot(sb, "02-login-failed")
                    notify_telegram(ok=False, stage="登录失败", screenshot_path=sp)
                    sys.exit(1)
                
                print("[INFO] ✅ 登录成功")
            else:
                print("[INFO] ✅ 已登录状态")
            
            # ========== 2. 访问 Dashboard ==========
            print("\n[INFO] ===== 步骤 2: 获取服务器列表 =====")
            print(f"[INFO] 访问: {DASHBOARD_URL}")
            
            sb.uc_open_with_reconnect(DASHBOARD_URL, reconnect_time=3.0)
            time.sleep(3)
            
            wait_for_cloudflare(sb, timeout=20)
            screenshot(sb, "03-dashboard")
            
            # 获取服务器列表
            servers_data = fetch_servers_api(sb)
            
            if not servers_data:
                print("[WARN] ⚠️ 未找到任何服务器")
                notify_telegram(ok=False, stage="获取服务器", msg="账号下没有服务器")
                sys.exit(0)
            
            print(f"\n[INFO] 找到 {len(servers_data)} 个服务器:")
            for s in servers_data:
                print(f"[INFO]   📦 {s.get('name', 'Unknown')} (ID: {s.get('id', 'N/A')})")
            
            # ========== 3. 处理每个服务器 ==========
            print("\n[INFO] ===== 步骤 3: 续订服务器 =====")
            
            for server in servers_data:
                server_id = server.get("id")
                server_name = server.get("name", "Unknown")
                
                print(f"\n[INFO] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print(f"[INFO] 处理: {server_name} (ID: {server_id})")
                print(f"[INFO] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                # 访问服务器详情页
                detail_url = f"{BASE_URL}/servers/edit?id={server_id}"
                print(f"[INFO] 访问: {detail_url}")
                
                try:
                    sb.uc_open_with_reconnect(detail_url, reconnect_time=3.0)
                    time.sleep(3)
                    wait_for_cloudflare(sb, timeout=20)
                except Exception as e:
                    print(f"[WARN] 页面加载异常: {e}")
                
                screenshot(sb, f"04-server-{server_id}")
                
                # ========== 步骤 3.1: 点击底部 Renew 按钮 ==========
                print("[INFO] 查找底部 Renew 按钮...")
                
                if not find_and_click_renew_button(sb, "bottom"):
                    # 尝试通过 JavaScript 查找
                    try:
                        clicked = sb.execute_script("""
                            const buttons = document.querySelectorAll('button, a');
                            for (const btn of buttons) {
                                if (btn.textContent.includes('Renew') && 
                                    !btn.closest('.modal')) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        """)
                        if not clicked:
                            print("[ERROR] 未找到底部 Renew 按钮")
                            results.append(f"❌ {server_name}: 未找到 Renew 按钮")
                            continue
                    except Exception as e:
                        print(f"[ERROR] 查找按钮失败: {e}")
                        results.append(f"❌ {server_name}: 查找按钮失败")
                        continue
                
                time.sleep(2)
                screenshot(sb, f"05-dialog-{server_id}")
                
                # ========== 步骤 3.2: 等待 Cloudflare Turnstile ==========
                print("[INFO] 等待 Cloudflare Turnstile 验证...")
                
                # 等待对话框中的 Turnstile 验证
                turnstile_passed = False
                for i in range(30):
                    try:
                        page_source = sb.get_page_source().lower()
                        
                        # 检查是否有 Turnstile
                        if "turnstile" in page_source or "cf-turnstile" in page_source:
                            if i % 5 == 0:
                                print(f"[INFO] 等待 Turnstile 验证... ({i}/30秒)")
                            
                            # 尝试自动点击
                            try:
                                sb.uc_gui_click_captcha()
                            except:
                                pass
                        else:
                            turnstile_passed = True
                            break
                        
                        # 检查是否验证成功
                        if "success" in page_source or sb.is_element_visible("[data-state='solved']"):
                            turnstile_passed = True
                            print("[INFO] ✅ Turnstile 验证通过")
                            break
                            
                    except:
                        pass
                    
                    time.sleep(1)
                
                if not turnstile_passed:
                    # 检查对话框是否仍然存在
                    try:
                        if sb.is_element_visible(".modal") or sb.is_element_visible("div[role='dialog']"):
                            print("[INFO] 对话框存在，继续尝试...")
                            turnstile_passed = True
                    except:
                        pass
                
                if not turnstile_passed:
                    print("[WARN] ⚠️ Turnstile 验证超时")
                    sp = screenshot(sb, f"06-turnstile-timeout-{server_id}")
                    results.append(f"⚠️ {server_name}: Turnstile 验证超时")
                    notify_telegram(ok=False, stage=f"Turnstile 超时 - {server_name}", screenshot_path=sp)
                    continue
                
                screenshot(sb, f"06-turnstile-passed-{server_id}")
                time.sleep(1)
                
                # ========== 步骤 3.3: 点击对话框中的 Renew 按钮 ==========
                print("[INFO] 点击对话框中的 Renew 按钮...")
                
                if not find_and_click_renew_button(sb, "dialog"):
                    # 尝试通过 JavaScript
                    try:
                        clicked = sb.execute_script("""
                            const modal = document.querySelector('.modal, [role="dialog"]');
                            if (modal) {
                                const buttons = modal.querySelectorAll('button');
                                for (const btn of buttons) {
                                    if (btn.textContent.includes('Renew')) {
                                        btn.click();
                                        return true;
                                    }
                                }
                            }
                            // 备选：找所有 Renew 按钮，点击最后一个
                            const allBtns = document.querySelectorAll('button');
                            const renewBtns = Array.from(allBtns).filter(b => b.textContent.includes('Renew'));
                            if (renewBtns.length > 0) {
                                renewBtns[renewBtns.length - 1].click();
                                return true;
                            }
                            return false;
                        """)
                        if not clicked:
                            print("[ERROR] 未找到对话框 Renew 按钮")
                            sp = screenshot(sb, f"07-no-dialog-btn-{server_id}")
                            results.append(f"❌ {server_name}: 未找到对话框 Renew 按钮")
                            continue
                    except Exception as e:
                        print(f"[ERROR] 点击对话框按钮失败: {e}")
                        results.append(f"❌ {server_name}: 点击失败")
                        continue
                
                time.sleep(3)
                
                # ========== 检查结果 ==========
                screenshot(sb, f"08-result-{server_id}")
                
                try:
                    page_text = sb.get_page_source().lower()
                    
                    success_keywords = ["success", "renewed", "extended", "successfully", "续订成功"]
                    error_keywords = ["error", "failed", "失败"]
                    
                    if any(kw in page_text for kw in success_keywords):
                        print("[INFO] 🎉 续订成功！")
                        results.append(f"🎉 {server_name}: 续订成功")
                        sp = screenshot(sb, f"09-success-{server_id}")
                        notify_telegram(ok=True, stage=f"续订成功 - {server_name}", screenshot_path=sp)
                    elif any(kw in page_text for kw in error_keywords):
                        print("[ERROR] ❌ 续订失败")
                        results.append(f"❌ {server_name}: 续订失败")
                        sp = screenshot(sb, f"09-failed-{server_id}")
                        notify_telegram(ok=False, stage=f"续订失败 - {server_name}", screenshot_path=sp)
                    else:
                        # 检查对话框是否关闭
                        modal_visible = False
                        try:
                            modal_visible = sb.is_element_visible(".modal") or sb.is_element_visible("div[role='dialog']")
                        except:
                            pass
                        
                        if not modal_visible:
                            print("[INFO] ✅ 对话框已关闭，续订可能成功")
                            results.append(f"✅ {server_name}: 续订完成")
                            sp = screenshot(sb, f"09-done-{server_id}")
                            notify_telegram(ok=True, stage=f"续订完成 - {server_name}", screenshot_path=sp)
                        else:
                            print("[WARN] ⚠️ 状态未知")
                            results.append(f"⚠️ {server_name}: 状态未知")
                            
                except Exception as e:
                    print(f"[WARN] 检查结果时出错: {e}")
                    results.append(f"⚠️ {server_name}: 检查结果出错")
            
            # ========== 汇总 ==========
            print("\n" + "=" * 50)
            print("[INFO] 执行结果汇总:")
            print("=" * 50)
            
            summary = "\n".join(results) if results else "无服务器处理"
            print(summary)
            
            # 发送汇总通知
            success_count = sum(1 for r in results if "🎉" in r or "✅" in r)
            fail_count = sum(1 for r in results if "❌" in r)
            
            notify_telegram(
                ok=(fail_count == 0),
                stage="执行完成",
                msg=f"成功: {success_count}, 失败: {fail_count}\n{summary}"
            )
            
            print("\n[INFO] 🏁 全部完成")
            
    except ImportError as e:
        print(f"[ERROR] 缺少依赖: {e}")
        print("[ERROR] 请安装: pip install seleniumbase")
        sys.exit(1)
        
    except Exception as e:
        print(f"[ERROR] 发生异常: {e}")
        import traceback
        traceback.print_exc()
        
        notify_telegram(ok=False, stage="异常", msg=str(e))
        sys.exit(1)
        
    finally:
        # 清理虚拟显示
        if display:
            try:
                display.stop()
                print("[INFO] 虚拟显示已关闭")
            except:
                pass


if __name__ == "__main__":
    main()
