const { chromium } = require('playwright');
const path = require('path');
const FILE = 'file:///' + path.resolve(__dirname, 'visual-directions.html').replace(/\\/g, '/');
const SKINS = ['frost', 'cellar', 'abyss', 'paper'];

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  await p.goto(FILE);
  await p.waitForTimeout(900);

  console.log('===== 高级参数折叠：真实高度变化 =====');
  for (const sk of SKINS) {
    await p.click(`[data-skin-btn="${sk}"]`);
    await p.waitForTimeout(320);
    const closed = await p.evaluate(s => {
      const a = document.querySelector(`.skin[data-skin="${s}"] [data-adv]`);
      const clip = a.querySelector('.adv__clip');
      const panel = a.querySelector('[data-adv-panel]');
      return {
        wrapRows: getComputedStyle(a.querySelector('.adv__wrap')).gridTemplateRows,
        clipH: Math.round(clip.getBoundingClientRect().height),
        panelH: Math.round(panel.getBoundingClientRect().height),
        panelVisible: panel.getBoundingClientRect().height > 0
      };
    }, sk);
    await p.click(`.skin[data-skin="${sk}"] [data-adv-toggle]`);
    await p.waitForTimeout(500);
    const open = await p.evaluate(s => {
      const a = document.querySelector(`.skin[data-skin="${s}"] [data-adv]`);
      const clip = a.querySelector('.adv__clip');
      const panel = a.querySelector('[data-adv-panel]');
      const first = panel.querySelector('input,.tog');
      return {
        wrapRows: getComputedStyle(a.querySelector('.adv__wrap')).gridTemplateRows,
        clipH: Math.round(clip.getBoundingClientRect().height),
        panelH: Math.round(panel.getBoundingClientRect().height),
        firstCtlH: first ? Math.round(first.getBoundingClientRect().height) : null,
        firstCtlVisible: first ? first.getBoundingClientRect().height > 4 : false
      };
    }, sk);
    console.log(`  ${sk.padEnd(7)} 折叠[rows=${closed.wrapRows} clip=${closed.clipH}px 面板=${closed.panelH}px] → 展开[rows=${open.wrapRows} clip=${open.clipH}px 面板=${open.panelH}px 首个控件高=${open.firstCtlH}px 可见=${open.firstCtlVisible}]`);
  }

  console.log('\n===== tooltip 溢出诊断 =====');
  const tip = await p.evaluate(() => {
    const btn = document.querySelector('.skin:not([hidden]) .btn--ico.tip');
    const cs = getComputedStyle(btn);
    return {
      cls: btn.className,
      scrollW: btn.scrollWidth, clientW: btn.clientWidth,
      overflowX: cs.overflowX, position: cs.position, display: cs.display,
      childHTML: btn.innerHTML.slice(0, 60),
      svgW: btn.querySelector('svg') ? btn.querySelector('svg').getBoundingClientRect().width : null,
      inBody: document.body.scrollWidth > window.innerWidth
    };
  });
  console.log('  ' + JSON.stringify(tip));

  console.log('\n===== 画布实际绘制内容 =====');
  for (const sk of ['cellar', 'abyss']) {
    await p.click(`[data-skin-btn="${sk}"]`);
    await p.waitForTimeout(700);
    const r = await p.evaluate(s => {
      const out = [];
      document.querySelectorAll(`.skin[data-skin="${s}"] canvas`).forEach(c => {
        const ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let a = 0, maxA = 0;
        for (let i = 3; i < d.length; i += 4) { a += d[i]; if (d[i] > maxA) maxA = d[i]; }
        out.push({ w: c.width, h: c.height, cssW: c.clientWidth, cssH: c.clientHeight, alphaSum: a, maxAlpha: maxA });
      });
      return out;
    }, sk);
    console.log('  ' + sk + ': ' + JSON.stringify(r));
  }

  console.log('\n===== 换肤过渡（skin属性 + 背景色变化） =====');
  for (const sk of SKINS) {
    await p.click(`[data-skin-btn="${sk}"]`);
    await p.waitForTimeout(120);
    const mid = await p.evaluate(() => ({
      skinning: document.body.classList.contains('skinning'),
      attr: document.documentElement.getAttribute('data-skin')
    }));
    await p.waitForTimeout(400);
    const done = await p.evaluate(() => ({
      skinning: document.body.classList.contains('skinning'),
      visibleCount: [...document.querySelectorAll('.skin')].filter(s => !s.hidden).length,
      visibleName: [...document.querySelectorAll('.skin')].filter(s => !s.hidden).map(s => s.dataset.skin)[0]
    }));
    console.log(`  →${sk.padEnd(7)} 切换中skinning=${mid.skinning} 稳定后可见数=${done.visibleCount} (${done.visibleName}) 残留skinning=${done.skinning}`);
  }

  console.log('\n===== 文本截断/溢出复查（可见皮肤内所有元素） =====');
  for (const sk of SKINS) {
    await p.click(`[data-skin-btn="${sk}"]`);
    await p.waitForTimeout(350);
    const bad = await p.evaluate(s => {
      const out = [];
      document.querySelectorAll(`.skin[data-skin="${s}"] *`).forEach(el => {
        const cs = getComputedStyle(el);
        if (cs.overflowX !== 'visible') return;
        if (el.classList.contains('tip')) return;
        if (el.scrollWidth > el.clientWidth + 2) out.push((el.className || el.tagName) + ' ' + el.scrollWidth + '>' + el.clientWidth);
      });
      return out.slice(0, 8);
    }, sk);
    console.log('  ' + sk.padEnd(7) + (bad.length ? JSON.stringify(bad) : '无溢出'));
  }

  console.log('\n===== 空态结果卡必须隐藏 =====');
  for (const sk of SKINS) {
    await p.click(`[data-skin-btn="${sk}"]`);
    await p.waitForTimeout(250);
    await p.click(`.skin[data-skin="${sk}"] [data-reset]`);
    await p.waitForTimeout(420);
    const empty = await p.evaluate(s => {
      const r = document.querySelector(`.skin[data-skin="${s}"] [data-res]`);
      return { h: Math.round(r.getBoundingClientRect().height), disp: getComputedStyle(r).display };
    }, sk);
    await p.click(`.skin[data-skin="${sk}"] [data-drop]`);
    await p.waitForTimeout(200);
    await p.click(`.skin[data-skin="${sk}"] [data-start]`);
    await p.waitForTimeout(4400);
    const done = await p.evaluate(s => {
      const r = document.querySelector(`.skin[data-skin="${s}"] [data-res]`);
      return { h: Math.round(r.getBoundingClientRect().height), disp: getComputedStyle(r).display };
    }, sk);
    console.log(`  ${sk.padEnd(7)} 空态 h=${empty.h} (${empty.disp})  →  完成态 h=${done.h} (${done.disp})  ${empty.h === 0 && done.h > 20 ? 'PASS' : 'FAIL'}`);
  }

  await b.close();
})();
