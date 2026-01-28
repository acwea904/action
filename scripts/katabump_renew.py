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


def click_turnstile_checkbox(sb) -> bool:
    """
    尝试点击 Turnstile checkbox
    """
    try:
        # 方法1: 使用 uc_click 点击 iframe 内的 checkbox
        turnstile_selectors = [
            "iframe[src*='challenges.cloudflare.com']",
            "iframe[src*='turnstile']",
            "#cf-turnstile iframe",
            ".cf-turnstile iframe",
            "iframe[title*='Cloudflare']",
        ]
        
        for selector in turnstile_selectors:
            try:
                if sb.is_element_present(selector):
                    print(f"[INFO] 找到 Turnstile iframe: {selector}")
                    # 切换到 iframe
                    sb.switch_to_frame(selector)
                    time.sleep(1)
                    
                    # 尝试点击 checkbox
                    checkbox_selectors = [
                        "input[type='checkbox']",
                        ".ctp-checkbox-label",
                        "#challenge-stage",
                        "body",
                    ]
                    
                    for cb_sel in checkbox_selectors:
                        try:
                            if sb.is_element_present(cb_sel):
                                sb.uc_click(cb_sel)
                                print(f"[INFO] 点击了: {cb_sel}")
                                time.sleep(2)
                                break
                        except:
                            pass
                    
                    sb.switch_to_default_content()
                    return True
            except Exception as e:
                print(f"[DEBUG] 尝试 {selector} 失败: {e}")
                try:
                    sb.switch_to_default_content()
                except:
                    pass
        
        # 方法2: 使用 JavaScript 直接触发
        try:
            result = sb.execute_script("""
                // 查找 Turnstile iframe
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    if (iframe.src && (iframe.src.includes('challenges.cloudflare.com') || iframe.src.includes('turnstile'))) {
                        // 尝试点击 iframe 中心
                        const rect = iframe.getBoundingClientRect();
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        
                        // 创建并触发点击事件
                        const clickEvent = new MouseEvent('click', {
                            view: window,
                            bubbles: true,
                            cancelable: true,
                            clientX: x,
                            clientY: y
                        });
                        iframe.dispatchEvent(clickEvent);
                        return 'clicked_iframe';
                    }
                }
                return 'no_iframe';
            """)
            print(f"[DEBUG] JS 点击结果: {result}")
        except Exception as e:
            print(f"[DEBUG] JS 点击失败: {e}")
            
    except Exception as e:
        print(f"[WARN] 点击 Turnstile 失败: {e}")
    
    return False


def wait_for_cloudflare(sb, timeout: int = 60) -> bool:
    """
    等待并处理 Cloudflare 验证
    使用多种策略绕过
    """
    print("[INFO] 检查 Cloudflare 验证...")
    
    start_time = time.time()
    attempt = 0
    
    while time.time() - start_time < timeout:
        attempt += 1
        
        try:
            page_source = sb.get_page_source().lower()
            current_url = sb.get_current_url().lower()
            
            # 检查是否已经通过验证
            success_indicators = [
                "login" in current_url and "challenge" not in current_url,
                "dashboard" in current_url,
                "email" in page_source and "password" in page_source,
                "sign in" in page_source,
                "log in" in page_source,
            ]
            
            if any(success_indicators):
                print("[INFO] ✅ Cloudflare 验证通过")
                return True
            
            # 检查是否有 cf_clearance cookie
            try:
                cookies = sb.get_cookies()
                if any(c.get("name") == "cf_clearance" for c in cookies):
                    print("[INFO] ✅ 已获取 cf_clearance cookie")
                    time.sleep(2)
                    return True
            except:
                pass
            
            # Cloudflare 验证指标
            cf_indicators = [
                "turnstile" in page_source,
                "challenges.cloudflare" in page_source,
                "just a moment" in page_source,
                "verify you are human" in page_source,
                "checking your browser" in page_source,
                "cf-challenge" in page_source,
                "challenge-platform" in current_url,
            ]
            
            if any(cf_indicators):
                if attempt % 5 == 1:
                    print(f"[INFO] 检测到 Cloudflare 验证，尝试处理... (尝试 {attempt})")
                
                # 策略1: 使用 uc_gui_click_captcha (如果可用)
                if attempt <= 3:
                    try:
                        sb.uc_gui_click_captcha()
                        time.sleep(3)
                    except Exception as e:
                        if attempt == 1:
                            print(f"[DEBUG] uc_gui_click_captcha 不可用: {e}")
                
                # 策略2: 手动点击 Turnstile
                if attempt % 3 == 0:
                    click_turnstile_checkbox(sb)
                    time.sleep(2)
                
                # 策略3: 刷新页面重试 (每15秒)
                if attempt > 0 and attempt % 15 == 0:
                    print("[INFO] 刷新页面重试...")
                    try:
                        sb.uc_open_with_reconnect(sb.get_current_url(), reconnect_time=4)
                        time.sleep(3)
                    except:
                        pass
                
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


def find_and_click_renew_button(sb, button_type: str = "bottom") -> bool:
    """
    查找并点击 Renew 按钮
    """
    try:
        if button_type == "dialog":
            # 对话框中的按钮
            result = sb.execute_script("""
                const modal = document.querySelector('.modal, [role="dialog"], .modal-content');
                if (modal) {
                    const buttons = modal.querySelectorAll('button, a.btn');
                    for (const btn of buttons) {
                        const text = btn.textContent.toLowerCase();
                        if (text.includes('renew') && !text.includes('cancel')) {
                            btn.scrollIntoView({block: 'center'});
                            btn.click();
                            return 'clicked';
                        }
                    }
                }
                return 'not_found';
            """)
        else:
            # 页面底部的按钮
            result = sb.execute_script("""
                const buttons = document.querySelectorAll('button, a.btn');
                for (const btn of buttons) {
                    const text = btn.textContent.toLowerCase();
                    // 排除对话框中的按钮
                    if (text.includes('renew') && !btn.closest('.modal')) {
                        btn.scrollIntoView({block: 'center'});
                        btn.click();
                        return 'clicked';
                    }
                }
                return 'not_found';
            """)
        
        if result == 'clicked':
            print(f"[INFO] 点击 {button_type} Renew 按钮成功")
            return True
            
    except Exception as e:
        print(f"[WARN] 点击 {button_type} Renew 按钮失败: {e}")
    
    return False


def wait_for_turnstile_in_dialog(sb, timeout: int = 45) -> bool:
    """
    等待对话框中的 Turnstile 验证完成
    """
    print("[INFO] 等待对话框中的 Turnstile 验证...")
    
    start_time = time.time()
    attempt = 0
    
    while time.time() - start_time < timeout:
        attempt += 1
        
        try:
            # 检查对话框是否存在
            has_modal = sb.execute_script("""
                return document.querySelector('.modal, [role="dialog"]') !== null;
            """)
            
            if not has_modal:
                print("[INFO] 对话框已关闭")
                return True
            
            # 检查是否有 Turnstile
            has_turnstile = sb.execute_script("""
                const modal = document.querySelector('.modal, [role="dialog"]');
                if (!modal) return false;
                
                const html = modal.innerHTML.toLowerCase();
                return html.includes('turnstile') || 
                       html.includes('cf-turnstile') ||
                       modal.querySelector('iframe[src*="challenges.cloudflare"]') !== null;
            """)
            
            if has_turnstile:
                if attempt % 5 == 1:
                    print(f"[INFO] 对话框中有 Turnstile，等待验证... ({attempt})")
                
                # 尝试点击
                if attempt % 3 == 0:
                    click_turnstile_checkbox(sb)
                
                time.sleep(1)
            else:
                # 没有 Turnstile，检查是否可以点击 Renew
                can_click = sb.execute_script("""
                    const modal = document.querySelector('.modal, [role="dialog"]');
                    if (!modal) return false;
                    
                    const buttons = modal.querySelectorAll('button');
                    for (const btn of buttons) {
                        if (btn.textContent.toLowerCase().includes('renew') && !btn.disabled) {
                            return true;
                        }
                    }
                    return false;
                """)
                
                if can_click:
                    print("[INFO] ✅ Turnstile 验证通过，可以点击 Renew")
                    return True
                    
        except Exception as e:
            print(f"[DEBUG] 检查对话框状态出错: {e}")
        
        time.sleep(1)
    
    print("[WARN] ⚠️ 对话框 Turnstile 验证超时")
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
    
    results = []
    
    try:
        from seleniumbase import SB
        
        # 配置参数
        sb_kwargs = {
            "uc": True,
            "test": True,
            "locale": "en",
            "headless": False,  # UC Mode 需要非 headless
            "uc_cdp_events": True,  # 启用 CDP 事件
        }
        
        if proxy_server:
            # 检查代理是否可用
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
            
            # 使用 uc_open_with_reconnect 并增加重连时间
            sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=6)
            time.sleep(5)
            
            # 处理 Cloudflare
            if not wait_for_cloudflare(sb, timeout=60):
                sp = screenshot(sb, "01-cf-failed")
                notify_telegram(ok=False, stage="Cloudflare 验证失败", screenshot_path=sp)
                sys.exit(1)
            
            screenshot(sb, "01-login-page")
            
            # 检查是否需要登录
            current_url = sb.get_current_url()
            page_source = sb.get_page_source().lower()
            
            if "/auth/login" in current_url or ("email" in page_source and "password" in page_source):
                print("[INFO] 执行登录...")
                
                try:
                    # 等待表单加载
                    sb.wait_for_element("input[name='email']", timeout=10)
                    
                    # 填写登录表单
                    sb.uc_click("input[name='email']")
                    sb.type("input[name='email']", username)
                    time.sleep(0.5)
                    
                    sb.uc_click("input[name='password']")
                    sb.type("input[name='password']", password)
                    time.sleep(0.5)
                    
                    screenshot(sb, "02-login-filled")
                    
                    # 点击登录按钮
                    sb.uc_click("button[type='submit']")
                    time.sleep(5)
                    
                    # 再次处理可能的 Cloudflare
                    wait_for_cloudflare(sb, timeout=30)
                    
                except Exception as e:
                    print(f"[ERROR] 登录操作失败: {e}")
                    sp = screenshot(sb, "02-login-error")
                    notify_telegram(ok=False, stage="登录操作失败", msg=str(e), screenshot_path=sp)
                    sys.exit(1)
                
                # 检查登录结果
                time.sleep(3)
                current_url = sb.get_current_url()
                
                if "/auth/login" in current_url:
                    # 检查是否有错误消息
                    page_source = sb.get_page_source().lower()
                    if "invalid" in page_source or "error" in page_source or "incorrect" in page_source:
                        print("[ERROR] ❌ 登录失败：用户名或密码错误")
                        sp = screenshot(sb, "02-login-failed")
                        notify_telegram(ok=False, stage="登录失败", msg="用户名或密码错误", screenshot_path=sp)
                        sys.exit(1)
                    
                    # 可能还在验证中
                    print("[INFO] 等待登录完成...")
                    time.sleep(5)
                    wait_for_cloudflare(sb, timeout=20)
                
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
            
            # ========== 2. 访问 Dashboard ==========
            print("\n[INFO] ===== 步骤 2: 获取服务器列表 =====")
            print(f"[INFO] 访问: {DASHBOARD_URL}")
            
            sb.uc_open_with_reconnect(DASHBOARD_URL, reconnect_time=4)
            time.sleep(3)
            
            wait_for_cloudflare(sb, timeout=30)
            screenshot(sb, "03-dashboard")
            
            # 获取服务器列表
            servers_data = fetch_servers_api(sb)
            
            if not servers_data:
                # 尝试从页面解析
                print("[INFO] 尝试从页面解析服务器列表...")
                try:
                    servers_data = sb.execute_script("""
                        const rows = document.querySelectorAll('tr[data-id], .server-item, [data-server-id]');
                        const servers = [];
                        rows.forEach(row => {
                            const id = row.dataset.id || row.dataset.serverId;
                            const name = row.querySelector('.server-name, td:first-child')?.textContent?.trim();
                            if (id) servers.push({id, name: name || 'Server ' + id});
                        });
                        return servers;
                    """)
                except:
                    pass
            
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
                
                # 访问服务器详情页
                detail_url = f"{BASE_URL}/servers/edit?id={server_id}"
                print(f"[INFO] 访问: {detail_url}")
                
                try:
                    sb.uc_open_with_reconnect(detail_url, reconnect_time=4)
                    time.sleep(3)
                    wait_for_cloudflare(sb, timeout=30)
                except Exception as e:
                    print(f"[WARN] 页面加载异常: {e}")
                
                screenshot(sb, f"04-server-{server_id}")
                
                # 步骤 3.1: 点击底部 Renew 按钮
                print("[INFO] 查找底部 Renew 按钮...")
                
                if not find_and_click_renew_button(sb, "bottom"):
                    print("[ERROR] 未找到底部 Renew 按钮")
                    sp = screenshot(sb, f"04-no-renew-{server_id}")
                    results.append(f"❌ {server_name}: 未找到 Renew 按钮")
                    continue
                
                time.sleep(2)
                screenshot(sb, f"05-dialog-{server_id}")
                
                # 步骤 3.2: 等待 Turnstile 验证
                if not wait_for_turnstile_in_dialog(sb, timeout=45):
                    sp = screenshot(sb, f"06-turnstile-timeout-{server_id}")
                    results.append(f"⚠️ {server_name}: Turnstile 验证超时")
                    notify_telegram(ok=False, stage=f"Turnstile 超时 - {server_name}", screenshot_path=sp)
                    
                    # 尝试关闭对话框
                    try:
                        sb.execute_script("""
                            const closeBtn = document.querySelector('.modal .close, .modal [aria-label="Close"], .btn-close');
                            if (closeBtn) closeBtn.click();
                        """)
                    except:
                        pass
                    continue
                
                screenshot(sb, f"06-turnstile-passed-{server_id}")
                time.sleep(1)
                
                # 步骤 3.3: 点击对话框中的 Renew 按钮
                print("[INFO] 点击对话框中的 Renew 按钮...")
                
                if not find_and_click_renew_button(sb, "dialog"):
                    print("[ERROR] 未找到对话框 Renew 按钮")
                    sp = screenshot(sb, f"07-no-dialog-btn-{server_id}")
                    results.append(f"❌ {server_name}: 未找到对话框 Renew 按钮")
                    continue
                
                time.sleep(3)
                
                # 检查结果
                screenshot(sb, f"08-result-{server_id}")
                
                try:
                    # 检查是否有成功提示
                    result_check = sb.execute_script("""
                        const body = document.body.innerText.toLowerCase();
                        const hasSuccess = body.includes('success') || 
                                          body.includes('renewed') || 
                                          body.includes('extended');
                        const hasError = body.includes('error') || body.includes('failed');
                        const modalClosed = !document.querySelector('.modal.show, [role="dialog"]:not([aria-hidden="true"])');
                        
                        return {hasSuccess, hasError, modalClosed};
                    """)
                    
                    if result_check.get('hasSuccess') or result_check.get('modalClosed'):
                        print("[INFO] 🎉 续订成功！")
                        results.append(f"🎉 {server_name}: 续订成功")
                        sp = screenshot(sb, f"09-success-{server_id}")
                        notify_telegram(ok=True, stage=f"续订成功 - {server_name}", screenshot_path=sp)
                    elif result_check.get('hasError'):
                        print("[ERROR] ❌ 续订失败")
                        results.append(f"❌ {server_name}: 续订失败")
                        sp = screenshot(sb, f"09-failed-{server_id}")
                        notify_telegram(ok=False, stage=f"续订失败 - {server_name}", screenshot_path=sp)
                    else:
                        print("[INFO] ✅ 续订完成（状态未知）")
                        results.append(f"✅ {server_name}: 续订完成")
                        
                except Exception as e:
                    print(f"[WARN] 检查结果时出错: {e}")
                    results.append(f"⚠️ {server_name}: 检查结果出错")
                
                # 等待一下再处理下一个
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
        print("[ERROR] 请安装: pip install seleniumbase")
        sys.exit(1)
        
    except Exception as e:
        print(f"[ERROR] 发生异常: {e}")
        import traceback
        traceback.print_exc()
        
        notify_telegram(ok=False, stage="异常", msg=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
