const { chromium } = require('playwright');
const fs = require('fs');

const config = JSON.parse(process.env.TV_ACCOUNTS || '{}');

async function main() {
  const browser = await chromium.launch({
    headless: true,
    proxy: config.HY2_URL ? { server: 'socks5://127.0.0.1:1080' } : undefined
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36'
  });

  if (config.COOKIES) {
    const cookies = config.COOKIES.split('; ').map(c => {
      const [name, ...rest] = c.split('=');
      return { name, value: rest.join('='), domain: '.taoiptv.com', path: '/' };
    });
    await context.addCookies(cookies);
  }

  const page = await context.newPage();

  try {
    console.log(`访问: ${config.SEARCH_URL}`);
    await page.goto(config.SEARCH_URL, { waitUntil: 'networkidle', timeout: 60000 });

    // 等待页面完全渲染
    await page.waitForSelector('#copyToken', { timeout: 30000 });

    const html = await page.content();
    console.log(`HTML长度: ${html.length}`);

    const tokenMatch = html.match(/data-clipboard-text="([a-f0-9]{16})"/);
    if (!tokenMatch) {
      console.log('❌ 未找到Token');
      fs.writeFileSync('error.html', html);
      process.exit(1);
    }
    const token = tokenMatch[1];
    console.log(`Token: ${token}`);

    const idMatch = html.match(/lives\/(\d+)\.txt/);
    const txtId = idMatch ? idMatch[1] : '44023';

    const txtUrl = `https://taoiptv.com/lives/${txtId}.txt?token=${token}`;
    console.log(`获取: ${txtUrl}`);
    const txtResponse = await page.goto(txtUrl, { timeout: 30000 });
    const bodyText = await txtResponse.text();

    if (!bodyText || bodyText.length < 100) {
      console.log('❌ TXT内容异常');
      process.exit(1);
    }

    fs.writeFileSync('taoiptv.m3u', convertToM3U(bodyText), 'utf8');
    console.log(`✅ 已生成 taoiptv.m3u`);

  } catch (error) {
    console.error('错误:', error.message);
    await page.screenshot({ path: 'error.png' }).catch(() => {});
    process.exit(1);
  } finally {
    await browser.close();
  }
}

function convertToM3U(txt) {
  let m3u = '#EXTM3U\n', group = '其他';
  for (const line of txt.split('\n')) {
    const t = line.trim();
    if (!t) continue;
    if (t.includes(',#genre#')) { group = t.replace(',#genre#', ''); continue; }
    const m = t.match(/^(.+?),(https?:\/\/.+)$/);
    if (m) m3u += `#EXTINF:-1 group-title="${group}",${m[1]}\n${m[2]}\n`;
  }
  return m3u;
}

main();
