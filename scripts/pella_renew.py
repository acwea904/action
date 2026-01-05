#!/usr/bin/env python3
"""
Pella 自动续期脚本 (增强稳定性 - 使用 JavaScript 强制输入绕过交互问题)
支持单账号和多账号

配置变量说明:
- 单账号变量:
    - PELLA_EMAIL / LEAFLOW_EMAIL=登录邮箱
    - PELLA_PASSWORD / LEAFLOW_PASSWORD=登录密码
- 多账号变量:
    - PELLA_ACCOUNTS / LEAFLOW_ACCOUNTS: 格式：邮箱1:密码1,邮箱2:密码2,邮箱3:密码3
- 通知变量 (可选):
    - TG_BOT_TOKEN=Telegram 机器人 Token
    - TG_CHAT_ID=Telegram 聊天 ID
"""
import os
import time
import logging
import re
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def mask_email(email):
    """隐藏邮箱地址"""
    if not email or '@' not in email:
        return '***'
    name, domain = email.split('@', 1)
    if len(name) <= 2:
        masked = '*' * len(name)
    else:
        masked = name[0] + '*' * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def mask_url(url):
    """隐藏URL中的敏感ID"""
    if not url:
        return '***'
    match = re.search(r'/server/([a-f0-9]+)', url)
    if match:
        sid = match.group(1)
        if len(sid) > 8:
            return url.replace(sid, sid[:4] + '***' + sid[-4:])
    return url


class PellaAutoRenew:
    LOGIN_URL = "https://www.pella.app/login"
    HOME_URL = "https://www.pella.app/home"
    RENEW_WAIT_TIME = 8
    WAIT_TIME_AFTER_LOGIN = 20

    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.initial_expiry_details = "N/A"
        self.initial_expiry_value = -1.0
        self.server_url = None
        
        if not self.email or not self.password:
            raise ValueError("邮箱和密码不能为空")
        
        self.driver = None
        self.setup_driver()
    
    def setup_driver(self):
        chrome_options = Options()
        
        if os.getenv('GITHUB_ACTIONS'):
            chrome_options.add_argument('--headless=new')
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument('--window-size=1920,1080')
        
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        except WebDriverException as e:
            logger.error(f"驱动初始化失败: {e}")
            raise

    def wait_for_element_clickable(self, by, value, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.element_to_be_clickable((by, value))
        )
    
    def wait_for_element_present(self, by, value, timeout=10):
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )

    def extract_expiry_days(self, page_source):
        match = re.search(r"Your server expires in\s*(\d+)D\s*(\d+)H\s*(\d+)M", page_source)
        if match:
            d, h, m = int(match.group(1)), int(match.group(2)), int(match.group(3))
            return f"{d}天{h}时{m}分", d + h/24 + m/1440
            
        match = re.search(r"Your server expires in\s*(\d+)D", page_source)
        if match:
            d = int(match.group(1))
            return f"{d}天", float(d)
            
        return "无法提取", -1.0

    def find_and_click_button(self):
        selectors = [
            "button.cl-formButtonPrimary",
            "button[data-localization-key='formButtonPrimary']",
            "//button[.//span[contains(text(), 'Continue')]]",
            "//button[contains(@class, 'cl-formButtonPrimary')]",
            "button[type='submit']",
            "form button"
        ]
        
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    btn = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                
                self.driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                time.sleep(0.3)
                self.driver.execute_script("arguments[0].click();", btn)
                return True
            except:
                continue
        return False

    def wait_for_password_field(self, timeout=15):
        selectors = [
            "input[type='password']",
            "input[name='password']",
            "input.cl-formFieldInput[type='password']",
            "#password",
        ]
        
        start = time.time()
        while time.time() - start < timeout:
            for sel in selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if elem.is_displayed():
                        return elem
                except:
                    pass
            time.sleep(0.5)
        return None

    def check_for_error(self):
        selectors = [
            ".cl-formFieldErrorText",
            "[data-localization-key*='error']",
            ".error-message",
        ]
        for sel in selectors:
            try:
                err = self.driver.find_element(By.CSS_SELECTOR, sel)
                if err.is_displayed():
                    return err.text
            except:
                pass
        return None

    def login(self):
        logger.info("开始登录")
        self.driver.get(self.LOGIN_URL)
        time.sleep(4)
        
        def js_set_value(element, value):
            element.clear()
            element.click()
            time.sleep(0.2)
            element.send_keys(value)
            time.sleep(0.2)
            self.driver.execute_script("""
                arguments[0].value = arguments[1];
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
            """, element, value)
        
        # 输入邮箱
        try:
            email_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[name='identifier']", 15)
            js_set_value(email_input, self.email)
            if email_input.get_attribute('value') != self.email:
                email_input.clear()
                email_input.send_keys(self.email)
            logger.info("邮箱输入完成")
        except Exception as e:
            raise Exception(f"输入邮箱失败: {e}")
            
        # 点击继续
        try:
            time.sleep(1)
            if not self.find_and_click_button():
                raise Exception("无法点击Continue按钮")
            
            password_input = self.wait_for_password_field(timeout=15)
            if not password_input:
                error = self.check_for_error()
                if error:
                    raise Exception(f"登录错误: {error}")
                raise Exception("密码框未出现")
            
            logger.info("进入密码步骤")
            time.sleep(1)
        except Exception as e:
            raise Exception(f"第一步失败: {e}")

        # 输入密码
        try:
            password_input = self.wait_for_element_present(By.CSS_SELECTOR, "input[type='password']", 10)
            js_set_value(password_input, self.password)
            logger.info("密码输入完成")
        except Exception as e:
            raise Exception(f"输入密码失败: {e}")

        # 提交登录
        try:
            time.sleep(2)
            if not self.find_and_click_button():
                raise Exception("无法点击登录按钮")
        except Exception as e:
            raise Exception(f"点击登录失败: {e}")

        # 验证登录
        try:
            for _ in range(self.WAIT_TIME_AFTER_LOGIN // 2):
                time.sleep(2)
                url = self.driver.current_url
                
                if '/home' in url or '/dashboard' in url:
                    logger.info("登录成功")
                    return True
                
                error = self.check_for_error()
                if error:
                    raise Exception(f"登录失败: {error}")
                
                if '/login' not in url and '/sign-in' not in url:
                    self.driver.get(self.HOME_URL)
                    time.sleep(2)
                    if '/home' in self.driver.current_url:
                        logger.info("登录成功")
                        return True
            
            self.driver.get(self.HOME_URL)
            time.sleep(3)
            if '/home' in self.driver.current_url:
                logger.info("登录成功")
                return True
            
            raise Exception("登录超时")
        except Exception as e:
            raise Exception(f"登录验证失败: {e}")

    def get_server_url(self):
        if '/home' not in self.driver.current_url:
            self.driver.get(self.HOME_URL)
            time.sleep(3)
            
        try:
            link = self.wait_for_element_clickable(By.CSS_SELECTOR, "a[href*='/server/']", 15)
            link.click()
            WebDriverWait(self.driver, 10).until(EC.url_contains("/server/"))
            self.server_url = self.driver.current_url
            logger.info(f"服务器: {mask_url(self.server_url)}")
            return True
        except Exception as e:
            raise Exception(f"获取服务器失败: {e}")
    
    def renew_server(self):
        if not self.server_url:
            raise Exception("缺少服务器URL")
            
        self.driver.get(self.server_url)
        time.sleep(5)

        self.initial_expiry_details, self.initial_expiry_value = self.extract_expiry_days(self.driver.page_source)
        logger.info(f"当前过期: {self.initial_expiry_details}")

        if self.initial_expiry_value == -1.0:
            raise Exception("无法提取过期时间")

        try:
            selector = "a[href*='/renew/']:not(.opacity-50):not(.pointer-events-none)"
            count = 0
            original = self.driver.current_window_handle
            
            while True:
                buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if not buttons:
                    break

                url = buttons[0].get_attribute('href')
                logger.info(f"续期 #{count + 1}")
                
                self.driver.execute_script("window.open(arguments[0]);", url)
                time.sleep(1)
                self.driver.switch_to.window(self.driver.window_handles[-1])
                time.sleep(self.RENEW_WAIT_TIME)
                self.driver.close()
                self.driver.switch_to.window(original)
                count += 1
                
                self.driver.get(self.server_url)
                time.sleep(3)

            if count == 0:
                disabled = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/renew/'].opacity-50")
                return "今日已续期" if disabled else "未找到续期按钮"

            self.driver.get(self.server_url)
            time.sleep(5)
            
            final, final_val = self.extract_expiry_days(self.driver.page_source)
            logger.info(f"续期后: {final}")
            
            if final_val > self.initial_expiry_value:
                return f"续期成功 {self.initial_expiry_details} -> {final}"
            return f"天数未变化 ({final})"

        except Exception as e:
            raise Exception(f"续期错误: {e}")
            
    def run(self):
        try:
            logger.info(f"处理账号: {mask_email(self.email)}")
            
            if self.login() and self.get_server_url():
                result = self.renew_server()
                logger.info(f"结果: {result}")
                return True, result
            return False, "登录或获取服务器失败"
                
        except Exception as e:
            logger.error(f"失败: {e}")
            return False, f"失败: {e}"
        finally:
            if self.driver:
                self.driver.quit()


class MultiAccountManager:
    def __init__(self):
        self.tg_token = os.getenv('TG_BOT_TOKEN', '')
        self.tg_chat = os.getenv('TG_CHAT_ID', '')
        self.accounts = self.load_accounts()
    
    def load_accounts(self):
        accounts = []
        
        accounts_str = os.getenv('PELLA_ACCOUNTS', os.getenv('LEAFLOW_ACCOUNTS', '')).strip()
        if accounts_str:
            for pair in [p.strip() for p in re.split(r'[;,]', accounts_str) if p.strip()]:
                if ':' in pair:
                    email, pwd = pair.split(':', 1)
                    if email.strip() and pwd.strip():
                        accounts.append({'email': email.strip(), 'password': pwd.strip()})
            if accounts:
                logger.info(f"加载 {len(accounts)} 个账号")
                return accounts
        
        email = os.getenv('PELLA_EMAIL', os.getenv('LEAFLOW_EMAIL', '')).strip()
        pwd = os.getenv('PELLA_PASSWORD', os.getenv('LEAFLOW_PASSWORD', '')).strip()
        
        if email and pwd:
            accounts.append({'email': email, 'password': pwd})
            logger.info("加载单账号")
            return accounts
        
        raise ValueError("未找到账号配置")
    
    def send_notification(self, results):
        if not self.tg_token or not self.tg_chat:
            return
        
        try:
            msg = f"Pella续期 ({len(results)}个账号)\n\n"
            for email, _, result in results:
                status = "✓" if "成功" in result else ("○" if "已续期" in result else "✗")
                msg += f"{status} {mask_email(email)}: {result[:50]}\n"
            
            requests.post(
                f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                data={"chat_id": self.tg_chat, "text": msg},
                timeout=10
            )
            logger.info("通知已发送")
        except Exception as e:
            logger.error(f"通知失败: {e}")
    
    def run_all(self):
        results = []
        total = len(self.accounts)
        
        for i, acc in enumerate(self.accounts, 1):
            logger.info(f"[{i}/{total}] {mask_email(acc['email'])}")
            
            try:
                renew = PellaAutoRenew(acc['email'], acc['password'])
                success, result = renew.run()
                if i < total:
                    time.sleep(5)
            except Exception as e:
                success, result = False, f"异常: {e}"
            
            results.append((acc['email'], success, result))
        
        self.send_notification(results)
        return all(s for _, s, _ in results), results


def main():
    try:
        manager = MultiAccountManager()
        manager.run_all()
    except Exception as e:
        logger.error(f"错误: {e}")
        exit(1)


if __name__ == "__main__":
    main()
