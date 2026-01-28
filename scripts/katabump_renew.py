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
    """等待并处理 Cloudflare 验证（登录页面）"""
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
            print(f"[WARN] 检查 Cloudflare 状态时出错: {e}")
            time.sleep(1)
  
    print("[WARN] ⚠️ Cloudflare 验证超时")
    return False


def click_turnstile_checkbox(sb) -> bool:
    """
    点击 Turnstile checkbox
    使用多种方法尝试
    """
    print("[INFO] 尝试点击 Turnstile checkbox...")
  
    # 方法1: 使用 SeleniumBase 的 uc_gui_click_captcha
    try:
        sb.uc_gui_click_captcha()
        print("[INFO] uc_gui_click_captcha 执行成功")
        time.sleep(3)
        return True
    except Exception as e:
        print(f"[DEBUG] uc_gui_click_captcha 失败: {e}")
  
    # 方法2: 查找并点击 iframe
    try:
        # 获取 Turnstile iframe 的位置并点击
        result = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return {error: 'no_modal'};
          
            const turnstileDiv = modal.querySelector('.cf-turnstile');
            if (!turnstileDiv) return {error: 'no_turnstile_div'};
          
            const iframe = turnstileDiv.querySelector('iframe');
            if (!iframe) return {error: 'no_iframe'};
          
            const rect = iframe.getBoundingClientRect();
            return {
                found: true,
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
                width: rect.width,
                height: rect.height
            };
        """)
      
        print(f"[DEBUG] Turnstile iframe 信息: {result}")
      
        if result and result.get('found'):
            # 使用 pyautogui 或 ActionChains 点击
            try:
                from selenium.webdriver.common.action_chains import ActionChains
              
                # 获取 iframe 元素
                iframe = sb.find_element("#renew-modal .cf-turnstile iframe")
              
                # 使用 ActionChains 点击 iframe 中心
                actions = ActionChains(sb.driver)
                actions.move_to_element(iframe).click().perform()
                print("[INFO] ActionChains 点击 iframe 成功")
                time.sleep(3)
                return True
            except Exception as e:
                print(f"[DEBUG] ActionChains 点击失败: {e}")
              
    except Exception as e:
        print(f"[DEBUG] 查找 iframe 失败: {e}")
  
    # 方法3: 使用 uc_click 点击 iframe
    try:
        sb.uc_click("#renew-modal .cf-turnstile iframe")
        print("[INFO] uc_click iframe 成功")
        time.sleep(3)
        return True
    except Exception as e:
        print(f"[DEBUG] uc_click iframe 失败: {e}")
  
    # 方法4: 切换到 iframe 内部点击
    try:
        iframe_selector = "#renew-modal .cf-turnstile iframe"
        if sb.is_element_present(iframe_selector):
            sb.switch_to_frame(iframe_selector)
            time.sleep(1)
          
            # 尝试点击 checkbox
            checkbox_selectors = [
                "input[type='checkbox']",
                "#challenge-stage input",
                "body",
            ]
          
            for sel in checkbox_selectors:
                try:
                    if sb.is_element_present(sel):
                        sb.click(sel)
                        print(f"[INFO] 在 iframe 内点击 {sel} 成功")
                        sb.switch_to_default_content()
                        time.sleep(3)
                        return True
                except:
                    pass
          
            sb.switch_to_default_content()
    except Exception as e:
        print(f"[DEBUG] 切换 iframe 失败: {e}")
        try:
            sb.switch_to_default_content()
        except:
            pass
  
    # 方法5: 使用 JavaScript 模拟点击
    try:
        sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return;
          
            const iframe = modal.querySelector('.cf-turnstile iframe');
            if (!iframe) return;
          
            // 创建并触发鼠标事件
            const rect = iframe.getBoundingClientRect();
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
          
            const clickEvent = new MouseEvent('click', {
                view: window,
                bubbles: true,
                cancelable: true,
                clientX: x,
                clientY: y
            });
          
            iframe.dispatchEvent(clickEvent);
        """)
        print("[INFO] JavaScript 模拟点击执行")
        time.sleep(3)
    except Exception as e:
        print(f"[DEBUG] JavaScript 点击失败: {e}")
  
    return False


def wait_for_turnstile_in_modal(sb, timeout: int = 45) -> bool:
    """
    等待对话框中的 Turnstile 验证完成
    先点击 checkbox，然后等待验证完成
    """
    print("[INFO] 等待对话框中的 Turnstile 验证...")
  
    start_time = time.time()
    clicked = False
  
    while time.time() - start_time < timeout:
        try:
            # 检查 Turnstile 是否已完成（response 有值）
            result = sb.execute_script("""
                const modal = document.getElementById('renew-modal');
                if (!modal) return {status: 'no_modal'};
              
                // 检查 cf-turnstile-response 输入框
                const responseInput = modal.querySelector('input[name="cf-turnstile-response"]');
                if (responseInput && responseInput.value && responseInput.value.length > 10) {
                    return {status: 'completed', value_length: responseInput.value.length};
                }
              
                // 检查是否有 iframe（等待点击）
                const iframe = modal.querySelector('.cf-turnstile iframe');
                if (iframe) {
                    // 检查 iframe 内是否显示成功
                    const turnstileDiv = modal.querySelector('.cf-turnstile');
                    const divContent = turnstileDiv ? turnstileDiv.innerHTML : '';
                  
                    // 检查是否有隐藏的 response（有些情况下验证完成但 input 在 shadow DOM 中）
                    const allInputs = modal.querySelectorAll('input[type="hidden"]');
                    for (const input of allInputs) {
                        if (input.name && input.name.includes('turnstile') && input.value && input.value.length > 10) {
                            return {status: 'completed_hidden', value_length: input.value.length};
                        }
                    }
                  
                    return {status: 'waiting', has_iframe: true};
                }
              
                return {status: 'unknown'};
            """)
          
            print(f"[DEBUG] Turnstile 状态: {result}")
          
            if result and result.get('status') in ['completed', 'completed_hidden']:
                print("[INFO] ✅ Turnstile 验证已完成")
                return True
          
            # 如果还在等待，尝试点击 checkbox
            if result and result.get('status') == 'waiting' and not clicked:
                print("[INFO] Turnstile 等待点击，尝试点击 checkbox...")
                click_turnstile_checkbox(sb)
                clicked = True
                time.sleep(2)
                continue
          
            # 如果已经点击过但还在等待，可能需要再次点击
            if result and result.get('status') == 'waiting' and clicked:
                elapsed = time.time() - start_time
                # 每 10 秒重试点击一次
                if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                    print("[INFO] 重试点击 Turnstile...")
                    click_turnstile_checkbox(sb)
          
            time.sleep(1)
          
        except Exception as e:
            print(f"[DEBUG] 检查 Turnstile 状态出错: {e}")
            time.sleep(1)
  
    # 超时后最终检查
    try:
        final_check = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return false;
          
            const input = modal.querySelector('input[name="cf-turnstile-response"]');
            if (input && input.value && input.value.length > 10) return true;
          
            // 检查所有隐藏输入
            const inputs = modal.querySelectorAll('input[type="hidden"]');
            for (const i of inputs) {
                if (i.value && i.value.length > 50) return true;
            }
          
            return false;
        """)
        if final_check:
            print("[INFO] ✅ Turnstile 验证已完成（最终检查）")
            return True
    except:
        pass
  
    print("[WARN] ⚠️ Turnstile 验证超时")
    return False


def submit_renew_form(sb) -> bool:
    """提交续订表单"""
    print("[INFO] 提交续订表单...")
  
    try:
        # 点击对话框中的 Renew 按钮
        result = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return 'no_modal';
          
            // 查找提交按钮
            const submitBtn = modal.querySelector('button[type="submit"]');
            if (submitBtn) {
                submitBtn.click();
                return 'clicked_submit';
            }
          
            // 备选：查找包含 Renew 文字的按钮
            const buttons = modal.querySelectorAll('button');
            for (const btn of buttons) {
                if (btn.textContent.toLowerCase().includes('renew') && 
                    !btn.classList.contains('btn-close') &&
                    !btn.classList.contains('btn-secondary')) {
                    btn.click();
                    return 'clicked_renew_btn';
                }
            }
          
            return 'no_button';
        """)
      
        print(f"[DEBUG] 点击结果: {result}")
      
        if result in ['clicked_submit', 'clicked_renew_btn']:
            time.sleep(3)
            return True
      
        # 方法2: 直接提交表单
        result2 = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return 'no_modal';
          
            const form = modal.querySelector('form');
            if (form) {
                form.submit();
                return 'form_submitted';
            }
            return 'no_form';
        """)
      
        print(f"[DEBUG] 表单提交结果: {result2}")
      
        if result2 == 'form_submitted':
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
        page_source = sb.get_page_source()
      
        success_indicators = [
            "success" in page_source.lower(),
            "renewed" in page_source.lower(),
            "extended" in page_source.lower(),
            "alert-success" in page_source,
        ]
      
        error_indicators = [
            "error" in page_source.lower() and "alert-danger" in page_source,
            "failed" in page_source.lower(),
        ]
      
        modal_closed = sb.execute_script("""
            const modal = document.getElementById('renew-modal');
            if (!modal) return true;
            return !modal.classList.contains('show');
        """)
      
        expiry_info = sb.execute_script("""
            const rows = document.querySelectorAll('.row');
            for (const row of rows) {
                if (row.textContent.includes('Expiry')) {
                    return row.textContent;
                }
            }
            return '';
        """)
      
        return {
            "success": any(success_indicators) or modal_closed,
            "error": any(error_indicators),
            "modal_closed": modal_closed,
            "expiry_info": expiry_info,
            "url": current_url
        }
      
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
            import urllib.request
            try:
                proxy_handler = urllib.request.ProxyHandler({'http': proxy_server, 'https': proxy_server})
                opener = urllib.request.build_opener(proxy_handler)
                opener.open("http://httpbin.org/ip", timeout=5)
                print(f"[INFO] 使用代理: {proxy_server}")
                sb_kwargs["proxy"] = proxy_server
            except:
                print(f"[WARN] 代理不可用，直接连接")
      
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
                sp = screenshot(sb, "03-no-servers")
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
              
                # 步骤 3.1: 点击底部 Renew 按钮打开对话框
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
                            if (b.textContent.toLowerCase().includes('renew') && 
                                !b.closest('.modal')) {
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
              
                # 步骤 3.2: 等待 Turnstile 验证完成（包含点击）
                if not wait_for_turnstile_in_modal(sb, timeout=45):
                    sp = screenshot(sb, f"06-turnstile-timeout-{server_id}")
                    results.append(f"⚠️ {server_name}: Turnstile 验证超时")
                  
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
              
                # 步骤 3.3: 提交表单
                if not submit_renew_form(sb):
                    print("[ERROR] 提交表单失败")
                    sp = screenshot(sb, f"07-submit-failed-{server_id}")
                    results.append(f"❌ {server_name}: 提交表单失败")
                    continue
              
                time.sleep(3)
                screenshot(sb, f"08-result-{server_id}")
              
                # 步骤 3.4: 检查结果
                result = check_renew_result(sb)
                print(f"[DEBUG] 续订结果: {result}")
              
                if result.get("success"):
                    print("[INFO] 🎉 续订成功！")
                    if result.get("expiry_info"):
                        print(f"[INFO] 到期信息: {result['expiry_info']}")
                    results.append(f"🎉 {server_name}: 续订成功")
                    sp = screenshot(sb, f"09-success-{server_id}")
                    notify_telegram(ok=True, stage=f"续订成功 - {server_name}", screenshot_path=sp)
                elif result.get("error"):
                    print("[ERROR] ❌ 续订失败")
                    results.append(f"❌ {server_name}: 续订失败")
                    sp = screenshot(sb, f"09-failed-{server_id}")
                    notify_telegram(ok=False, stage=f"续订失败 - {server_name}", screenshot_path=sp)
                else:
                    print("[INFO] ✅ 续订完成（状态未知）")
                    results.append(f"✅ {server_name}: 续订完成")
              
                time.sleep(2)
          
            # ========== 汇总 ==========
            print("\n" + "=" * 50)
            print("[INFO] 执行结果汇总:")
            print("=" * 50)
            
            summary = "\n".join(results) if results else "无服务器处理"
            print(summary)
            
            success_count = sum(1 for r in results if "🎉" in r or "✅" in r)
            fail_count = sum(1 for r in results if "❌" in r)
            
            notify_telegram(
                ok=(fail_count == 0),
                stage="执行完成",
                msg=f"成功: {success_count}, 失败: {fail_count}\n{summary}"
            )
            
            print("\n[INFO] 🏁 全部完成")
            
            if fail_count > 0:
                sys.exit(1)
            
    except ImportError as e:
        print(f"[ERROR] 缺少依赖: {e}")
        sys.exit(1)
        
    except Exception as e:
        print(f"[ERROR] 发生异常: {e}")
        import traceback
        traceback.print_exc()
        
        notify_telegram(ok=False, stage="异常", msg=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
