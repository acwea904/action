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
    await page.goto(config.SEARCH_URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

    // 直接从HTML提取token
    const html = await page.content();
    const tokenMatch = html.match(/data-clipboard-text="([a-f0-9]{16})"/);
    if (!tokenMatch) {
      console.log('❌ 未找到Token');
      fs.writeFileSync('error.html', html);
      process.exit(1);
    }
    const token = tokenMatch[1];
    console.log(`Token: ${token}`);

    // 从搜索结果提取TXT ID
    const idMatch = html.match(/lives\/(\d+)\.txt/);
    const txtId = idMatch ? idMatch[1] : '44023';
    console.log(`TXT ID: ${txtId}`);

    // 获取TXT
    const txtUrl = `https://taoiptv.com/lives/${txtId}.txt?token=${token}`;
    console.log(`获取: ${txtUrl}`);
    const response = await page.goto(txtUrl, { timeout: 30000 });
    const bodyText = await response.text();

    if (!bodyText || bodyText.length < 100) {
      console.log('❌ TXT内容异常');
      console.log(bodyText.substring(0, 500));
      process.exit(1);
    }

    fs.writeFileSync('taoiptv.m3u', convertToM3U(bodyText), 'utf8');
    console.log(`✅ 已生成 taoiptv.m3u (${bodyText.split('\n').length} 行)`);

  } catch (error) {
    console.error('错误:', error.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

function convertToM3U(txt) {
  let m3u = '#EXTM3U\n';
  let group = '其他';
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
