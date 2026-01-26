#!/usr/bin/env python3
"""
KataBump 自动续订 - Playwright 版本
模拟真实浏览器行为
"""

import os
import sys
import json
import time
import random
import base64
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    from nacl import encoding, public
except ImportError as e:
    print(f"❌ 缺少依赖: {e}")
    print("请运行: pip install playwright pynacl")
    sys.exit(1)

# ============ 配置 ============
BASE_URL = "https://katabump.com"
RENEW_THRESHOLD_DAYS = 2
PROFILE_DIR = "pw_profiles"
PROXY_SERVER = "http://127.0.0.1:8080"

# ============ 工具函数 ============
def log(msg, level="📋"):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {level} {msg}")

def random_delay(min_sec=0.5, max_sec=2.0):
    """模拟人类操作延迟"""
    time.sleep(random.uniform(min_sec, max_sec))

def human_type(page, selector, text):
    """模拟人类打字"""
    element = page.locator(selector)
    element.click()
    random_delay(0.1, 0.3)
    for char in text:
        element.type(char, delay=random.randint(50, 150))
    random_delay(0.2, 0.5)

# ============ Telegram 通知 ============
def send_telegram(message):
    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    
    if not bot_token or not chat_id:
        log("未配置 Telegram，跳过通知", "⚠️")
        return
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }).encode()
        
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                log("Telegram 通知已发送", "✅")
    except Exception as e:
        log(f"Telegram 发送失败: {e}", "⚠️")

# ============ GitHub Secrets ============
def encrypt_secret(public_key: str, secret_value: str) -> str:
    """加密 secret 值"""
    public_key_bytes = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key_bytes)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")

def update_github_secret(secret_name: str, secret_value: str):
    """更新 GitHub Secret"""
    token = os.environ.get("REPO_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    
    if not token or not repo:
        log("未配置 REPO_TOKEN，无法更新 Secret", "⚠️")
        return False
    
    try:
        # 获取公钥
        url = f"https://api.github.com/repos/{repo}/actions/secrets/public-key"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        })
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            key_data = json.loads(resp.read().decode())
        
        # 加密并更新
        encrypted_value = encrypt_secret(key_data["key"], secret_value)
        
        url = f"https://api.github.com/repos/{repo}/actions/secrets/{secret_name}"
        data = json.dumps({
            "encrypted_value": encrypted_value,
            "key_id": key_data["key_id"]
        }).encode()
        
        req = urllib.request.Request(url, data=data, method="PUT", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"
        })
        
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in [201, 204]:
                log(f"GitHub Secret {secret_name} 已更新", "✅")
                return True
    except Exception as e:
        log(f"更新 GitHub Secret 失败: {e}", "❌")
    
    return False

# ============ 核心逻辑 ============
class KataBumpRenewer:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
    def start_browser(self):
        """启动浏览器"""
        self.playwright = sync_playwright().start()
        
        # 创建 profile 目录
        profile_path = Path(PROFILE_DIR)
        profile_path.mkdir(exist_ok=True)
        
        # 启动浏览器
        self.browser = self.playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox"
            ]
        )
        
        # 创建上下文（带代理和持久化存储）
        self.context = self.browser.new_context(
            proxy={"server": PROXY_SERVER},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York"
        )
        
        # 加载已保存的 cookies
        self.load_cookies()
        
        self.page = self.context.new_page()
        
        # 注入反检测脚本
        self.page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """)
        
        log("浏览器已启动", "✅")
    
    def load_cookies(self):
        """从环境变量加载 cookies"""
        cookies_str = os.environ.get("KATA_COOKIES", "")
        if not cookies_str:
            return
        
        try:
            cookies = json.loads(cookies_str)
            # 转换为 Playwright 格式
            pw_cookies = []
            for name, value in cookies.items():
                pw_cookies.append({
                    "name": name,
                    "value": value,
                    "domain": ".katabump.com",
                    "path": "/"
                })
            self.context.add_cookies(pw_cookies)
            log(f"已加载 {len(pw_cookies)} 个 cookies", "✅")
        except Exception as e:
            log(f"加载 cookies 失败: {e}", "⚠️")
    
    def save_cookies(self):
        """保存 cookies 到 GitHub Secret"""
        try:
            cookies = self.context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies if "katabump" in c.get("domain", "")}
            
            if cookies_dict:
                cookies_json = json.dumps(cookies_dict)
                update_github_secret("KATA_COOKIES", cookies_json)
        except Exception as e:
            log(f"保存 cookies 失败: {e}", "⚠️")
    
    def close_browser(self):
        """关闭浏览器"""
        if self.context:
            self.save_cookies()
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def check_login(self) -> bool:
        """检查是否已登录"""
        try:
            self.page.goto(f"{BASE_URL}/servers", wait_until="networkidle", timeout=30000)
            random_delay(1, 2)
            
            # 检查是否在登录页
            if "/login" in self.page.url:
                return False
            
            # 检查是否有服务器列表
            if self.page.locator("text=My Servers").count() > 0:
                return True
            if self.page.locator(".server-card, [class*='server']").count() > 0:
                return True
                
            return False
        except Exception as e:
            log(f"检查登录状态失败: {e}", "❌")
            return False
    
    def get_servers(self) -> list:
        """获取服务器列表"""
        servers = []
        
        try:
            self.page.goto(f"{BASE_URL}/servers", wait_until="networkidle", timeout=30000)
            random_delay(1, 2)
            
            # 等待页面加载
            self.page.wait_for_selector("a[href*='/servers/']", timeout=10000)
            
            # 获取所有服务器链接
            links = self.page.locator("a[href*='/servers/']").all()
            
            for link in links:
                href = link.get_attribute("href")
                if href and "/servers/" in href:
                    server_id = href.split("/servers/")[-1].split("/")[0].split("?")[0]
                    if server_id.isdigit():
                        # 获取服务器名称
                        name = link.inner_text().strip() or f"Server-{server_id}"
                        servers.append({
                            "id": server_id,
                            "name": name[:20]
                        })
            
            # 去重
            seen = set()
            unique_servers = []
            for s in servers:
                if s["id"] not in seen:
                    seen.add(s["id"])
                    unique_servers.append(s)
            
            return unique_servers
            
        except Exception as e:
            log(f"获取服务器列表失败: {e}", "❌")
            return []
    
    def get_server_expiry(self, server_id: str) -> tuple:
        """获取服务器到期时间"""
        try:
            self.page.goto(f"{BASE_URL}/servers/{server_id}", wait_until="networkidle", timeout=30000)
            random_delay(1, 2)
            
            # 查找到期时间文本
            page_text = self.page.content()
            
            # 尝试多种模式匹配
            import re
            patterns = [
                r"expires?\s*:?\s*(\d{4}-\d{2}-\d{2})",
                r"expiry\s*:?\s*(\d{4}-\d{2}-\d{2})",
                r"valid\s+until\s*:?\s*(\d{4}-\d{2}-\d{2})",
                r"(\d{4}-\d{2}-\d{2})\s*\(?\s*\d+\s*days?\s*(?:left|remaining)",
            ]
            
            for pattern in patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    expiry_str = match.group(1)
                    expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d")
                    days_left = (expiry_date - datetime.utcnow()).days
                    return expiry_date, days_left
            
            log(f"无法解析服务器 {server_id} 的到期时间", "⚠️")
            return None, None
            
        except Exception as e:
            log(f"获取到期时间失败: {e}", "❌")
            return None, None
    
    def renew_server(self, server_id: str, server_name: str) -> bool:
        """续订服务器"""
        try:
            self.page.goto(f"{BASE_URL}/servers/{server_id}", wait_until="networkidle", timeout=30000)
            random_delay(1, 2)
            
            # 模拟鼠标移动
            self.page.mouse.move(random.randint(100, 500), random.randint(100, 500))
            random_delay(0.3, 0.8)
            
            # 查找续订按钮
            renew_btn = None
            selectors = [
                "button:has-text('Renew')",
                "a:has-text('Renew')",
                "[class*='renew']",
                "button:has-text('Extend')",
            ]
            
            for selector in selectors:
                if self.page.locator(selector).count() > 0:
                    renew_btn = self.page.locator(selector).first
                    break
            
            if not renew_btn:
                log(f"未找到续订按钮", "❌")
                return False
            
            # 滚动到按钮位置
            renew_btn.scroll_into_view_if_needed()
            random_delay(0.5, 1)
            
            # 点击续订
            renew_btn.click()
            random_delay(2, 3)
            
            # 检查是否需要确认
            confirm_selectors = [
                "button:has-text('Confirm')",
                "button:has-text('Yes')",
                "button:has-text('OK')",
            ]
            
            for selector in confirm_selectors:
                if self.page.locator(selector).count() > 0:
                    random_delay(0.5, 1)
                    self.page.locator(selector).first.click()
                    random_delay(1, 2)
                    break
            
            # 等待页面响应
            self.page.wait_for_load_state("networkidle", timeout=10000)
            
            # 验证续订成功
            new_expiry, new_days = self.get_server_expiry(server_id)
            if new_expiry and new_days > RENEW_THRESHOLD_DAYS:
                log(f"续订成功！新到期: {new_expiry.strftime('%Y-%m-%d')}", "✅")
                return True
            
            return False
            
        except PlaywrightTimeout:
            log("操作超时", "❌")
            return False
        except Exception as e:
            log(f"续订失败: {e}", "❌")
            return False
    
    def run(self):
        """主运行逻辑"""
        log("=" * 50)
        log("KataBump 自动续订 (Playwright)")
        log("=" * 50)
        
        force_renew = os.environ.get("FORCE_RENEW", "false").lower() == "true"
        results = []
        
        try:
            self.start_browser()
            
            # 检查登录状态
            log("检查登录状态...")
            if not self.check_login():
                log("未登录或 cookies 已过期", "❌")
                send_telegram("❌ <b>KataBump</b>\n\nCookies 已过期，请更新！")
                return
            
            log("已登录", "✅")
            random_delay(1, 2)
            
            # 获取服务器列表
            log("获取服务器列表...")
            servers = self.get_servers()
            
            if not servers:
                log("未找到服务器", "❌")
                return
            
            log(f"找到 {len(servers)} 个服务器", "✅")
            
            # 处理每个服务器
            for server in servers:
                server_id = server["id"]
                server_name = server["name"]
                
                log("")
                log(f"━━━ {server_name} (ID: {server_id}) ━━━")
                
                random_delay(1, 2)
                
                # 获取到期时间
                expiry_date, days_left = self.get_server_expiry(server_id)
                
                if expiry_date is None:
                    log("无法获取到期时间", "⚠️")
                    results.append(f"⚠️ {server_name}: 无法获取状态")
                    continue
                
                log(f"到期: {expiry_date.strftime('%Y-%m-%d')} | 剩余: {days_left} 天")
                
                # 判断是否需要续订
                need_renew = days_left <= RENEW_THRESHOLD_DAYS or force_renew
                
                if not need_renew:
                    log("无需续订", "✅")
                    results.append(f"✅ {server_name}: {days_left}天后到期")
                    continue
                
                # 执行续订
                reason = "强制续订" if force_renew else f"剩余{days_left}天"
                log(f"开始续订 ({reason})...")
                
                random_delay(1, 2)
                
                if self.renew_server(server_id, server_name):
                    new_expiry, new_days = self.get_server_expiry(server_id)
                    if new_expiry:
                        results.append(f"🎉 {server_name}: 续订成功，新到期 {new_expiry.strftime('%Y-%m-%d')}")
                    else:
                        results.append(f"🎉 {server_name}: 续订成功")
                else:
                    results.append(f"❌ {server_name}: 续订失败")
                
                random_delay(2, 4)
            
        except Exception as e:
            log(f"运行出错: {e}", "❌")
            results.append(f"❌ 运行出错: {e}")
        
        finally:
            self.close_browser()
        
        # 发送汇总通知
        log("")
        log("=" * 50)
        log("完成")
        
        if results:
            summary = "\n".join(results)
            message = f"📋 <b>KataBump 续订报告</b>\n\n{summary}"
            send_telegram(message)
        
        log("🏁 结束")

# ============ 入口 ============
if __name__ == "__main__":
    renewer = KataBumpRenewer()
    renewer.run()
