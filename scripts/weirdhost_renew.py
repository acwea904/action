#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import asyncio
import aiohttp
import base64
from datetime import datetime
from playwright.async_api import async_playwright

try:
    from nacl import encoding, public
    NACL_AVAILABLE = True
except ImportError:
    NACL_AVAILABLE = False

DEFAULT_DASHBOARD_URL = "https://hub.weirdhost.xyz/"
DEFAULT_COOKIE_NAME = "remember_web"
NOTIFY_DAYS_BEFORE = 2  # 到期前几天通知


def extract_server_id(url: str) -> str:
    """从 URL 中提取服务器 ID"""
    try:
        if "/server/" in url:
            return url.split("/server/")[-1].strip("/")
        return "Unknown"
    except:
        return "Unknown"


def calculate_remaining_days(expiry_str: str) -> int:
    """计算剩余天数（负数表示已过期）"""
    try:
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                expiry_dt = datetime.strptime(expiry_str.strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return None
        
        diff = expiry_dt - datetime.now()
        return diff.days
    except:
        return None


def format_remaining_time(expiry_str: str) -> str:
    """格式化剩余时间显示"""
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
            parts.append(f"{days} 天")
        if hours > 0:
            parts.append(f"{hours} 小时")
        if minutes > 0 and days == 0:
            parts.append(f"{minutes} 分钟")
        
        return " ".join(parts) if parts else "不到 1 分钟"
    except:
        return "计算失败"


def get_executor_name() -> str:
    """获取执行器名称"""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return "GitHub Actions"
    return "本地执行"


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
    for i in range(max_wait):
        try:
            is_cf = await page.evaluate("""
                () => {
                    if (document.querySelector('iframe[src*="challenges.cloudflare.com"]')) return true;
                    if (document.querySelector('[data-sitekey]')) return true;
                    const text = document.body.innerText;
                    return text.includes('Checking') || text.includes('moment') || text.includes('human');
                }
            """)
            if not is_cf:
                print(f"✅ CF 验证通过 ({i+1}秒)")
                return True
            if i % 10 == 0:
                print(f"⏳ CF 验证中... ({i+1}/{max_wait}秒)")
            await page.wait_for_timeout(1000)
        except:
            await page.wait_for_timeout(1000)
    print("⚠️ CF 验证超时")
    return False


async def wait_for_page_ready(page, max_wait: int = 15) -> bool:
    for i in range(max_wait):
        try:
            ready = await page.evaluate("""
                () => {
                    const hasButton = document.querySelector('button') !== null;
                    const hasContent = document.body.innerText.length > 100;
                    return hasButton && hasContent;
                }
            """)
            if ready:
                print(f"✅ 页面就绪 ({i+1}秒)")
                return True
        except:
            pass
        await page.wait_for_timeout(1000)
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


async def get_first_server_url(page, dashboard_url: str) -> str:
    """从仪表板页面自动获取第一个服务器的 URL"""
    try:
        print(f"🔍 正在获取服务器列表...")
        await page.goto(dashboard_url, timeout=90000)
        await wait_for_cloudflare(page, max_wait=120)
        await page.wait_for_timeout(2000)
        
        server_id = await page.evaluate("""
            () => {
                const firstLink = document.querySelector('table tr td a[href^="/server/"]');
                if (firstLink) {
                    const href = firstLink.getAttribute('href');
                    return href.replace('/server/', '');
                }
                return null;
            }
        """)
        
        if server_id:
            server_url = f"https://hub.weirdhost.xyz/server/{server_id}"
            print(f"✅ 自动获取到服务器: {server_id}")
            return server_url
        else:
            print("⚠️ 未找到服务器")
            return None
    except Exception as e:
        print(f"⚠️ 获取服务器列表失败: {e}")
        return None


def format_manual_renew_notification(server_url: str, expiry_time: str, remaining_days: int) -> str:
    """格式化手动续订通知"""
    server_id = extract_server_id(server_url)
    remaining_time = format_remaining_time(expiry_time)
    executor = get_executor_name()
    
    # 根据剩余天数设置状态
    if remaining_days < 0:
        status_emoji = "🔴"
        status_text = "已过期"
    elif remaining_days == 0:
        status_emoji = "🔴"
        status_text = "今天到期"
    elif remaining_days == 1:
        status_emoji = "🟡"
        status_text = "明天到期"
    else:
        status_emoji = "🟡"
        status_text = f"{remaining_days} 天后到期"
    
    return f"""⚠️ <b>Weirdhost 需要手动续订</b>

{status_emoji} <b>{status_text}</b>
🖥 服务器: <code>{server_id}</code>
📅 到期时间: <code>{expiry_time}</code>
⏳ 剩余时间: <b>{remaining_time}</b>
❗️ 自动续订需要验证码
💻 执行器: {executor}

👉 <a href="{server_url}">点击续订</a>"""


def format_time_fetch_error_notification(server_url: str) -> str:
    """格式化获取时间失败的通知"""
    server_id = extract_server_id(server_url)
    executor = get_executor_name()
    
    return f"""⚠️ <b>Weirdhost 状态异常</b>

❌ 无法获取到期时间
🖥 服务器: <code>{server_id}</code>
🔍 可能原因:
  • 页面结构变化
  • Cookie 失效
  • 服务器状态异常
💻 执行器: {executor}

👉 <a href="{server_url}">点击检查</a>"""


async def add_server_time():
    cookie_value = os.environ.get("REMEMBER_WEB_COOKIE", "").strip()
    cookie_name = os.environ.get("REMEMBER_WEB_COOKIE_NAME", DEFAULT_COOKIE_NAME)
    dashboard_url = os.environ.get("DASHBOARD_URL", DEFAULT_DASHBOARD_URL)

    if not cookie_value:
        print("❌ REMEMBER_WEB_COOKIE 未设置")
        return

    print("🚀 启动 Playwright...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
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
            if "/renew" in response.url and "notfreeservers" in response.url:
                renew_result["captured"] = True
                renew_result["status"] = response.status
                try:
                    renew_result["body"] = await response.json()
                except:
                    renew_result["body"] = await response.text()
                print(f"📡 API 响应: {response.status}")

        page.on("response", capture_response)

        try:
            await context.add_cookies([{"name": cookie_name, "value": cookie_value, "domain": "hub.weirdhost.xyz", "path": "/"}])

            # 自动获取服务器 URL
            server_url = os.environ.get("SERVER_URL", "").strip()
            if not server_url:
                server_url = await get_first_server_url(page, dashboard_url)
                if not server_url:
                    print("❌ 无法获取服务器 URL")
                    return

            server_id = extract_server_id(server_url)
            print(f"🌐 访问服务器: {server_id}")
            
            await page.goto(server_url, timeout=90000)
            await wait_for_cloudflare(page, max_wait=120)
            await page.wait_for_timeout(2000)
            await wait_for_page_ready(page, max_wait=20)

            if "/auth/login" in page.url or "/login" in page.url:
                print("❌ Cookie 已失效（静默处理）")
                return

            print("✅ 登录成功")

            expiry_time = await get_expiry_time(page)
            
            # 【核心逻辑】检查是否获取到时间
            if expiry_time == "Unknown" or not expiry_time:
                print(f"\n{'='*50}")
                print("❌ 无法获取到期时间，发送通知")
                print(f"{'='*50}\n")
                
                msg = format_time_fetch_error_notification(server_url)
                await page.screenshot(path="time_fetch_error.png", full_page=True)
                await tg_notify_photo("time_fetch_error.png", msg)
                print("✅ 已发送时间获取失败通知")
                return
            
            remaining_time = format_remaining_time(expiry_time)
            remaining_days = calculate_remaining_days(expiry_time)
            
            print(f"📅 到期: {expiry_time} | 剩余: {remaining_time} ({remaining_days}天)")

            # 【核心逻辑】检查是否需要发送到期提醒
            # 剩余 ≤ 2天、≤ 1天、≤ 0天（已过期）都发送提醒
            if remaining_days is not None and remaining_days <= NOTIFY_DAYS_BEFORE:
                print(f"\n{'='*50}")
                print(f"⚠️ 触发到期提醒：剩余 {remaining_days} 天")
                print(f"{'='*50}\n")
                
                msg = format_manual_renew_notification(server_url, expiry_time, remaining_days)
                await tg_notify(msg)
                print("✅ 已发送手动续订提醒")
                
                # 发送提醒后直接返回，不再尝试自动续期
                return

            print("\n" + "="*50)
            print("📌 尝试自动续期")
            print("="*50)
            
            add_button = await find_renew_button(page)
            if not add_button:
                print("⚠️ 未找到续期按钮（静默处理）")
                return

            await add_button.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(1000)
            await add_button.click()
            print("🔄 已点击续期按钮，等待 CF 验证...")

            await page.wait_for_timeout(5000)
            cf_passed = await wait_for_cloudflare(page, max_wait=120)
            
            if not cf_passed:
                print("⚠️ CF 验证超时（静默处理）")
                return

            print("⏳ 等待复选框...")
            try:
                checkbox = await page.wait_for_selector('input[type="checkbox"]', timeout=5000)
                await checkbox.click()
                print("✅ 已点击复选框")
            except:
                try:
                    await page.evaluate("document.querySelector('input[type=\"checkbox\"]')?.click()")
                    print("✅ 已通过 JS 点击复选框")
                except:
                    print("⚠️ 未找到复选框")

            print("⏳ 等待 API 响应...")
            await page.wait_for_timeout(2000)
            
            for i in range(30):
                if renew_result["captured"]:
                    print(f"✅ 捕获到响应 ({i+1}秒)")
                    break
                if i % 5 == 4:
                    print(f"⏳ 等待 API... ({i+1}秒)")
                await page.wait_for_timeout(1000)

            if renew_result["captured"]:
                status = renew_result["status"]
                body = renew_result["body"]

                if status in (200, 201, 204):
                    # 【核心逻辑】续期成功，发送通知
                    await page.wait_for_timeout(2000)
                    await page.reload()
                    await wait_for_cloudflare(page, max_wait=30)
                    await page.wait_for_timeout(3000)
                    new_expiry = await get_expiry_time(page)
                    new_remaining = format_remaining_time(new_expiry)
                    
                    msg = f"""🎁 <b>Weirdhost 续订报告</b>

✅ 续期成功！
🖥 服务器: <code>{server_id}</code>
📅 新到期时间: <code>{new_expiry}</code>
⏳ 剩余时间: <b>{new_remaining}</b>
💻 执行器: {get_executor_name()}"""
                    
                    print(f"\n{'='*50}")
                    print("✅ 续期成功！发送通知")
                    print(f"{'='*50}\n")
                    await tg_notify(msg)

                elif status == 400:
                    error_detail = parse_renew_error(body)
                    if is_cooldown_error(error_detail):
                        print(f"ℹ️ 冷却期内（静默处理）")
                    else:
                        print(f"⚠️ 续期失败: {error_detail}（静默处理）")
                else:
                    print(f"⚠️ HTTP {status}（静默处理）")
            else:
                print("⚠️ 未检测到 API 响应（静默处理）")

            # 更新 Cookie
            new_name, new_value = await extract_remember_cookie(context)
            if new_value and new_value != cookie_value:
                print("🔄 更新 Cookie")
                await update_github_secret("REMEMBER_WEB_COOKIE", new_value)

        except Exception as e:
            print(f"❌ 异常: {repr(e)}（静默处理）")

        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(add_server_time())
