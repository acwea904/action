const { chromium } = require('playwright');
const fs = require('fs');

const config = JSON.parse(process.env.TV_ACCOUNTS || '{}');
const STATE_FILE = 'taoiptv-state.json';

async function main() {
  const browser = await chromium.launch({
    proxy: config.HY2_URL ? { server: 'socks5://127.0.0.1:1080' } : undefined
  });

  let context;
  if (fs.existsSync(STATE_FILE)) {
    console.log('恢复浏览器状态...');
    context = await browser.newContext({ storageState: STATE_FILE });
  } else if (config.CF_CLEARANCE) {
    console.log('使用初始 CF_CLEARANCE...');
    context = await browser.newContext();
    await context.addCookies([{
      name: 'cf_clearance',
      value: config.CF_CLEARANCE,
      domain: '.taoiptv.com',
      path: '/'
    }]);
  } else {
    context = await browser.newContext();
  }

  const page = await context.newPage();

  try {
    console.log('访问搜索页面...');
    await page.goto(config.SEARCH_URL, { waitUntil: 'networkidle', timeout: 60000 });
    await page.waitForSelector('#copyToken', { timeout: 30000 });
    
    const token = await page.$eval('#copyToken', el => el.getAttribute('data-clipboard-text'));
    console.log(`Token: ${token}`);

    const postData = await page.$eval('#post-23440', el => {
      const dateText = el.querySelector('.entry-date')?.textContent || '';
      const dateMatch = dateText.match(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/);
      return { date: dateMatch ? dateMatch[0] : '' };
    });
    console.log(`更新日期: ${postData.date}`);

    const txtUrl = `https://taoiptv.com/lives/44023.txt?token=${token}`;
    const txtPage = await context.newPage();
    await txtPage.goto(txtUrl, { waitUntil: 'networkidle', timeout: 30000 });
    const bodyText = await txtPage.$eval('body', el => el.innerText);
    await txtPage.close();

    fs.writeFileSync('taoiptv.m3u', convertToM3U(bodyText), 'utf8');
    console.log('已生成 taoiptv.m3u');

    fs.writeFileSync('state-info.json', JSON.stringify({ token, date: postData.date, lastRun: new Date().toISOString() }, null, 2));
    await context.storageState({ path: STATE_FILE });
    console.log('浏览器状态已保存');

  } catch (error) {
    console.error('错误:', error.message);
    process.exit(1);
  } finally {
    await browser.close();
  }
}

function convertToM3U(txt) {
  const lines = txt.trim().split('\n');
  let m3u = '#EXTM3U\n';
  let currentGroup = '其他';
  const groupMap = { '央视频道': '央视', '卫视频道': '卫视', '地方频道': '地方', '港澳台': '港澳台', '体育频道': '体育', '电影频道': '电影', '少儿频道': '少儿', '更新时间': '其他' };

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (trimmed.includes(',#genre#')) {
      currentGroup = groupMap[trimmed.replace(',#genre#', '').trim()] || trimmed.replace(',#genre#', '').trim();
      continue;
    }
    const match = trimmed.match(/^(.+?),(https?:\/\/.+)$/);
    if (match) {
      const [, name, url] = match;
      m3u += `#EXTINF:-1 tvg-id="${name}" tvg-name="${name}" tvg-logo="https://epg.112114.xyz/logo/${encodeURIComponent(name)}.png" group-title="${currentGroup}",${name}\n${url}\n`;
    }
  }
  return m3u;
}

main();
