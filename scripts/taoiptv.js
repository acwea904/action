const { chromium } = require('playwright');
const fs = require('fs');

const config = JSON.parse(process.env.TV_ACCOUNTS || '{}');
const STATE_FILE = 'taoiptv-state.json';

async function main() {
  const browser = await chromium.launch({
    headless: true,
    proxy: config.HY2_URL ? { server: 'socks5://127.0.0.1:1080' } : undefined
  });

  const userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36';

  let context;
  if (fs.existsSync(STATE_FILE)) {
    console.log('恢复浏览器状态...');
    context = await browser.newContext({ storageState: STATE_FILE, userAgent });
  } else if (config.COOKIES) {
    console.log('使用初始 Cookies...');
    context = await browser.newContext({ userAgent });
    const cookies = config.COOKIES.split('; ').map(c => {
      const [name, ...rest] = c.split('=');
      return { name, value: rest.join('='), domain: '.taoiptv.com', path: '/', secure: true, sameSite: 'None' };
    });
    await context.addCookies(cookies);
  } else {
    context = await browser.newContext({ userAgent });
  }

  const page = await context.newPage();
  await page.setExtraHTTPHeaders({
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"'
  });

  try {
    console.log(`访问: ${config.SEARCH_URL}`);
    const response = await page.goto(config.SEARCH_URL, { waitUntil: 'networkidle', timeout: 60000 });
    console.log(`状态码: ${response?.status()}`);

    // 截图调试
    await page.screenshot({ path: 'page-loaded.png', fullPage: true });
    
    // 打印页面标题和部分HTML
    const title = await page.title();
    console.log(`页面标题: ${title}`);
    
    const bodyHtml = await page.evaluate(() => document.body.innerHTML.substring(0, 2000));
    console.log('页面内容预览:');
    console.log(bodyHtml);

    // 检查可能的选择器
    const selectors = ['#copyToken', '.copyToken', '[data-clipboard-text]', 'button[id*="copy"]', '.token'];
    for (const sel of selectors) {
      const count = await page.locator(sel).count();
      console.log(`${sel}: ${count}个`);
    }

    // 尝试等待
    const tokenEl = await page.waitForSelector('#copyToken', { timeout: 10000 }).catch(() => null);
    if (!tokenEl) {
      console.log('❌ 未找到 #copyToken');
      process.exit(1);
    }

    const token = await tokenEl.getAttribute('data-clipboard-text');
    console.log(`Token: ${token}`);

    const txtUrl = `https://taoiptv.com/lives/44023.txt?token=${token}`;
    await page.goto(txtUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    const bodyText = await page.evaluate(() => document.body.innerText);

    fs.writeFileSync('taoiptv.m3u', convertToM3U(bodyText), 'utf8');
    console.log('✅ 已生成 taoiptv.m3u');
    await context.storageState({ path: STATE_FILE });

  } catch (error) {
    console.error('错误:', error.message);
    await page.screenshot({ path: 'error.png' }).catch(() => {});
    fs.writeFileSync('error.html', await page.content().catch(() => ''));
    process.exit(1);
  } finally {
    await browser.close();
  }
}

function convertToM3U(txt) {
  const lines = txt.trim().split('\n');
  let m3u = '#EXTM3U\n';
  let group = '其他';
  for (const line of lines) {
    const t = line.trim();
    if (!t) continue;
    if (t.includes(',#genre#')) { group = t.replace(',#genre#', ''); continue; }
    const m = t.match(/^(.+?),(https?:\/\/.+)$/);
    if (m) m3u += `#EXTINF:-1 group-title="${group}",${m[1]}\n${m[2]}\n`;
  }
  return m3u;
}

main();
