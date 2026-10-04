const { chromium } = require('playwright');
const path = require('path');

const FILE = 'file:///' + path.resolve(__dirname, 'visual-directions.html').replace(/\\/g, '/');
const SKINS = ['frost', 'cellar', 'abyss', 'paper'];

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()); });

  await page.goto(FILE);
  await page.waitForTimeout(600);

  // 等待自动演示流转完成
  await page.waitForTimeout(4200);

  const report = {};

  for (const sk of SKINS) {
    await page.click(`[data-skin-btn="${sk}"]`);
    await page.waitForTimeout(450);

    const r = await page.evaluate((sk) => {
      const root = document.querySelector(`.skin[data-skin="${sk}"]`);
      const cs = getComputedStyle(root);
      const win = document.getElementById('win');
      const bar = document.getElementById('winBar');
      const barCs = getComputedStyle(bar);
      const bodyCs = getComputedStyle(document.body);
      const q = s => root.querySelector(s);

      // 溢出检测
      const overflow = [];
      root.querySelectorAll('*').forEach(el => {
        if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflowX === 'visible') {
          overflow.push((el.className || el.tagName) + ' sw=' + el.scrollWidth + ' cw=' + el.clientWidth);
        }
      });

      // 画布是否真的画了东西
      let canvasInk = null;
      root.querySelectorAll('canvas').forEach(c => {
        const ctx = c.getContext('2d');
        if (!c.width) { canvasInk = 'zero-size'; return; }
        const d = ctx.getImageData(0, 0, Math.min(c.width, 400), Math.min(c.height, 200)).data;
        let sum = 0;
        for (let i = 3; i < d.length; i += 4) sum += d[i];
        canvasInk = 'alpha sum=' + sum;
      });

      return {
        visible: !root.hidden,
        skinAttr: document.documentElement.getAttribute('data-skin'),
        bg: cs.backgroundColor + ' / ' + cs.backgroundImage.slice(0, 46),
        winBg: getComputedStyle(win).backgroundColor,
        chromeVsContent: barCs.backgroundColor + '  vs  ' + cs.backgroundColor,
        ink: cs.color,
        // 空态/完成态
        fileShown: getComputedStyle(q('[data-filebar]')).display,
        emptyShown: getComputedStyle(q('.drop__empty')).display,
        resOn: q('[data-res]').classList.contains('is-on'),
        resName: q('[data-res-name]').textContent,
        notes: q('[data-res-notes]').textContent,
        tracks: q('[data-res-tracks]').textContent,
        dur: q('[data-res-dur]').textContent,
        barWidth: q('[data-bar]').style.width,
        pc: q('[data-pc]').textContent,
        now: q('[data-now]').textContent,
        stageNow: root.querySelector('.stg__i.is-now .stg__t')?.textContent || null,
        stagesDone: root.querySelectorAll('.stg__i.is-done').length,
        paramsRendered: q('[data-params]').children.length,
        advRendered: q('[data-adv-panel]').children.length,
        selOptions: q('[data-sel-list]').children.length,
        selLabel: q('[data-sel-label]').textContent,
        icons: root.querySelectorAll('svg.ic').length,
        startDisabled: q('[data-start]').disabled,
        overflow: overflow.slice(0, 6),
        canvasInk
      };
    }, sk);
    report[sk] = r;
  }

  console.log('===== 四方向运行时状态 =====');
  for (const sk of SKINS) console.log('\n--- ' + sk + ' ---\n' + JSON.stringify(report[sk], null, 1));

  // ===== 交互测试：模式联动 =====
  console.log('\n===== 模式切换联动（paper） =====');
  await page.click('[data-skin-btn="paper"]');
  await page.waitForTimeout(350);
  for (const m of ['accomp', 'melody', 'basic', 'auto']) {
    await page.click('.skin[data-skin="paper"] [data-sel] .sel__trg');
    await page.waitForTimeout(200);
    await page.click(`.skin[data-skin="paper"] [data-sel-list] [data-mode="${m}"]`);
    await page.waitForTimeout(280);
    const r = await page.evaluate(() => {
      const root = document.querySelector('.skin[data-skin="paper"]');
      return {
        label: root.querySelector('[data-sel-label]').textContent,
        params: [...root.querySelectorAll('[data-params] .pp-tl b')].map(x => x.textContent),
        rows: root.querySelectorAll('[data-params] .pp-trow').length,
        tab: root.querySelector('.pp-mode[aria-selected="true"]').textContent,
        note: root.querySelector('[data-sel-note]').textContent,
        meta: root.querySelector('[data-pp-mode]').textContent
      };
    });
    console.log('  ' + m + ' → ' + JSON.stringify(r));
  }

  // ===== 高级参数折叠 =====
  console.log('\n===== 高级参数折叠（4 方向） =====');
  for (const sk of SKINS) {
    await page.click(`[data-skin-btn="${sk}"]`);
    await page.waitForTimeout(300);
    const before = await page.evaluate(s => {
      const p = document.querySelector(`.skin[data-skin="${s}"] [data-adv-panel]`);
      return p.getBoundingClientRect().height;
    }, sk);
    await page.click(`.skin[data-skin="${sk}"] [data-adv-toggle]`);
    await page.waitForTimeout(420);
    const after = await page.evaluate(s => {
      const root = document.querySelector(`.skin[data-skin="${s}"]`);
      const p = root.querySelector('[data-adv-panel]');
      return {
        h: p.getBoundingClientRect().height,
        open: root.querySelector('[data-adv]').classList.contains('is-open'),
        aria: root.querySelector('[data-adv-toggle]').getAttribute('aria-expanded'),
        rows: p.querySelectorAll('input,.tog').length
      };
    }, sk);
    console.log('  ' + sk.padEnd(7) + ' 折叠高 ' + before.toFixed(0) + 'px → 展开高 ' + after.h.toFixed(0) + 'px  open=' + after.open + ' aria=' + after.aria + ' 控件数=' + after.rows);
  }

  // ===== 重置 → 空态 =====
  console.log('\n===== 重置回空态 =====');
  for (const sk of SKINS) {
    await page.click(`[data-skin-btn="${sk}"]`);
    await page.waitForTimeout(280);
    await page.click(`.skin[data-skin="${sk}"] [data-reset]`);
    await page.waitForTimeout(300);
    const r = await page.evaluate(s => {
      const root = document.querySelector(`.skin[data-skin="${s}"]`);
      return {
        empty: getComputedStyle(root.querySelector('.drop__empty')).display,
        file: getComputedStyle(root.querySelector('[data-filebar]')).display,
        res: root.querySelector('[data-res]').classList.contains('is-on'),
        startDisabled: root.querySelector('[data-start]').disabled,
        dropH: Math.round(root.querySelector('[data-drop]').getBoundingClientRect().height)
      };
    }, sk);
    console.log('  ' + sk.padEnd(7) + ' ' + JSON.stringify(r));
  }

  // ===== 完整流程：点击拖放 → 开始 → 完成 =====
  console.log('\n===== 完整状态流转（逐方向，真实点击） =====');
  for (const sk of SKINS) {
    await page.click(`[data-skin-btn="${sk}"]`);
    await page.waitForTimeout(300);
    await page.click(`.skin[data-skin="${sk}"] [data-drop]`);
    await page.waitForTimeout(250);
    const has = await page.evaluate(s => getComputedStyle(document.querySelector(`.skin[data-skin="${s}"] [data-filebar]`)).display, sk);
    await page.click(`.skin[data-skin="${sk}"] [data-start]`);
    const seen = [];
    for (let i = 0; i < 24; i++) {
      await page.waitForTimeout(190);
      const st = await page.evaluate(s => {
        const root = document.querySelector(`.skin[data-skin="${s}"]`);
        return {
          now: root.querySelector('[data-now]').textContent.slice(0, 34),
          pct: root.querySelector('[data-pc]').textContent,
          w: root.querySelector('[data-bar]').style.width,
          res: root.querySelector('[data-res]').classList.contains('is-on')
        };
      }, sk);
      const key = st.now;
      if (!seen.length || seen[seen.length - 1].key !== key) seen.push({ key, ...st });
      if (st.res) break;
    }
    console.log('  ' + sk.padEnd(7) + ' filebar=' + has);
    seen.forEach(s => console.log('      ' + String(s.pct).padStart(5) + ' ' + String(s.w).padStart(7) + '  ' + s.key));
    const fin = await page.evaluate(s => {
      const root = document.querySelector(`.skin[data-skin="${s}"]`);
      return {
        res: root.querySelector('[data-res]').classList.contains('is-on'),
        name: root.querySelector('[data-res-name]').textContent,
        notes: root.querySelector('[data-res-notes]').textContent,
        tracks: root.querySelector('[data-res-tracks]').textContent,
        openBtn: !!root.querySelector('[data-open]')
      };
    }, sk);
    console.log('      完成 → ' + JSON.stringify(fin));
  }

  // ===== 响应式 =====
  console.log('\n===== 响应式不塌（1440/1280/1024/860） =====');
  for (const w of [1440, 1280, 1024, 860]) {
    await page.setViewportSize({ width: w, height: 900 });
    await page.waitForTimeout(360);
    const r = await page.evaluate(() => {
      const bad = [];
      document.querySelectorAll('.skin:not([hidden]) *').forEach(el => {
        const cs = getComputedStyle(el);
        if (cs.overflowX === 'visible' && el.scrollWidth > el.clientWidth + 3) {
          bad.push((typeof el.className === 'string' ? el.className : el.tagName).slice(0, 34) + '|' + el.scrollWidth + '>' + el.clientWidth);
        }
      });
      return { docOverflow: document.documentElement.scrollWidth > window.innerWidth + 1, bad: bad.slice(0, 5) };
    });
    console.log('  ' + w + 'px  文档横向溢出=' + r.docOverflow + '  元素溢出=' + (r.bad.length ? JSON.stringify(r.bad) : '无'));
  }

  console.log('\n===== JS 运行时错误 =====');
  console.log(errors.length ? errors.join('\n') : '  无（通过）');

  await browser.close();
})();
