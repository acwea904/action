#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import logging
import asyncio
import aiohttp
from base64 import b64encode
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from playwright.async_api import async_playwright, BrowserContext, Page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ⚠️ 关键：必须与获取 Cookie 时的 UA 完全一致！
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36 Core/1.116.601.400 QQBrowser/20.0.7091.400"

@dataclass
class ServerInfo:
    server_id: str
    name: str
    short_id: str
    is_active: bool
    cpu: str = ""
    ram: str = ""
    disk: str = ""

@dataclass
class AccountResult:
    index: int
    servers: List[ServerInfo] = field(default_factory=list)
    started: List[dict] = field(default_factory=list)
    cookie_changed: bool = False
    new_cookie: str = ""
    error: str = ""

@dataclass
class Config:
    cookies_list: List[str]
    tg_token: Optional[str]
    tg_chat_id: Optional[str]
    repo_token: Optional[str]
    repository: Optional[str]

    @classmethod
    def from_env(cls) -> "Config":
        raw = os.environ.get("LUNES_COOKIES", "").strip()
        return cls(
            cookies_list=[c.strip() for c in raw.split("|||") if c.strip()],  # 使用 ||| 分隔多账号
            tg_token=os.environ.get("TG_BOT_TOKEN"),
            tg_chat_id=os.environ.get("TG_CHAT_ID"),
            repo_token=os.environ.get("REPO_TOKEN"),
            repository=os.environ.get("GITHUB_REPOSITORY")
        )

def parse_cookies(s: str) -> List[Dict]:
    cookies = []
    for p in s.split(";"):
        p = p.strip()
        if "=" in p:
            n, v = p.split("=", 1)
            for domain in [".lunes.host", "betadash.lunes.host", "ctrl.lunes.host"]:
                cookies.append({
                    "name": n.strip(), 
                    "value": v.strip(), 
                    "domain": domain, 
                    "path": "/",
                    "secure": True,
                    "sameSite": "Lax"
                })
    return cookies

def mask_cookie(s: str, show: int = 8) -> str:
    if len(s) <= show * 2:
        return s
    return f"{s[:show]}...{s[-show:]}"

class Notifier:
    def __init__(self, token: Optional[str], chat_id: Optional[str]):
        self.token, self.chat_id = token, chat_id
    
    async def send(self, msg: str) -> Optional[int]:
        if not self.token or not self.chat_id:
            return None
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    if r.status == 200:
                        logger.info("✅ Telegram通知已发送")
                        return (await r.json()).get('result', {}).get('message_id')
                    logger.error(f"❌ Telegram通知失败: {r.status}")
        except Exception as e:
            logger.error(f"❌ Telegram异常: {e}")
        return None
    
    async def send_photo(self, photo_bytes: bytes, caption: str = "") -> bool:
        if not self.token or not self.chat_id:
            return False
        try:
            async with aiohttp.ClientSession() as s:
                data = aiohttp.FormData()
                data.add_field('chat_id', str(self.chat_id))
                data.add_field('photo', photo_bytes, filename='screenshot.png', content_type='image/png')
                if caption:
                    data.add_field('caption', caption)
                async with s.post(
                    f"https://api.telegram.org/bot{self.token}/sendPhoto",
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as r:
                    return r.status == 200
        except:
            return False

class GitHubManager:
    def __init__(self, token: Optional[str], repo: Optional[str]):
        self.token, self.repo = token, repo
    
    async def update_secret(self, name: str, value: str) -> bool:
        if not self.token or not self.repo:
            return False
        try:
            from nacl import encoding, public
            headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json"}
            async with aiohttp.ClientSession() as s:
                async with s.get(f"https://api.github.com/repos/{self.repo}/actions/secrets/public-key", headers=headers) as r:
                    if r.status != 200:
                        return False
                    kd = await r.json()
                pk = public.PublicKey(kd["key"].encode(), encoding.Base64Encoder())
                enc = b64encode(public.SealedBox(pk).encrypt(value.encode())).decode()
                async with s.put(
                    f"https://api.github.com/repos/{self.repo}/actions/secrets/{name}",
                    headers=headers, 
                    json={"encrypted_value": enc, "key_id": kd["key_id"]}
                ) as r:
                    if r.status in [201, 204]:
                        logger.info(f"✅ GitHub Secret [{name}] 已更新")
                        return True
        except Exception as e:
            logger.error(f"❌ GitHub异常: {e}")
        return False

class LunesClient:
    def __init__(self, ctx: BrowserContext, page: Page):
        self.ctx, self.page = ctx, page
        self.dashboard_url = "https://betadash.lunes.host/"
        self.ctrl_url = "https://ctrl.lunes.host/server"
    
    async def get_servers(self) -> List[ServerInfo]:
        servers = []
        try:
            logger.info(f"🌐 访问: {self.dashboard_url}")
            
            resp = await self.page.goto(self.dashboard_url, wait_until="domcontentloaded", timeout=60000)
            status = resp.status if resp else 0
            logger.info(f"📡 响应状态: {status}")
            
            if status == 403:
                logger.error("❌ 403 Forbidden - Cookie 与 User-Agent 不匹配或已过期")
                # 截图诊断
                await self.page.screenshot(path="/tmp/403_error.png")
                return []
            
            await self.page.wait_for_timeout(3000)
            
            current_url = self.page.url
            logger.info(f"📍 当前URL: {current_url}")
            
            if "/login" in current_url:
                logger.error("❌ Cookie已失效，重定向到登录页")
                return []
            
            # 等待服务器卡片
            try:
                await self.page.wait_for_selector("a.server-card", timeout=15000)
            except:
                content = await self.page.content()
                if "Create Server" in content:
                    logger.info("✅ 页面已加载，暂无服务器")
                    return []
                logger.error("❌ 页面加载异常")
                await self.page.screenshot(path="/tmp/page_error.png")
                return []
            
            cards = await self.page.locator("a.server-card").all()
            logger.info(f"📋 找到 {len(cards)} 个服务器卡片")
            
            for card in cards:
                try:
                    href = await card.get_attribute("href") or ""
                    match = re.search(r"/servers/(\d+)", href)
                    if not match:
                        continue
                    
                    server_id = match.group(1)
                    
                    short_id = ""
                    meta = card.locator(".server-meta")
                    if await meta.count() > 0:
                        meta_text = await meta.text_content() or ""
                        id_match = re.search(r"ID\s*·\s*(\w+)", meta_text)
                        if id_match:
                            short_id = id_match.group(1)
                    
                    name_el = card.locator(".server-title")
                    name = await name_el.text_content() if await name_el.count() > 0 else server_id
                    
                    status_el = card.locator(".server-status")
                    status_text = await status_el.text_content() if await status_el.count() > 0 else ""
                    is_active = "Active" in status_text
                    
                    pills = await card.locator(".server-pill").all()
                    cpu, ram, disk = "", "", ""
                    for pill in pills:
                        text = await pill.text_content() or ""
                        if "CPU" in text:
                            cpu = text.strip()
                        elif "RAM" in text:
                            ram = text.strip()
                        elif "Disk" in text:
                            disk = text.strip()
                    
                    server = ServerInfo(
                        server_id=server_id,
                        name=name.strip(),
                        short_id=short_id,
                        is_active=is_active,
                        cpu=cpu, ram=ram, disk=disk
                    )
                    servers.append(server)
                    
                    icon = "🟢" if is_active else "🔴"
                    logger.info(f"  {icon} [{server_id}] {name.strip()} - {'Active' if is_active else 'Inactive'}")
                    
                except Exception as e:
                    logger.warning(f"  ⚠️ 解析失败: {e}")
            
        except Exception as e:
            logger.error(f"❌ 获取服务器列表失败: {e}")
        
        return servers
    
    async def start_server(self, server: ServerInfo) -> Tuple[bool, Optional[bytes]]:
        try:
            url = f"{self.ctrl_url}/{server.server_id}"
            logger.info(f"🌐 访问控制台: {url}")
            
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.page.wait_for_timeout(3000)
            
            start_btn = self.page.locator('button:has-text("Start")').first
            if await start_btn.count() == 0:
                logger.info(f"  ℹ️ 未找到Start按钮")
                return False, None
            
            disabled = await start_btn.get_attribute("disabled")
            if disabled is not None:
                logger.info(f"  ✅ 服务器已在运行中")
                return False, None
            
            logger.info(f"  🔴 点击启动...")
            await start_btn.click()
            await self.page.wait_for_timeout(5000)
            
            screenshot = await self.page.screenshot(full_page=True)
            logger.info(f"  🟢 启动完成")
            
            return True, screenshot
            
        except Exception as e:
            logger.error(f"  ❌ 启动失败: {e}")
            return False, None
    
    async def extract_cookies(self) -> Tuple[str, bool]:
        try:
            cookies = await self.ctx.cookies()
            lunes_cookies = {}
            for c in cookies:
                if "lunes.host" in c.get("domain", ""):
                    lunes_cookies[c['name']] = c['value']
            
            if lunes_cookies:
                new_cookie = "; ".join([f"{k}={v}" for k, v in lunes_cookies.items()])
                return new_cookie, True
        except Exception as e:
            logger.error(f"❌ 提取Cookie失败: {e}")
        return "", False


async def process_account(cookie_str: str, idx: int, notifier: Notifier) -> AccountResult:
    result = AccountResult(index=idx + 1)
    
    cookies = parse_cookies(cookie_str)
    if not cookies:
        result.error = "Cookie解析失败"
        return result
    
    logger.info(f"{'='*60}")
    logger.info(f"📌 处理账号 #{idx+1}")
    logger.info(f"🍪 Cookie: {mask_cookie(cookie_str)}")
    logger.info(f"{'='*60}")
    
    async with async_playwright() as p:
        logger.info("🚀 启动浏览器...")
        
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        
        # ⚠️ 关键：使用与获取Cookie时完全相同的 User-Agent 和请求头
        ctx = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Sec-Ch-Ua": '"Not)A;Brand";v="24", "Chromium";v="116"',
                "Sec-Ch-Ua-Arch": '"x86"',
                "Sec-Ch-Ua-Bitness": '"64"',
                "Sec-Ch-Ua-Full-Version": '"116.0.5845.97"',
                "Sec-Ch-Ua-Full-Version-List": '"Not)A;Brand";v="24.0.0.0", "Chromium";v="116.0.5845.97"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Model": '""',
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Ch-Ua-Platform-Version": '"10.0.0"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        
        logger.info(f"🔧 User-Agent: {USER_AGENT[:50]}...")
        logger.info("🍪 注入Cookie...")
        await ctx.add_cookies(cookies)
        
        page = await ctx.new_page()
        
        # 隐藏 webdriver 特征
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)
        
        client = LunesClient(ctx, page)
        
        try:
            servers = await client.get_servers()
            result.servers = servers
            
            if not servers:
                if "/login" in page.url:
                    result.error = "Cookie已失效"
                else:
                    result.error = "无服务器或403错误"
                return result
            
            active = sum(1 for s in servers if s.is_active)
            logger.info(f"📊 统计: {active} 运行中, {len(servers)-active} 已停止")
            
            for server in servers:
                if server.is_active:
                    continue
                
                logger.info(f"🔄 启动服务器 [{server.server_id}] {server.name}")
                started, screenshot = await client.start_server(server)
                
                if started:
                    result.started.append({"server": server, "screenshot": screenshot})
                
                await asyncio.sleep(2)
            
            new_cookie, has_cookie = await client.extract_cookies()
            if has_cookie and new_cookie:
                old_cf = re.search(r'cf_clearance=([^;]+)', cookie_str)
                new_cf = re.search(r'cf_clearance=([^;]+)', new_cookie)
                
                if old_cf and new_cf and old_cf.group(1) != new_cf.group(1):
                    result.cookie_changed = True
                    result.new_cookie = new_cookie
                    logger.info(f"🔄 cf_clearance 已变化!")
                else:
                    result.new_cookie = cookie_str
            else:
                result.new_cookie = cookie_str
            
        except Exception as e:
            result.error = str(e)
            logger.error(f"❌ 异常: {e}")
        finally:
            await ctx.close()
            await browser.close()
            logger.info("🔒 浏览器已关闭")
    
    return result


async def main():
    start_time = datetime.now()
    
    logger.info("=" * 60)
    logger.info("🚀 Lunes Host 自动启动脚本")
    logger.info(f"⏰ 开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)
    
    config = Config.from_env()
    
    logger.info("\n📋 配置检查:")
    logger.info(f"  LUNES_COOKIES: {'✅ 已设置' if config.cookies_list else '❌ 未设置'}")
    logger.info(f"  TG_BOT_TOKEN: {'✅' if config.tg_token else '⚠️'}")
    logger.info(f"  TG_CHAT_ID: {'✅' if config.tg_chat_id else '⚠️'}")
    logger.info(f"  REPO_TOKEN: {'✅' if config.repo_token else '⚠️'}")
    
    if not config.cookies_list:
        logger.error("\n❌ 未设置 LUNES_COOKIES")
        return
    
    logger.info(f"\n📊 共 {len(config.cookies_list)} 个账号")
    
    notifier = Notifier(config.tg_token, config.tg_chat_id)
    github = GitHubManager(config.repo_token, config.repository)
    
    results: List[AccountResult] = []
    
    for i, cookie in enumerate(config.cookies_list):
        result = await process_account(cookie, i, notifier)
        results.append(result)
        if i < len(config.cookies_list) - 1:
            await asyncio.sleep(5)
    
    # 汇总
    total_servers = sum(len(r.servers) for r in results)
    total_started = sum(len(r.started) for r in results)
    total_errors = sum(1 for r in results if r.error)
    cookie_changed = any(r.cookie_changed for r in results)
    
    logger.info("\n" + "=" * 60)
    logger.info("📊 执行汇总")
    logger.info("=" * 60)
    logger.info(f"  账号: {len(results)} | 服务器: {total_servers} | 启动: {total_started} | 错误: {total_errors}")
    
    # 通知
    msg = [
        "🎁 <b>Lunes Host 自动检查</b>",
        f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 账号:{len(results)} 服务器:{total_servers} 启动:{total_started}",
        ""
    ]
    
    for r in results:
        msg.append(f"<b>👤 #{r.index}</b>")
        if r.error:
            msg.append(f"  ❌ {r.error}")
        else:
            for s in r.servers:
                icon = "🟢" if s.is_active else "🔴"
                started_mark = " ⚡" if any(st['server'].server_id == s.server_id for st in r.started) else ""
                msg.append(f"  {icon} {s.name}{started_mark}")
        msg.append("")
    
    await notifier.send("\n".join(msg))
    
    for r in results:
        for st in r.started:
            if st.get("screenshot"):
                await notifier.send_photo(st["screenshot"], f"📸 #{r.index} - {st['server'].name}")
    
    if cookie_changed:
        new_cookies = [r.new_cookie or config.cookies_list[i] for i, r in enumerate(results)]
        await github.update_secret("LUNES_COOKIES", "|||".join(new_cookies))
    
    logger.info(f"\n👋 完成，耗时: {(datetime.now()-start_time).total_seconds():.1f}秒")


if __name__ == "__main__":
    asyncio.run(main())
