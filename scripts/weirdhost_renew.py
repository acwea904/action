#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import aiohttp
import base64
import re
import json
import subprocess
import tempfile
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

DEFAULT_SERVER_URL = "https://hub.weirdhost.xyz/server/d341874c"
DEFAULT_COOKIE_NAME = "remember_web"
PROXY_LIST_URL = os.environ.get("PROXY_LIST_URL", "")
HY2_URI = os.environ.get("HY2_URI", "")
HY2_LOCAL_PORT = 10808


def parse_hy2_uri(uri: str) -> dict:
    if not uri.startswith("hysteria2://"):
        return None
    try:
        parsed = urlparse(uri)
        password = parsed.username
        server = parsed.hostname
        port = parsed.port
        params = parse_qs(parsed.query)
        return {
            "server": f"{server}:{port}",
            "auth": password,
            "tls": {
                "sni": params.get("sni", [""])[0],
                "insecure": params.get("insecure", ["0"])[0] == "1"
            }
        }
    except:
        return None


async def start_hy2_client() -> subprocess.Popen:
    if not HY2_URI:
        return None
    
    config = parse_hy2_uri(HY2_URI)
    if not config:
        print("⚠️ HY2_URI 解析失败")
        return None
    
    config["socks5"] = {"listen": f"127.0.0.1:{HY2_LOCAL_PORT}"}
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f)
        config_path = f.name
    
    print(f"🚀 启动 Hysteria2 客户端...")
    try:
        proc = subprocess.Popen(
            ["/usr/local/bin/hysteria", "client", "-c", config_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        await asyncio.sleep(3)
        if proc.poll() is None:
            print(f"✅ Hysteria2 已启动，本地端口: {HY2_LOCAL_PORT}")
            return proc
        else:
            stderr = proc.stderr.read().decode()
            print(f"❌ Hysteria2 启动失败: {stderr}")
    except Exception as e:
        print(f"❌ Hysteria2 启动异常: {e}")
    return None


async def fetch_residential_proxies() -> list:
    proxies = []
    if not PROXY_LIST_URL:
        return proxies
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(PROXY_LIST_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    for line in text.split('\n'):
                        if '[家宽]' in line and line.startswith('socks5://'):
                            match = re.match(r'(socks5://[\d.]+:\d+)', line)
                            if match:
                                proxies.append(match.group(1))
                    print(f"📡 获取到 {len(proxies)} 个家宽代理")
    except Exception as e:
        print(f"⚠️ 获取代理列表失败: {e}")
    return proxies


def calculate_remaining_time(expiry_str: str) -> str:
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return "无法解析"
        diff = expiry_dt - datetime.now()
        if diff.total_seconds() < 0:
            return "⚠️ 已过期"
        days = diff.days
        hours, remainder = divmod(diff.seconds, 3600)
        minutes = remainder // 60
        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes}分钟")
        return " ".join(parts) if parts else "不到1分钟"
    except:
        return "计算失败"


def parse_renew_error(body: dict) -> str:
    try:
        if isinstance(body, dict) and "errors" in body:
            errors = body.get("errors", [])
            if errors and isinstance(errors[0], dict):
                return errors[0].get("detail", str(body))
        return str(body)
    except:
        return str(body)


def is_cooldown_error(error_detail: str) -> bool:
    keywords = ["can only once at one time period", "can't renew", "cannot renew", "already renewed"]
    return any(kw in error_detail.lower() for kw in keywords)


async def wait_for_cloudflare(page, max_wait: int = 120) -> bool:
    print("🛡️ 等待 Cloudflare 验证...")
    await page.wait_for_timeout(3000)
    
    for i in range(max_wait):
        try:
            is_cf = await page.evaluate("""
                () => {
                    if (document.querySelector('iframe[src*="challenges.cloudflare.com"]')) return true;
                    if (document.querySelector('[data-sitekey]')) return true;
                    if (document.querySelector('#challenge-running')) return true;
                    if (document.querySelector('.cf-browser-verification')) return true;
                    const text = document.body.innerText || '';
                    if (text.includes('Checking your browser') || text.includes('Just a moment') || 
                        text.includes('Verify you are human') || text.includes('DDoS protection')) return true;
                    return false;
                }
            """)
            if not is_cf:
                await page.wait_for_timeout(2000)
                print(f"✅ CF 验证通过 ({i+1}秒)")
                return True
            if i % 10 == 0:
                print(f"⏳ CF 验证中... ({i+1}/{max_wait}秒)")
            await page.wait_for_timeout(1000)
        except:
            await page.wait_for_timeout(1000)
    print("⚠️ CF 验证超时")
    return False


async def wait_for_turnstile(page, max_wait: int = 60) -> bool:
    """等待 Turnstile 验证完成"""
    print("🔄 检查 Turnstile 验证...")
    
    for i in range(max_wait):
        try:
            # 检查是否有 Turnstile iframe
            has_turnstile = await page.evaluate("""
                () => {
                    const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                    if (!iframe) return false;
                    // 检查是否已完成（通常会隐藏或移除）
                    const style = window.getComputedStyle(iframe);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    return true;
                }
            """)
            
            if not has_turnstile:
                print(f"✅ Turnstile 验证完成 ({i+1}秒)")
                return True
            
            # 尝试点击 Turnstile checkbox
            if i == 5 or i == 15:
                try:
                    await page.evaluate("""
                        () => {
                            const iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                            if (iframe) {
                                iframe.contentDocument?.querySelector('input[type="checkbox"]')?.click();
                            }
                        }
                    """)
                except:
                    pass
            
            if i % 10 == 0:
                print(f"⏳ Turnstile 验证中... ({i+1}/{max_wait}秒)")
            await page.wait_for_timeout(1000)
        except:
            await page.wait_for_timeout(1000)
    
    print("⚠️ Turnstile 验证超时")
    return False


async def wait_for_page_ready(page, max_wait: int = 30) -> bool:
    print("⏳ 等待页面内容加载...")
    for i in range(max_wait):
        try:
            ready = await page.evaluate("""
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const btn of buttons) {
                        const text = btn.innerText || '';
                        if (text.includes('시간추가') || text.includes('Add Time') || text.includes('Renew')) {
                            return true;
                        }
                    }
                    const bodyText = document.body.innerText || '';
                    if (bodyText.includes('유통기한') || bodyText.includes('Expiry')) {
                        return true;
                    }
                    return false;
                }
            """)
            if ready:
                await page.wait_for_timeout(2000)
                print(f"✅ 页面就绪 ({i+1}秒)")
                return True
            if i % 5 == 0:
                print(f"⏳ 等待页面... ({i+1}/{max_wait}秒)")
        except:
            pass
        await page.wait_for_timeout(1000)
    print("⚠️ 页面加载超时")
    return False


def encrypt_secret(public_key: str, secret_value: str) -> str:
    pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


async def update_github_secret(secret_name: str, secret_value: str) -> bool:
    repo_token = os.environ.get("REPO_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo_token or not repository or not NACL_AVAILABLE:
        return False
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {repo_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession() as session:
        try:
            pk_url = f"https://api.github.com/repos/{repository}/actions/secrets/public-key"
            async with session.get(pk_url, headers=headers) as resp:
                if resp.status != 200:
                    return False
                pk_data = await resp.json()
            encrypted_value = encrypt_secret(pk_data["key"], secret_value)
            secret_url = f"https://api.github.com/repos/{repository}/actions/secrets/{secret_name}"
            payload = {"encrypted_value": encrypted_value, "key_id": pk_data["key_id"]}
            async with session.put(secret_url, headers=headers, json=payload) as resp:
                return resp.status in (201, 204)
        except:
            return False


async def tg_notify(message: str):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"})
        except:
            pass


async def tg_notify_photo(photo_path: str, caption: str = ""):
    token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                data.add_field("caption", caption)
                data.add_field("parse_mode", "HTML")
                await session.post(url, data=data)
        except:
            pass


async def extract_remember_cookie(context) -> tuple:
    try:
        cookies = await context.cookies()
        for cookie in cookies:
            if cookie["name"].startswith("remember_web"):
                return (cookie["name"], cookie["value"])
    except:
        pass
    return (None, None)


async def get_expiry_time(page) -> str:
    try:
        return await page.evaluate("""
            () => {
                const text = document.body.innerText;
                const match = text.match(/유통기한\\s*(\\d{4}-\\d{2}-\\d{2}(?:\\s+\\d{2}:\\d{2}:\\d{2})?)/);
                if (match) return match[1].trim();
                return 'Unknown';
            }
        """)
    except:
        return "Unknown"


async def find_renew_button(page):
    selectors = [
        'button:has-text("시간추가")',
        'button:has-text("Add Time")',
        'button:has-text("Renew")',
    ]
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if await locator.count() > 0:
                return locator.nth(0)
        except:
            continue
    return None


async def try_renew_with_proxy(proxy_url: str, server_url: str, cookie_name: str, cookie_value: str, proxy_label: str = None) -> dict:
    label = proxy_label or proxy_url or "直连"
    print(f"\n{'='*50}")
    print(f"🔄 尝试: {label}")
    print('='*50)
    
    result = {"success": False, "need_retry": False, "message": "", "new_cookie": None}
    
    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": ['--disable-blink-features=AutomationControlled']
        }
        if proxy_url:
            launch_args["proxy"] = {"server": proxy_url}
        
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            extra_http_headers={'Accept-Language': 'zh-CN,zh;q=0.9'}
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)
        
        page = await context.new_page()
        page.set_default_timeout(120000)
        
        renew_result = {"captured": False, "status": None, "body": None}

        async def capture_response(response):
            if "/renew" in response.url or "notfreeservers" in response.url:
                renew_result["captured"] = True
                renew_result["status"] = response.status
                try:
                    renew_result["body"] = await response.json()
                except:
                    renew_result["body"] = await response.text()
                print(f"📡 API 响应: {response.status} - {response.url}")

        page.on("response", capture_response)
        proxy_info = f"\n🌐 代理: {label}" if proxy_url else ""

        try:
            await context.add_cookies([{"name": cookie_name, "value": cookie_value, "domain": "hub.weirdhost.xyz", "path": "/"}])

            print(f"🌐 访问: {server_url}")
            await page.goto(server_url, timeout=90000)
            await wait_for_cloudflare(page, max_wait=120)
            
            page_ready = await wait_for_page_ready(page, max_wait=30)
            if not page_ready:
                result["need_retry"] = True
                result["message"] = "页面加载超时"
                return result

            if "/auth/login" in page.url or "/login" in page.url:
                result["message"] = "Cookie 已失效"
                await page.screenshot(path="login_failed.png", full_page=True)
                await tg_notify_photo("login_failed.png", "🎁 <b>Weirdhost 续订报告</b>\n\n❌ Cookie 已失效，请手动更新")
                return result

            print("✅ 登录成功")

            expiry_time = await get_expiry_time(page)
            remaining_time = calculate_remaining_time(expiry_time)
            print(f"📅 到期: {expiry_time} | 剩余: {remaining_time}")

            add_button = await find_renew_button(page)
            if not add_button:
                result["need_retry"] = True
                result["message"] = "未找到续期按钮"
                return result

            await add_button.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(1000)
            
            # 点击续期按钮
            print("📌 点击续期按钮...")
            await add_button.click()
            
            # 等待弹窗/Turnstile 出现
            await page.wait_for_timeout(3000)
            
            # 等待 Turnstile 验证
            await wait_for_turnstile(page, max_wait=60)
            
            # 尝试点击确认复选框
            print("⏳ 查找确认复选框...")
            for attempt in range(3):
                try:
                    checkbox = await page.wait_for_selector('input[type="checkbox"]:not([disabled])', timeout=5000)
                    if checkbox:
                        await checkbox.click()
                        print("✅ 已点击复选框")
                        break
                except:
                    # 尝试通过 JS 点击
                    clicked = await page.evaluate("""
                        () => {
                            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
                            for (const cb of checkboxes) {
                                if (!cb.checked && !cb.disabled) {
                                    cb.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    if clicked:
                        print("✅ 已通过 JS 点击复选框")
                        break
                await page.wait_for_timeout(2000)
            
            # 等待 API 响应
            print("⏳ 等待 API 响应...")
            for i in range(60):
                if renew_result["captured"]:
                    print(f"✅ 捕获到响应 ({i+1}秒)")
                    break
                if i % 10 == 0 and i > 0:
                    print(f"⏳ 等待 API... ({i}秒)")
                await page.wait_for_timeout(1000)

            if renew_result["captured"]:
                status = renew_result["status"]
                body = renew_result["body"]

                if status in (200, 201, 204):
                    await page.wait_for_timeout(2000)
                    await page.reload()
                    await wait_for_cloudflare(page, max_wait=30)
                    await wait_for_page_ready(page, max_wait=20)
                    new_expiry = await get_expiry_time(page)
                    new_remaining = calculate_remaining_time(new_expiry)
                    
                    msg = f"""🎁 <b>Weirdhost 续订报告</b>

✅ 续期成功！
📅 新到期时间: {new_expiry}
⏳ 剩余时间: {new_remaining}{proxy_info}"""
                    await tg_notify(msg)
                    result["success"] = True

                elif status == 400:
                    error_detail = parse_renew_error(body)
                    if is_cooldown_error(error_detail):
                        msg = f"""🎁 <b>Weirdhost 续订报告</b>

ℹ️ 暂无需续期（冷却期内）
📅 到期时间: {expiry_time}
⏳ 剩余时间: {remaining_time}{proxy_info}"""
                        await tg_notify(msg)
                        result["success"] = True
                    else:
                        result["message"] = f"续期失败: {error_detail}"
                else:
                    result["message"] = f"HTTP {status}"
            else:
                # 截图调试
                await page.screenshot(path="no_response.png", full_page=True)
                await tg_notify_photo("no_response.png", f"🎁 <b>Weirdhost 续订报告</b>\n\n⚠️ 未检测到 API 响应\n📅 到期: {expiry_time}\n⏳ 剩余: {remaining_time}{proxy_info}")
                result["need_retry"] = True
                result["message"] = "未检测到 API 响应"

            new_name, new_value = await extract_remember_cookie(context)
            if new_value and new_value != cookie_value:
                result["new_cookie"] = new_value

        except Exception as e:
            result["need_retry"] = True
            result["message"] = f"异常: {repr(e)}"

        finally:
            await context.close()
            await browser.close()
    
    return result


async def add_server_time():
    server_url = os.environ.get("SERVER_URL", DEFAULT_SERVER_URL)
    cookie_value = os.environ.get("REMEMBER_WEB_COOKIE", "").strip()
    cookie_name = os.environ.get("REMEMBER_WEB_COOKIE_NAME", DEFAULT_COOKIE_NAME)

    if not cookie_value:
        await tg_notify("🎁 <b>Weirdhost 续订报告</b>\n\n❌ REMEMBER_WEB_COOKIE 未设置")
        return

    proxies = []
    hy2_proc = None
    
    # 优先使用 Hysteria2
    if HY2_URI:
        hy2_proc = await start_hy2_client()
        if hy2_proc:
            proxies.append((f"socks5://127.0.0.1:{HY2_LOCAL_PORT}", "Hysteria2"))
    
    # 获取家宽代理
    print("🚀 获取家宽代理列表...")
    socks_proxies = await fetch_residential_proxies()
    for p in socks_proxies:
        proxies.append((p, p))
    
    # 直连作为最后选项
    proxies.append((None, "直连"))
    
    try:
        for i, (proxy_url, label) in enumerate(proxies):
            print(f"\n🔄 [{i+1}/{len(proxies)}] 尝试: {label}")
            
            result = await try_renew_with_proxy(proxy_url, server_url, cookie_name, cookie_value, label)
            
            if result.get("new_cookie"):
                await update_github_secret("REMEMBER_WEB_COOKIE", result["new_cookie"])
            
            if result["success"]:
                print(f"✅ 使用 {label} 成功!")
                return
            
            if not result["need_retry"]:
                if result["message"]:
                    await tg_notify(f"🎁 <b>Weirdhost 续订报告</b>\n\n❌ {result['message']}")
                return
            
            print(f"⚠️ {label} 失败: {result['message']}")
        
        await tg_notify("🎁 <b>Weirdhost 续订报告</b>\n\n❌ 所有代理均失败")
    
    finally:
        if hy2_proc:
            hy2_proc.terminate()
            print("🛑 Hysteria2 已停止")


if __name__ == "__main__":
    asyncio.run(add_server_time())
