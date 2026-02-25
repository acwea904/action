#!/usr/bin/env python3
"""
Billing Kerit 自动续订脚本
- 使用 SeleniumBase UC 模式绕过 Cloudflare
- 支持代理
- 支持 Telegram 通知
- 捕获 API 响应判断续订结果
- Cookie 格式:session_id=值; cf_clearance=值
- 注意: 请使用与脚本相同的代理网络获取 Cookie，cf_clearance 与 IP 绑定
"""

import os
import sys
import time
import json
import base64
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from typing import Optional, Dict, Any

from seleniumbase import SB
from selenium.webdriver.common.by import By

# ============== 配置 ==============
FREE_PANEL_URL = "https://billing.kerit.cloud/free_panel"
SESSION_URL = "https://billing.kerit.cloud/session"
BASE_DOMAIN = "billing.kerit.cloud"

# 环境变量 - 与 workflow 保持一致
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
REPO_TOKEN = os.environ.get("REPO_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")

PROXY_SOCKS5 = os.environ.get("PROXY_SOCKS5", "")
PROXY_HTTP = os.environ.get("PROXY_HTTP", "")

COOKIES_STR = os.environ.get("BILLING_KERIT_COOKIES", "")

# 截图目录 - 与 workflow 上传路径一致
SCREENSHOT_DIR = Path("output/screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Cookie 失效提示（复用）
COOKIE_RENEW_TIP = "\n\n⚠️ 注意: 请使用与脚本相同的代理网络获取 Cookie"

COOKIE_HELP_DETAIL = (
    "Cookie 已失效，请手动更新\n\n"
    "📝 *Cookie 格式:*\n"
    "`session_id=值; cf_clearance=值`\n\n"
    "💡 *获取方式:*\n"
    "1. 浏览器登录 billing.kerit.cloud\n"
    "2. F12 → Application → Cookies\n"
    "3. 复制 `session_id` 和 `cf_clearance` 的值\n"
    "4. 更新 GitHub Secret: `BILLING_KERIT_COOKIES`"
    + COOKIE_RENEW_TIP
)


def log(level: str, message: str):
    """日志输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")


def mask(s: str) -> str:
    """字符串脱敏"""
    if not s or len(s) <= 4:
        return "****"
    return f"{s[:2]}***{s[-2:]}"


def mask_ip(ip: str) -> str:
    """IP 地址脱敏"""
    if not ip:
        return "***"
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.***.***.{parts[3]}"
    return mask(ip)


def screenshot_path(name: str) -> str:
    """生成截图路径"""
    timestamp = datetime.now().strftime("%H%M%S")
    return str(SCREENSHOT_DIR / f"{timestamp}-{name}.png")


def test_proxy(proxy_url: str) -> bool:
    """测试代理连接"""
    if not proxy_url:
        return False
    try:
        proxies = {"http": proxy_url, "https": proxy_url}
        resp = requests.get("https://api.ipify.org", proxies=proxies, timeout=15)
        ip = resp.text.strip()
        log("INFO", f"代理 IP: {mask_ip(ip)}")
        return True
    except Exception as e:
        log("WARN", f"代理测试失败: {e}")
        return False


def parse_cookies(cookie_str: str) -> list:
    """解析 Cookie 字符串，自动去重"""
    if not cookie_str:
        return []
    
    cookies = []
    seen = {}
    
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                seen[key] = value
    
    for key, value in seen.items():
        cookies.append({"name": key, "value": value})
    
    return cookies


def send_text_only(text: str):
    """仅发送文本消息"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=15)
        log("INFO", "Telegram 消息已发送")
    except Exception as e:
        log("ERROR", f"发送文本失败: {e}")
        

def notify_telegram(success: bool, title: str, message: str, image_path: str = None):
    """发送 Telegram 通知"""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("WARN", "Telegram 未配置，跳过通知")
        return
    
    emoji = "✅" if success else "❌"
    text = f"{emoji} *{title}*\n\n{message}\n\n_Billing Kerit Auto Renewal_"
    
    try:
        if image_path and Path(image_path).exists():
            # 图片消息有 1024 字符限制，超长时分开发送
            if len(text) > 1000:
                # 先发图片（简短说明）
                short_text = f"{emoji} *{title}*\n\n_详细信息见下条消息_"
                url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
                with open(image_path, "rb") as f:
                    files = {"photo": f}
                    data = {"chat_id": TG_CHAT_ID, "caption": short_text, "parse_mode": "Markdown"}
                    requests.post(url, data=data, files=files, timeout=30)
                # 再发详细文本
                send_text_only(text)
            else:
                url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendPhoto"
                with open(image_path, "rb") as f:
                    files = {"photo": f}
                    data = {"chat_id": TG_CHAT_ID, "caption": text, "parse_mode": "Markdown"}
                    resp = requests.post(url, data=data, files=files, timeout=30)
                if resp.status_code == 200:
                    log("INFO", "Telegram 图片已发送")
                else:
                    log("WARN", f"Telegram 图片发送失败: {resp.text[:100]}")
                    send_text_only(text)
        else:
            send_text_only(text)
    except Exception as e:
        log("ERROR", f"Telegram 通知失败: {e}")


def update_github_secret(secret_name: str, secret_value: str):
    """更新 GitHub Secret"""
    if not REPO_TOKEN or not GITHUB_REPOSITORY:
        log("WARN", "GitHub 配置缺失，跳过更新 Secret")
        return
    
    try:
        from nacl import public, encoding
        
        headers = {
            "Authorization": f"token {REPO_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # 获取公钥
        pub_key_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key"
        resp = requests.get(pub_key_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            log("ERROR", f"获取公钥失败: {resp.status_code}")
            return
        
        pub_key_data = resp.json()
        pub_key = public.PublicKey(pub_key_data["key"].encode(), encoding.Base64Encoder())
        
        # 加密
        sealed_box = public.SealedBox(pub_key)
        encrypted = sealed_box.encrypt(secret_value.encode())
        encrypted_b64 = base64.b64encode(encrypted).decode()
        
        # 更新
        secret_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}"
        payload = {"encrypted_value": encrypted_b64, "key_id": pub_key_data["key_id"]}
        resp = requests.put(secret_url, headers=headers, json=payload, timeout=15)
        
        if resp.status_code in [201, 204]:
            log("INFO", "GitHub Secret 已更新")
        else:
            log("ERROR", f"更新 Secret 失败: {resp.status_code}")
    
    except ImportError:
        log("WARN", "nacl 未安装，跳过 Secret 更新")
    except Exception as e:
        log("ERROR", f"更新 GitHub Secret 失败: {e}")


def save_cookies_for_update(sb) -> Optional[str]:
    """保存 Cookie 用于更新"""
    try:
        cookies = sb.driver.get_cookies()
        important_cookies = {}
        
        for cookie in cookies:
            name = cookie.get("name", "")
            if name in ["session_id", "cf_clearance"]:
                important_cookies[name] = cookie.get("value", "")
        
        if important_cookies:
            cookie_str = "; ".join([f"{k}={v}" for k, v in important_cookies.items()])
            log("INFO", f"新 Cookie 已保存 ({len(important_cookies)} 个)")
            return cookie_str
        return None
    except Exception as e:
        log("ERROR", f"保存 Cookie 失败: {e}")
        return None


def setup_network_interception(sb):
    """设置网络拦截，捕获 /api/renew 响应"""
    sb.execute_script("""
        window._renewApiResponses = [];
        
        // 拦截 fetch
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {
            const response = await originalFetch.apply(this, args);
            const url = typeof args[0] === 'string' ? args[0] : (args[0].url || '');
            
            if (url.includes('/api/renew')) {
                try {
                    const clone = response.clone();
                    const data = await clone.json();
                    window._renewApiResponses.push({
                        url: url,
                        status: response.status,
                        ok: response.ok,
                        data: data,
                        timestamp: Date.now()
                    });
                    console.log('Captured renew API:', response.status, data);
                } catch(e) {
                    window._renewApiResponses.push({
                        url: url,
                        status: response.status,
                        ok: response.ok,
                        error: e.message,
                        timestamp: Date.now()
                    });
                }
            }
            return response;
        };
        
        // 拦截 XMLHttpRequest
        const originalOpen = window.XMLHttpRequest.prototype.open;
        window.XMLHttpRequest.prototype.open = function(method, url) {
            this._url = url;
            return originalOpen.apply(this, arguments);
        };
        
        const originalSend = window.XMLHttpRequest.prototype.send;
        window.XMLHttpRequest.prototype.send = function() {
            this.addEventListener('load', function() {
                if (this._url && this._url.includes('/api/renew')) {
                    try {
                        const data = JSON.parse(this.responseText);
                        window._renewApiResponses.push({
                            url: this._url,
                            status: this.status,
                            ok: this.status >= 200 && this.status < 300,
                            data: data,
                            timestamp: Date.now()
                        });
                        console.log('Captured XHR renew API:', this.status, data);
                    } catch(e) {
                        window._renewApiResponses.push({
                            url: this._url,
                            status: this.status,
                            ok: this.status >= 200 && this.status < 300,
                            responseText: this.responseText,
                            timestamp: Date.now()
                        });
                    }
                }
            });
            return originalSend.apply(this, arguments);
        };
        
        console.log('Network interception ready');
    """)
    log("INFO", "网络拦截已设置")


def get_api_responses(sb) -> list:
    """获取捕获的 API 响应"""
    try:
        return sb.execute_script("return window._renewApiResponses || [];") or []
    except:
        return []


def check_renewal_result(sb) -> Dict[str, Any]:
    """
    检查续订结果 - 多重验证
    返回: {"status": "success|limit_reached|error|unknown", "message": "..."}
    """
    result = {"status": "unknown", "message": "", "api_status": None}
    
    # 1. 检查 API 响应（最可靠）
    try:
        api_responses = get_api_responses(sb)
        log("INFO", f"捕获到 {len(api_responses)} 个 API 响应")
        
        for resp in reversed(api_responses):
            status_code = resp.get("status", 0)
            data = resp.get("data", {})
            message = data.get("message", "") if isinstance(data, dict) else str(data)
            
            log("INFO", f"API: status={status_code}, message={message}")
            result["api_status"] = status_code
            
            if status_code == 200 and resp.get("ok"):
                result["status"] = "success"
                result["message"] = message or "续订成功"
                return result
            
            if status_code == 400:
                if "cannot exceed 7 days" in message.lower() or "exceed" in message.lower():
                    result["status"] = "limit_reached"
                    result["message"] = message
                    return result
                else:
                    result["status"] = "error"
                    result["message"] = message or "请求错误"
                    return result
            
            if status_code >= 400:
                result["status"] = "error"
                result["message"] = message or f"HTTP {status_code}"
                return result
    except Exception as e:
        log("WARN", f"获取 API 响应失败: {e}")
    
    # 2. 检查页面错误消息（备用）
    try:
        page_error = sb.execute_script("""
            var bodyText = document.body.innerText || '';
            
            if (bodyText.includes('Cannot exceed 7 days')) {
                return 'Cannot exceed 7 days validity';
            }
            if (bodyText.includes('limit reached') || bodyText.includes('weekly limit')) {
                return 'Weekly limit reached';
            }
            if (bodyText.includes('renewed successfully') || bodyText.includes('Renewal successful')) {
                return 'SUCCESS';
            }
            
            // 检查 toast 消息
            var toasts = document.querySelectorAll('.Toastify__toast, .toast, [role="alert"]');
            for (var i = 0; i < toasts.length; i++) {
                var text = toasts[i].textContent.trim();
                if (text.includes('Cannot exceed') || text.includes('exceed 7 days')) {
                    return text;
                }
                if (text.includes('success') && text.includes('renew')) {
                    return 'SUCCESS';
                }
            }
            
            return null;
        """)
        
        if page_error:
            log("INFO", f"页面消息: {page_error}")
            if page_error == "SUCCESS":
                result["status"] = "success"
                result["message"] = "续订成功"
            elif "exceed" in page_error.lower() or "limit" in page_error.lower():
                result["status"] = "limit_reached"
                result["message"] = page_error
            return result
    except Exception as e:
        log("WARN", f"检查页面消息失败: {e}")
    
    return result


def handle_turnstile(sb, max_attempts: int = 6) -> bool:
    """处理 Turnstile 验证"""
    log("INFO", "开始处理 Turnstile 验证...")
    
    for attempt in range(max_attempts):
        try:
            has_turnstile = sb.execute_script("""
                return !!(document.querySelector('iframe[src*="challenges.cloudflare.com"]') ||
                         document.querySelector('[class*="turnstile"]') ||
                         document.querySelector('#cf-turnstile'));
            """)
            
            if not has_turnstile:
                log("INFO", "未检测到 Turnstile")
                return True
            
            log("INFO", f"检测到 Turnstile, 尝试 {attempt + 1}/{max_attempts}")
            
            is_checked = sb.execute_script("""
                var response = document.querySelector('input[name="cf-turnstile-response"]');
                if (response && response.value && response.value.length > 10) return true;
                
                var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe) {
                    try {
                        var iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
                        var checkbox = iframeDoc.querySelector('input[type="checkbox"]');
                        if (checkbox && checkbox.checked) return true;
                    } catch(e) {}
                }
                
                var successIcon = document.querySelector('[class*="turnstile"] [class*="success"]');
                if (successIcon) return true;
                
                return false;
            """)
            
            if is_checked:
                log("INFO", "✅ Turnstile 已通过!")
                return True
            
            sb.execute_script("""
                var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe) {
                    var rect = iframe.getBoundingClientRect();
                    var clickX = rect.left + 30;
                    var clickY = rect.top + rect.height / 2;
                    
                    var clickEvent = new MouseEvent('click', {
                        bubbles: true,
                        cancelable: true,
                        clientX: clickX,
                        clientY: clickY,
                        view: window
                    });
                    iframe.dispatchEvent(clickEvent);
                }
            """)
            
            time.sleep(3)
            
            is_checked = sb.execute_script("""
                var response = document.querySelector('input[name="cf-turnance-response"]');
                return response && response.value && response.value.length > 10;
            """)
            
            if is_checked:
                log("INFO", "✅ Turnstile 已通过!")
                return True
                
        except Exception as e:
            log("WARN", f"Turnstile 处理异常: {e}")
        
        time.sleep(2)
    
    log("WARN", "Turnstile 验证未能确认通过")
    return False


def check_login_status(sb) -> bool:
    """检查登录状态"""
    try:
        current_url = sb.get_current_url()
        
        if "/login" in current_url or "/register" in current_url:
            return False
        
        is_logged_in = sb.execute_script("""
            var bodyText = document.body.innerText || '';
            
            if (document.querySelector('[href*="logout"]')) return true;
            if (document.querySelector('[href*="free_panel"]')) return true;
            if (bodyText.includes('Free Plans') || bodyText.includes('Dashboard')) return true;
            if (bodyText.includes('Please log in') || bodyText.includes('Sign in')) return false;
            
            return null;
        """)
        
        if is_logged_in is True:
            return True
        elif is_logged_in is False:
            return False
        
        return "/free_panel" in current_url or "/session" in current_url
        
    except Exception as e:
        log("WARN", f"检查登录状态失败: {e}")
        return False


def check_access_blocked(sb) -> bool:
    """检查是否被阻止访问（包括 VPN/代理/数据中心 IP 限制）"""
    try:
        blocked = sb.execute_script("""
            var bodyText = (document.body.innerText || '').toLowerCase();
            return bodyText.includes('access denied') ||
                   bodyText.includes('access restricted') ||
                   bodyText.includes('blocked') ||
                   bodyText.includes('forbidden') ||
                   bodyText.includes('rate limit') ||
                   bodyText.includes('too many requests') ||
                   bodyText.includes('restrict access from vpns') ||
                   bodyText.includes('datacenter ips') ||
                   bodyText.includes('unusual network activity') ||
                   bodyText.includes('disable your vpn');
        """)
        return blocked
    except:
        return False


def check_page_error(sb) -> Optional[str]:
    """
    检查页面是否有浏览器级别错误（Chrome 错误页面）
    只检测真正的浏览器错误，不检测网站内容
    """
    try:
        error = sb.execute_script("""
            // Chrome 错误页面有特定的 DOM 结构
            var isErrorPage = (
                document.querySelector('body.neterror') !== null ||
                document.getElementById('main-frame-error') !== null ||
                document.querySelector('.interstitial-wrapper') !== null ||
                document.querySelector('div[jscontent="errorCode"]') !== null ||
                // Chrome "This site can't be reached" 页面
                document.querySelector('#main-message') !== null && 
                document.body.innerText.includes("can't be reached")
            );
            
            // 如果不是错误页面，直接返回 null
            if (!isErrorPage) {
                return null;
            }
            
            // 是错误页面，检查具体错误类型
            var bodyText = (document.body.innerText || '').toLowerCase();
            
            if (bodyText.includes('err_too_many_redirects') || 
                bodyText.includes('redirected you too many times')) {
                return 'TOO_MANY_REDIRECTS';
            }
            
            if (bodyText.includes('err_connection')) {
                return 'CONNECTION_ERROR';
            }
            
            if (bodyText.includes('err_timed_out') || bodyText.includes('timed out')) {
                return 'TIMEOUT_ERROR';
            }
            
            if (bodyText.includes('err_ssl') || bodyText.includes('err_cert')) {
                return 'SSL_ERROR';
            }
            
            if (bodyText.includes('err_name_not_resolved')) {
                return 'DNS_ERROR';
            }
            
            if (bodyText.includes("can't be reached") || bodyText.includes("isn't working")) {
                return 'PAGE_NOT_WORKING';
            }
            
            // 是错误页面但无法识别具体类型
            return 'BROWSER_ERROR';
        """)
        return error
    except:
        return None


def get_error_notify_message(page_error: str, context: str = "") -> tuple:
    """
    根据页面错误类型生成通知标题和消息（复用）
    返回: (title, message)
    """
    # 与 Cookie/会话相关的错误 → 附加 Cookie 提示
    cookie_errors = {
        "TOO_MANY_REDIRECTS": (
            "Cookie 失效",
            f"重定向次数过多，Cookie 已失效，请重新获取{COOKIE_RENEW_TIP}"
        ),
        "CONNECTION_ERROR": (
            "连接错误",
            f"页面连接失败，Cookie 可能已失效或代理异常{COOKIE_RENEW_TIP}"
        ),
        "PAGE_NOT_WORKING": (
            "页面异常",
            f"页面不工作，Cookie 可能已失效{COOKIE_RENEW_TIP}"
        ),
    }
    
    # 非 Cookie 相关的错误
    other_errors = {
        "ACCESS_BLOCKED": (
            "访问被阻止",
            "IP 被限制，请更换代理"
        ),
        "NOT_FOUND": (
            "页面未找到",
            "页面 404，服务可能已变更"
        ),
        "SERVER_ERROR": (
            "服务器错误",
            "服务器内部错误，请稍后重试"
        ),
    }
    
    if page_error in cookie_errors:
        title, message = cookie_errors[page_error]
    elif page_error in other_errors:
        title, message = other_errors[page_error]
    else:
        title = "页面错误"
        message = f"错误类型: {page_error}{COOKIE_RENEW_TIP}"
    
    # 添加上下文信息
    if context:
        message = f"[{context}] {message}"
    
    return title, message


def main():
    """主函数"""
    log("INFO", "=" * 50)
    log("INFO", "🚀 Billing Kerit 自动续订脚本启动")
    log("INFO", "=" * 50)
    
    # 检查代理
    proxy_url = PROXY_SOCKS5 or PROXY_HTTP
    if proxy_url:
        log("INFO", f"🌐 使用代理: {mask(proxy_url)}")
        if test_proxy(proxy_url):
            log("INFO", "✅ 代理连接正常")
        else:
            log("WARN", "⚠️ 代理测试失败，继续尝试...")
    else:
        log("INFO", "🌐 直连模式（未配置代理）")
    
    # 解析 Cookie
    cookies = parse_cookies(COOKIES_STR)
    if cookies:
        cookie_names = [c["name"] for c in cookies]
        log("INFO", f"解析到 {len(cookies)} 个 Cookie: {', '.join(cookie_names)}")
    else:
        log("WARN", "未提供 Cookie，将尝试新会话")
    
    # 找出 session_id
    session_cookie = None
    for c in cookies:
        if c["name"] == "session_id":
            session_cookie = c
            break
    
    final_screenshot = None
    display = None
    
    # Linux 下启动虚拟显示
    if sys.platform.startswith("linux"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
        except Exception as e:
            log("WARN", f"虚拟显示启动失败: {e}")
    
    try:
        log("INFO", "🌐 启动浏览器...")
        
        sb_kwargs = {
            "uc": True,
            "headless": False,
            "locale_code": "en",
            "test": True,
        }
        
        if proxy_url:
            if proxy_url.startswith("socks"):
                sb_kwargs["proxy"] = proxy_url.replace("socks5://", "socks5h://")
            else:
                sb_kwargs["proxy"] = proxy_url
            log("INFO", "浏览器将使用代理")
        
        with SB(**sb_kwargs) as sb:
            try:
                log("INFO", "浏览器已启动")
                
                # 1. 首次访问获取 Cloudflare Cookie
                log("INFO", "🌐 首次访问网站，获取 Cloudflare 验证...")
                sb.uc_open_with_reconnect("https://billing.kerit.cloud/", reconnect_time=10)
                
                for i in range(30):
                    time.sleep(2)
                    current_url = sb.get_current_url()
                    page_title = sb.get_title()
                    
                    if "challenge" not in current_url.lower() and "cloudflare" not in page_title.lower():
                        break
                    
                    if i == 10:
                        try:
                            sb.uc_gui_click_captcha()
                        except:
                            pass
                
                # 首次访问后检查页面错误
                page_error = check_page_error(sb)
                if page_error:
                    sp_error = screenshot_path("00-page-error")
                    sb.save_screenshot(sp_error)
                    log("ERROR", f"❌ 首次访问出错: {page_error}")
                    title, message = get_error_notify_message(page_error, "首次访问")
                    notify_telegram(False, title, message, sp_error)
                    sys.exit(1)
                
                # 首次访问后检查 IP 是否被阻止
                if check_access_blocked(sb):
                    sp_blocked = screenshot_path("00-ip-blocked")
                    sb.save_screenshot(sp_blocked)
                    log("ERROR", "❌ 访问被阻止，IP 被限制")
                    notify_telegram(False, "访问被阻止", "IP 被限制，请更换代理", sp_blocked)
                    sys.exit(1)
                
                # 2. 注入 session_id Cookie
                if session_cookie:
                    log("INFO", "🍪 注入 session_id Cookie...")
                    try:
                        sb.add_cookie({
                            "name": session_cookie["name"],
                            "value": session_cookie["value"],
                            "domain": BASE_DOMAIN,
                            "path": "/"
                        })
                        log("INFO", f"已注入 Cookie: {session_cookie['name']}")
                    except Exception as e:
                        log("WARN", f"注入 Cookie 失败: {e}")
                
                # 3. 访问 session 页面检查登录状态
                log("INFO", f"🔗 访问 {SESSION_URL}...")
                sb.uc_open_with_reconnect(SESSION_URL, reconnect_time=8)
                time.sleep(5)
                
                current_url = sb.get_current_url()
                log("INFO", f"当前 URL: {current_url}")
                
                # 检查页面错误
                page_error = check_page_error(sb)
                if page_error:
                    sp_error = screenshot_path("01-session-error")
                    sb.save_screenshot(sp_error)
                    log("ERROR", f"❌ Session 页面错误: {page_error}")
                    title, message = get_error_notify_message(page_error, "Session 页面")
                    notify_telegram(False, title, message, sp_error)
                    sys.exit(1)
                
                sp_session = screenshot_path("01-session-check")
                sb.save_screenshot(sp_session)
                final_screenshot = sp_session
                
                if not check_login_status(sb):
                    log("ERROR", "❌ 未登录，Cookie 可能已失效")
                    notify_telegram(False, "登录失败", COOKIE_HELP_DETAIL, sp_session)
                    sys.exit(1)
                
                log("INFO", "✅ Cookie 有效，已登录")
                
                # 4. 进入 Free Plans 页面
                log("INFO", "🎁 进入 Free Plans 页面...")
                sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=8)
                time.sleep(5)
                
                current_url = sb.get_current_url()
                log("INFO", f"当前 URL: {current_url}")
                
                # 检查页面错误
                page_error = check_page_error(sb)
                if page_error:
                    sp_error = screenshot_path("02-page-error")
                    sb.save_screenshot(sp_error)
                    log("ERROR", f"❌ Free Plans 页面错误: {page_error}")
                    title, message = get_error_notify_message(page_error, "Free Plans")
                    notify_telegram(False, title, message, sp_error)
                    sys.exit(1)
                
                # 验证是否成功进入 free_panel 页面
                if "/free_panel" not in current_url:
                    sp_wrong = screenshot_path("02-wrong-page")
                    sb.save_screenshot(sp_wrong)
                    log("WARN", f"⚠️ 未能进入 Free Plans 页面，当前: {current_url}")
                    
                    # 尝试再次访问
                    log("INFO", "🔄 重试访问 Free Plans...")
                    sb.uc_open_with_reconnect(FREE_PANEL_URL, reconnect_time=10)
                    time.sleep(5)
                    
                    current_url = sb.get_current_url()
                    log("INFO", f"重试后 URL: {current_url}")
                    
                    # 再次检查
                    page_error = check_page_error(sb)
                    if page_error or "/free_panel" not in current_url:
                        sp_fail = screenshot_path("02-access-failed")
                        sb.save_screenshot(sp_fail)
                        log("ERROR", "❌ 无法访问 Free Plans 页面")
                        
                        if page_error:
                            title, message = get_error_notify_message(page_error, "Free Plans 重试")
                        else:
                            title = "访问失败"
                            message = f"无法进入 Free Plans\n当前页面: {current_url}\nCookie 可能已失效{COOKIE_RENEW_TIP}"
                        
                        notify_telegram(False, title, message, sp_fail)
                        sys.exit(1)
                
                if check_access_blocked(sb):
                    sp_blocked = screenshot_path("02-blocked")
                    sb.save_screenshot(sp_blocked)
                    log("ERROR", "❌ 访问被阻止")
                    notify_telegram(False, "访问被阻止", "IP 被限制，请更换代理", sp_blocked)
                    sys.exit(1)
                
                sp_free = screenshot_path("02-free-plans")
                sb.save_screenshot(sp_free)
                final_screenshot = sp_free
                
                # 5. 获取续订信息
                log("INFO", "🔍 检查续订状态...")
                
                renewal_count = sb.execute_script("""
                    var el = document.getElementById('renewal-count');
                    return el ? el.textContent.trim() : '0';
                """) or "0"
                log("INFO", f"本周已续订次数: {renewal_count}/7")
                
                status_text = sb.execute_script("""
                    var el = document.getElementById('renewal-status-text');
                    return el ? el.textContent.trim() : '未知';
                """) or "未知"
                log("INFO", f"续订状态: {status_text}")
                
                # 6. 检查续订按钮
                renew_btn_disabled = sb.execute_script("""
                    var btn = document.getElementById('renewServerBtn');
                    if (!btn) return true;
                    return btn.disabled || btn.hasAttribute('disabled');
                """)
                
                log("INFO", f"续订按钮 disabled: {renew_btn_disabled}")
                
                if renew_btn_disabled:
                    log("INFO", "⏭️ 续订按钮已禁用，跳过续订")
                    result_message = f"续订次数: {renewal_count}/7\n状态: {status_text}\n\n⏭️ 未到续订时间或已达限制"
                    
                    new_cookie_str = save_cookies_for_update(sb)
                    if new_cookie_str:
                        update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                    
                    notify_telegram(True, "检查完成", result_message, final_screenshot)
                else:
                    # 7. 开始循环续订流程
                    log("INFO", "✨ 续订按钮可用，开始循环续订流程...")
                    
                    total_renewed = 0
                    max_renewals = 7  # 最多尝试续订次数
                    initial_count = renewal_count
                    
                    for renewal_round in range(1, max_renewals + 1):
                        log("INFO", f"{'='*20} 第 {renewal_round} 轮续订 {'='*20}")
                        
                        # 检查按钮是否还可用
                        renew_server_btn_disabled = sb.execute_script("""
                            var btn = document.getElementById('renewServerBtn');
                            if (!btn) return true;
                            return btn.disabled || btn.hasAttribute('disabled');
                        """)
                        
                        if renew_server_btn_disabled:
                            log("INFO", "续订按钮已禁用，停止续订")
                            break
                        
                        # 点击续订按钮打开模态框
                        sb.execute_script("""
                            var btn = document.getElementById('renewServerBtn');
                            if (btn) btn.click();
                        """)
                        log("INFO", "已点击续订按钮，等待模态框...")
                        time.sleep(2)
                        
                        # 检查模态框
                        modal_visible = sb.execute_script("""
                            var modal = document.getElementById('renewalModal');
                            if (!modal) return false;
                            var style = window.getComputedStyle(modal);
                            return style.display !== 'none';
                        """)
                        
                        if not modal_visible:
                            log("WARN", "模态框未打开，跳过本轮")
                            continue
                        
                        log("INFO", "📋 续订模态框已打开")
                        
                        # 处理 Turnstile
                        try:
                            sb.uc_gui_click_captcha()
                            time.sleep(2)
                        except:
                            pass
                        
                        handle_turnstile(sb)
                        
                        # 点击广告
                        log("INFO", "🖱️ 点击广告横幅...")
                        main_window = sb.driver.current_window_handle
                        original_windows = set(sb.driver.window_handles)
                        
                        sb.execute_script("""
                            var adBanner = document.getElementById('adBanner');
                            if (adBanner) {
                                var parent = adBanner.closest('[onclick]') || adBanner.parentElement;
                                if (parent) parent.click();
                                else adBanner.click();
                            }
                        """)
                        
                        time.sleep(3)
                        
                        # 关闭广告窗口
                        new_windows = set(sb.driver.window_handles) - original_windows
                        if new_windows:
                            log("INFO", f"关闭 {len(new_windows)} 个广告窗口")
                            for win in new_windows:
                                try:
                                    sb.driver.switch_to.window(win)
                                    sb.driver.close()
                                except:
                                    pass
                            sb.driver.switch_to.window(main_window)
                        
                        time.sleep(1)
                        
                        # 点击最终续订按钮
                        log("INFO", "🔘 点击最终续订按钮...")
                        
                        renew_btn_ready = sb.execute_script("""
                            var btn = document.getElementById('renewBtn');
                            if (!btn) return {exists: false};
                            return {
                                exists: true,
                                disabled: btn.disabled,
                                visible: btn.offsetParent !== null
                            };
                        """)
                        
                        if renew_btn_ready and renew_btn_ready.get("exists") and not renew_btn_ready.get("disabled"):
                            sb.execute_script("""
                                var btn = document.getElementById('renewBtn');
                                if (btn && !btn.disabled) btn.click();
                            """)
                            log("INFO", "已点击 renewBtn")
                        else:
                            log("WARN", "renewBtn 不可用，尝试提交表单...")
                            sb.execute_script("""
                                var form = document.querySelector('#renewalModal form');
                                if (form) form.submit();
                            """)
                        
                        # 等待响应
                        time.sleep(3)
                        
                        # 检查是否达到限制（通过 toast 或页面提示）
                        limit_reached = sb.execute_script("""
                            var bodyText = document.body.innerText || '';
                            return bodyText.includes('Cannot exceed 7 days') ||
                                   bodyText.includes('exceed 7 days') ||
                                   bodyText.includes('maximum') ||
                                   bodyText.includes('limit reached');
                        """)
                        
                        if limit_reached:
                            log("INFO", "⚠️ 检测到已达续订限制")
                            break
                        
                        total_renewed += 1
                        log("INFO", f"✅ 第 {renewal_round} 轮续订完成")
                        
                        # 关闭模态框，准备下一轮
                        sb.execute_script("""
                            var closeBtn = document.querySelector('#renewalModal .close, [data-dismiss="modal"], .btn-close');
                            if (closeBtn) closeBtn.click();
                            var modal = document.getElementById('renewalModal');
                            if (modal) modal.style.display = 'none';
                            var backdrop = document.querySelector('.modal-backdrop');
                            if (backdrop) backdrop.remove();
                            document.body.classList.remove('modal-open');
                        """)
                        
                        time.sleep(2)
                        
                        # 刷新页面获取最新状态
                        sb.refresh()
                        time.sleep(3)
                        
                        # 检查当前状态
                        current_count = sb.execute_script("""
                            var el = document.getElementById('renewal-count');
                            return el ? el.textContent.trim() : '0';
                        """) or "0"
                        
                        current_days = sb.execute_script("""
                            var el = document.querySelector('[class*="TIME_REMAINING"], .time-remaining');
                            if (el) return el.textContent.trim();
                            // 备用: 查找包含 "Days" 的元素
                            var allText = document.body.innerText;
                            var match = allText.match(/(\\d+)\\s*Days?/i);
                            return match ? match[1] + ' Days' : '未知';
                        """) or "未知"
                        
                        log("INFO", f"当前状态: 续订次数 {current_count}/7, 剩余时间约 {current_days}")
                        
                        # 检查是否达到 7 天
                        try:
                            days_num = int(current_days.split()[0]) if current_days != "未知" else 0
                            if days_num >= 7:
                                log("INFO", "🎉 已达到 7 天有效期上限!")
                                break
                        except:
                            pass
                    
                    # 获取最终状态
                    time.sleep(2)
                    
                    final_count = sb.execute_script("""
                        var el = document.getElementById('renewal-count');
                        return el ? el.textContent.trim() : '未知';
                    """) or "未知"
                    
                    final_status = sb.execute_script("""
                        var el = document.getElementById('renewal-status-text');
                        return el ? el.textContent.trim() : '未知';
                    """) or "未知"
                    
                    # 获取剩余天数
                    final_days = sb.execute_script("""
                        var text = document.body.innerText;
                        var match = text.match(/(\\d+)\\s*Days?.*?(?:TIME REMAINING|remaining|Auto-shutdown)/i);
                        return match ? match[1] : '未知';
                    """) or "未知"
                    
                    log("INFO", f"最终续订次数: {final_count}/7")
                    log("INFO", f"最终剩余时间: {final_days} Days")
                    log("INFO", f"本次共续订: {total_renewed} 次")
                    
                    sp_final = screenshot_path("99-final")
                    sb.save_screenshot(sp_final)
                    final_screenshot = sp_final
                    
                    # 发送通知
                    if total_renewed > 0:
                        result_message = (
                            f"🎉 *续订成功*\n\n"
                            f"本次续订: {total_renewed} 次\n"
                            f"续订次数: {initial_count} → {final_count}/7\n"
                            f"剩余时间: {final_days} Days\n"
                            f"状态: {final_status}"
                        )
                        notify_telegram(True, "续订成功", result_message, final_screenshot)
                    else:
                        result_message = (
                            f"续订次数: {final_count}/7\n"
                            f"剩余时间: {final_days} Days\n"
                            f"状态: {final_status}\n\n"
                            f"⚠️ 未能续订，可能已达限制"
                        )
                        notify_telegram(False, "续订未执行", result_message, final_screenshot)
                    
                    # 保存 Cookie
                    log("INFO", "💾 保存 Cookie...")
                    new_cookie_str = save_cookies_for_update(sb)
                    if new_cookie_str:
                        update_github_secret("BILLING_KERIT_COOKIES", new_cookie_str)
                
                log("INFO", "✅ 脚本执行完成")
                
            except Exception as e:
                log("ERROR", f"浏览器操作异常: {e}")
                import traceback
                traceback.print_exc()
                
                try:
                    sp_error = screenshot_path("error")
                    sb.save_screenshot(sp_error)
                    notify_telegram(False, "执行异常", str(e), sp_error)
                except:
                    notify_telegram(False, "执行异常", str(e), None)
                
                sys.exit(1)
            
            finally:
                log("INFO", "🔒 浏览器已关闭")
    
    except Exception as e:
        log("ERROR", f"启动失败: {e}")
        import traceback
        traceback.print_exc()
        notify_telegram(False, "启动失败", str(e), None)
        sys.exit(1)
    
    finally:
        if display:
            try:
                display.stop()
            except:
                pass


if __name__ == "__main__":
    main()
