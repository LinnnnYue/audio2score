const { chromium } = require('playwright');
const path = require('path');
const FILE = 'file:///' + path.resolve(__dirname, 'visual-directions.html').replace(/\\/g, '/');
const SKINS = ['frost', 'cellar', 'abyss', 'paper'];

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  await p.goto(FILE);

  for (const sk of SKINS) {
    // 空态
    await p.waitForTimeout(300);
    await p.click(`[data-skin-btn="${sk}"]`);
    await p.waitForTimeout(200);
    await p.click(`.skin[data-skin="${sk}"] [data-reset]`);
    await p.waitForTimeout(700);
    await p.screenshot({ path: `_shot_${sk}_empty.png` });

    // 下拉展开
    await p.click(`.skin[data-skin="${sk}"] [data-sel] .sel__trg`);
    await p.waitForTimeout(450);
    await p.screenshot({ path: `_shot_${sk}_dropdown.png` });
    await p.keyboard.press('Escape');
    await p.waitForTimeout(300);

    // 高级参数展开
    await p.click(`.skin[data-skin="${sk}"] [data-adv-toggle]`);
    await p.waitForTimeout(500);
    await p.screenshot({ path: `_shot_${sk}_adv.png` });
    await p.click(`.skin[data-skin="${sk}"] [data-adv-toggle]`);
    await p.waitForTimeout(400);

    // 进行中
    await p.click(`.skin[data-skin="${sk}"] [data-drop]`);
    await p.waitForTimeout(250);
    await p.click(`.skin[data-skin="${sk}"] [data-start]`);
    await p.waitForTimeout(1500);
    await p.screenshot({ path: `_shot_${sk}_running.png` });

    // 完成态
    await p.waitForTimeout(3200);
    await p.screenshot({ path: `_shot_${sk}_done.png` });
    console.log('shot ' + sk);
  }
  await b.close();
})();
