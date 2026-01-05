#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Castle-Host 服务器自动续约脚本
"""

import os
import asyncio
import aiohttp
import re
import json
import logging
from datetime import datetime
from playwright.async_api import async_playwright
import sys

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('castle_renew.log')
    ]
)
logger = logging.getLogger(__name__)

# 存储续约数据
renewal_data = {
    "server_id": "",
    "before_expiry": "",
    "after_expiry": "",
    "renewal_time": "",
    "success": False,
    "status": "",
    "error_message": ""
}

# ------------------ 日期格式转换 ------------------
def convert_date_format(date_str):
    """将 DD.MM.YYYY 转换为 YYYY-MM-DD"""
    if not date_str or date_str == "Unknown":
        return date_str
    try:
        # 12.01.2026 -> 2026-01-12
        if re.match(r'\d{2}\.\d{2}\.\d{4}', date_str):
            parts = date_str.split('.')
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
        return date_str
    except:
        return date_str

def parse_date(date_str):
    """解析日期字符串为datetime对象"""
    try:
        formats = ['%d.%m.%Y', '%Y-%m-%d', '%Y年%m月%d日']
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
    except:
        return None

def calculate_days_left(date_str):
    """计算距离到期还有多少天"""
    date_obj = parse_date(date_str)
    if date_obj:
        return (date_obj - datetime.now()).days
    return None

# ------------------ Telegram 通知 ------------------
async def tg_notify(message: str, token=None, chat_id=None):
    """发送Telegram通知"""
    token = token or os.environ.get("TG_BOT_TOKEN")
    chat_id = chat_id or os.environ.get("TG_CHAT_ID")
        
    if not token or not chat_id:
        logger.info("ℹ️ Telegram通知未配置")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }, timeout=10) as resp:
                if resp.status == 200:
                    logger.info("✅ Telegram通知已发送")
                    return True
                logger.warning(f"⚠️ Telegram通知发送失败: {resp.status}")
                return False
    except Exception as e:
        logger.error(f"⚠️ TG通知失败: {e}")
        return False

# ------------------ Cookie 解析 ------------------
def parse_cookie_string(cookie_str: str):
    """解析Cookie字符串"""
    cookies = []
    for part in cookie_str.split(';'):
        part = part.strip()
        if '=' in part:
            name, value = part.split('=', 1)
            cookies.append({
                "name": name.strip(),
                "value": value.strip(),
                "domain": ".castle-host.com",
                "path": "/"
            })
    logger.info(f"✅ 成功解析 {len(cookies)} 个Cookie")
    return cookies

# ------------------ 提取到期时间 ------------------
async def extract_expiry_date(page):
    """从页面提取服务器到期时间"""
    try:
        body_text = await page.text_content('body')
        
        patterns = [
            r'Сервер действует до (\d{2}\.\d{2}\.\d{4})',
            r'Оплачено до (\d{2}\.\d{2}\.\d{4})',
            r'(\d{2}\.\d{2}\.\d{4})\s*\([^)]*\)',
            r'\b(\d{2}\.\d{2}\.\d{4})\b'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, body_text)
            if match:
                return match.group(1)
        return None
    except Exception as e:
        logger.error(f"❌ 提取到期时间失败: {e}")
        return None

# ------------------ 提取余额 ------------------
async def extract_balance(page):
    """提取账户余额"""
    try:
        body_text = await page.text_content('body')
        match = re.search(r'(\d+\.\d+)\s*₽', body_text)
        return match.group(1) if match else "0.00"
    except:
        return "0.00"

# ------------------ 分析错误信息 ------------------
def analyze_error_message(error_msg):
    """分析错误信息，返回简化的中文描述"""
    error_lower = error_msg.lower()
    
    if '24 час' in error_lower or '24 hour' in error_lower:
        return "rate_limited", "今日已续期"
    
    if 'уже продлен' in error_lower:
        return "already_renewed", "今日已续期"
    
    if 'недостаточно' in error_lower:
        return "insufficient_funds", "余额不足"
    
    if 'максимальн' in error_lower:
        return "max_period", "已达最大期限"
    
    if 'vk' in error_lower or 'вк' in error_lower:
        return "vk_required", "需要VK验证"
    
    return "unknown", error_msg

# ------------------ 续约执行 ------------------
async def perform_renewal(page, server_id):
    """执行续约操作"""
    logger.info(f"🔄 开始续约流程，服务器ID: {server_id}")
    
    api_response = {"status": None, "body": None}
    
    try:
        # 查找续约按钮
        selectors = ['#freebtn', 'button:has-text("Продлить")', 'button[onclick*="freePay"]']
        
        for selector in selectors:
            button = page.locator(selector)
            if await button.count() > 0:
                logger.info(f"🖱️ 找到续约按钮: {selector}")
                
                if await button.get_attribute("disabled"):
                    return {"success": False, "error_type": "button_disabled", "message": "按钮已禁用"}
                
                # 监听API响应
                async def handle_response(response):
                    if "/buy_months/" in response.url:
                        api_response["status"] = response.status
                        try:
                            api_response["body"] = await response.json()
                            logger.info(f"📡 API响应: {json.dumps(api_response['body'], ensure_ascii=False)}")
                        except:
                            pass
                
                page.on("response", handle_response)
                await button.click()
                logger.info("🖱️ 已点击续约按钮")
                
                # 等待响应
                for _ in range(20):
                    if api_response["body"]:
                        break
                    await asyncio.sleep(0.5)
                
                # 解析响应
                if api_response["body"] and isinstance(api_response["body"], dict):
                    body = api_response["body"]
                    status = body.get("status", "")
                    
                    if status == "error":
                        error_msg = body.get("error", "未知错误")
                        error_type, error_desc = analyze_error_message(error_msg)
                        logger.warning(f"⚠️ 服务器返回: {error_msg}")
                        return {"success": False, "error_type": error_type, "message": error_desc}
                    
                    if status in ["success", "ok"]:
                        logger.info("✅ 服务器确认续期成功")
                        return {"success": True, "error_type": None, "message": "续期成功"}
                
                await page.wait_for_timeout(3000)
                
                # 检查页面提示
                page_text = await page.text_content('body')
                if '24 час' in page_text:
                    return {"success": False, "error_type": "rate_limited", "message": "今日已续期"}
                
                if re.search(r'Сервер продлен|продлен успешно', page_text, re.IGNORECASE):
                    return {"success": True, "error_type": None, "message": "续期成功"}
                
                return {"success": None, "error_type": "unknown", "message": "需要验证"}
        
        # 尝试JavaScript
        try:
            result = await page.evaluate("typeof freePay === 'function' ? (freePay(), true) : false")
            if result:
                await page.wait_for_timeout(3000)
                return {"success": None, "message": "需要验证"}
        except:
            pass
        
        return {"success": False, "error_type": "no_button", "message": "未找到续约按钮"}
        
    except Exception as e:
        logger.error(f"❌ 续约出错: {e}")
        return {"success": False, "error_type": "exception", "message": str(e)}

# ------------------ 验证续约结果 ------------------
async def verify_renewal(page, original_expiry):
    """验证续约是否成功"""
    try:
        await asyncio.sleep(2)
        await page.reload(wait_until="networkidle")
        await asyncio.sleep(2)
        
        new_expiry = await extract_expiry_date(page)
        if not new_expiry:
            return None, 0
        
        logger.info(f"📅 续约前: {original_expiry} -> 续约后: {new_expiry}")
        
        if original_expiry and new_expiry:
            old_date = parse_date(original_expiry)
            new_date = parse_date(new_expiry)
            if old_date and new_date:
                days_added = (new_date - old_date).days
                return new_expiry, days_added
        
        return new_expiry, 0
    except Exception as e:
        logger.error(f"❌ 验证失败: {e}")
        return None, 0

# ------------------ 主函数 ------------------
async def main():
    logger.info("=" * 60)
    logger.info("Castle-Host 服务器自动续约脚本")
    logger.info("=" * 60)
    
    # 环境变量
    cookie_str = os.environ.get("CASTLE_COOKIES", "").strip()
    server_id = os.environ.get("SERVER_ID", "117954")
    tg_token = os.environ.get("TG_BOT_TOKEN")
    tg_chat_id = os.environ.get("TG_CHAT_ID")
    force_renew = os.environ.get("FORCE_RENEW", "false").lower() == "true"
    renew_threshold = int(os.environ.get("RENEW_THRESHOLD", "3"))
    
    if not cookie_str:
        logger.error("❌ 未设置 CASTLE_COOKIES")
        return
    
    renewal_data["server_id"] = server_id
    renewal_data["renewal_time"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    cookies = parse_cookie_string(cookie_str)
    if not cookies:
        logger.error("❌ Cookie解析失败")
        return
    
    server_url = f"https://cp.castle-host.com/servers/pay/index/{server_id}"
    
    logger.info("🚀 启动浏览器...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        
        await context.add_cookies(cookies)
        page = await context.new_page()
        page.set_default_timeout(60000)
        
        try:
            logger.info(f"🌐 访问: {server_url}")
            await page.goto(server_url, wait_until="networkidle")
            
            if "login" in page.url or "auth" in page.url:
                error_msg = "❌ Cookie已失效，请重新获取"
                logger.error(error_msg)
                await tg_notify(f"❌ Castle-Host Cookie已失效\n\n🆔 服务器: {server_id}\n🔗 {server_url}", tg_token, tg_chat_id)
                return
            
            logger.info("✅ 登录成功")
            
            # 提取信息
            original_expiry = await extract_expiry_date(page)
            balance = await extract_balance(page)
            renewal_data["before_expiry"] = original_expiry
            
            # 计算剩余天数
            days_left = calculate_days_left(original_expiry) if original_expiry else None
            
            # 转换日期格式
            expiry_formatted = convert_date_format(original_expiry) if original_expiry else "Unknown"
            
            logger.info(f"📅 到期时间: {expiry_formatted}, 剩余: {days_left} 天")
            
            # 检查是否需要续约
            if days_left and days_left > renew_threshold and not force_renew:
                logger.info(f"ℹ️ 剩余 {days_left} 天，跳过续约")
                
                message = f"""ℹ️ Castle-Host 状态正常

🆔 服务器: {server_id}
📅 到期时间: {expiry_formatted}
⏳ 剩余天数: {days_left} 天
💰 余额: {balance} ₽

📝 无需续期"""
                
                await tg_notify(message, tg_token, tg_chat_id)
                renewal_data["success"] = True
                renewal_data["status"] = "skipped"
                renewal_data["after_expiry"] = original_expiry
                
            else:
                # 执行续约
                result = await perform_renewal(page, server_id)
                renewal_data["status"] = result.get("error_type", "unknown")
                
                if result["success"] == True:
                    # 成功
                    new_expiry, days_added = await verify_renewal(page, original_expiry)
                    new_expiry_formatted = convert_date_format(new_expiry) if new_expiry else "Unknown"
                    renewal_data["after_expiry"] = new_expiry
                    renewal_data["success"] = True
                    
                    message = f"""✅ Castle-Host 续约成功

🆔 服务器: {server_id}
📅 到期时间: {new_expiry_formatted}
📈 续期: +{days_added} 天
💰 余额: {balance} ₽"""
                    
                    logger.info("🎉 续约成功！")
                    
                elif result["success"] == False:
                    # 失败
                    error_type = result.get("error_type", "unknown")
                    error_msg = result.get("message", "未知错误")
                    
                    renewal_data["success"] = False
                    renewal_data["after_expiry"] = original_expiry
                    renewal_data["error_message"] = error_msg
                    
                    # 选择图标
                    if error_type == "rate_limited":
                        icon = "⏰"
                    elif error_type == "already_renewed":
                        icon = "✅"
                    else:
                        icon = "⚠️"
                    
                    message = f"""{icon} Castle-Host 续约提示

🆔 服务器: {server_id}
📅 到期时间: {expiry_formatted}
⏳ 剩余天数: {days_left} 天
💰 余额: {balance} ₽

📋 {error_msg}"""
                    
                    if error_type == "rate_limited":
                        logger.info("⏰ 今日已续期")
                    else:
                        logger.warning(f"⚠️ {error_msg}")
                    
                else:
                    # 不确定，验证
                    new_expiry, days_added = await verify_renewal(page, original_expiry)
                    new_expiry_formatted = convert_date_format(new_expiry) if new_expiry else "Unknown"
                    renewal_data["after_expiry"] = new_expiry
                    
                    if new_expiry and new_expiry != original_expiry and days_added > 0:
                        renewal_data["success"] = True
                        message = f"""✅ Castle-Host 续约成功

🆔 服务器: {server_id}
📅 到期时间: {new_expiry_formatted}
📈 续期: +{days_added} 天
💰 余额: {balance} ₽"""
                        logger.info("🎉 续约成功！")
                    else:
                        renewal_data["success"] = False
                        message = f"""⏰ Castle-Host 续约提示

🆔 服务器: {server_id}
📅 到期时间: {expiry_formatted}
⏳ 剩余天数: {days_left} 天
💰 余额: {balance} ₽

📋 今日已续期"""
                        logger.info("⏰ 今日已续期")
                
                await tg_notify(message, tg_token, tg_chat_id)
            
            # 保存记录
            with open("renewal_history.json", "a", encoding="utf-8") as f:
                json.dump(renewal_data, f, ensure_ascii=False)
                f.write("\n")
            
            await page.screenshot(path="renewal_result.png", full_page=True)
            
        except Exception as e:
            logger.error(f"❌ 错误: {e}", exc_info=True)
            await tg_notify(f"❌ Castle-Host 脚本错误\n\n{str(e)}", tg_token, tg_chat_id)
            
        finally:
            await context.close()
            await browser.close()
            logger.info("👋 完成")

if __name__ == "__main__":
    print("Castle-Host 自动续约脚本")
    
    if not os.environ.get("CASTLE_COOKIES"):
        print("❌ 请设置 CASTLE_COOKIES 环境变量")
        print("   export CASTLE_COOKIES=\"PHPSESSID=xxx; uid=xxx\"")
        sys.exit(1)
    
    asyncio.run(main())
