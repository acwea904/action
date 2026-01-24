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
      const [name, value] = c.split('=');
      return { name, value, domain: '.taoiptv.com', path: '/', secure: true, sameSite: 'None' };
    });
    await context.addCookies(cookies);
  } else {
    context = await browser.newContext({ userAgent });
  }

  const page = await context.newPage();
  
  // 设置额外 headers
  await page.setExtraHTTPHeaders({
    'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'sec-ch-ua': '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"'
  });

  try {
    console.log(`访问: ${config.SEARCH_URL}`);
    const response = await page.goto(config.SEARCH_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
    console.log(`状态码: ${response?.status()}`);

    const content = await page.content();
    if (content.includes('challenge-platform')) {
      console.log('❌ 被Cloudflare拦截');
      await page.screenshot({ path: 'cf-blocked.png' });
      process.exit(1);
    }

    await page.waitForSelector('#copyToken', { timeout: 30000 });
    const token = await page.$eval('#copyToken', el => el.getAttribute('data-clipboard-text'));
    console.log(`Token: ${token}`);

    const txtUrl = `https://taoiptv.com/lives/44023.txt?token=${token}`;
    console.log(`获取: ${txtUrl}`);
    await page.goto(txtUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    const bodyText = await page.evaluate(() => document.body.innerText);

    if (!bodyText || bodyText.length < 100) {
      console.log('❌ TXT内容异常');
      process.exit(1);
    }

    fs.writeFileSync('taoiptv.m3u', convertToM3U(bodyText), 'utf8');
    console.log('✅ 已生成 taoiptv.m3u');

    await context.storageState({ path: STATE_FILE });
    console.log('✅ 状态已保存');

  } catch (error) {
    console.error('错误:', error.message);
    await page.screenshot({ path: 'error.png' }).catch(() => {});
    process.exit(1);
  } finally {
    await browser.close();
  }
}

function convertToM3U(txt) {
  const lines = txt.trim().split('\n');
  let m3u = '#EXTM3U\n';
  let currentGroup = '其他';

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.includes(',#genre#')) {
      currentGroup = trimmed.replace(',#genre#', '').trim();
      continue;
    }
    const match = trimmed.match(/^(.+?),(https?:\/\/.+)$/);
    if (match) {
      const [, name, url] = match;
      m3u += `#EXTINF:-1 group-title="${currentGroup}",${name}\n${url}\n`;
    }
  }
  return m3u;
}

main();
