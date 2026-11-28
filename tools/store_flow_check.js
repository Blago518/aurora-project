// 店舗アプリの入札の流れを、使う人として始めから終わりまで通す（tools/test_store_flow.py から呼ぶ）。
// 2026-11-22 オーナー「使ってみて、ってあと何回言わないといけない？」「反省したフリだけ」:
// その日に受けた指摘を、毎回自動で回る確かめに変えたもの。指摘を受けたら、ここに1つ足す。
// 使い方: node store_flow_check.js <データの json> <store/index.html>  → 結果を JSON で出す（problems が空なら合格）
const { chromium, devices } = require("playwright");
const [, , dataPath, htmlPath] = process.argv;
const problems = [];
const bad = (m) => problems.push(m);

// オーナーが「この言葉は使わない」と直した表示（画面に出たら不合格）
const BANNED = [
  ["購入しない", "2026-11-22「購入しない、じゃなくて、OKとかNOとか」"],
  ["買えない", "2026-11-21「買えない、ではなく」"],
  ["ボーダー", "2026-11-21「ボーダー？」"],
  ["良い評価", "2026-11-26「良い評価99%とかだとぱっと見で良く見えて紛らわしいので、良い、は外して」"],
  ["上限なし", "2026-11-24 おすすめの Contax NX。「いくらでも買える」と読める。判断は NO の1語"],
];

async function cardTexts(p) {
  return p.$$eval("#bidList .bid", (bs) => bs.map((x) => ({
    id: x.dataset.card,
    tags: [...x.querySelectorAll(".tag")].map((t) => t.textContent),
    badge: (x.querySelector(".max .yn") || {}).textContent || "",
    max: x.querySelector(".max").textContent.replace(/\s+/g, " "),
    snipeOff: !!x.querySelector(".links .mini.off"),
    links: [...x.querySelectorAll(".links a")].map((a) => [a.textContent, a.getAttribute("href")]),
  })));
}
function checkCards(cards, when) {
  for (const c of cards) {
    // 印と横の言葉は同じ向き（NO の横に「まで入札」を書かない。2026-11-22）
    if (c.badge === "NO" && /まで入札/.test(c.max)) bad(`${when} ${c.id}: NO なのに「まで入札」: ${c.max}`);
    if (c.badge === "NO" && !c.snipeOff && !c.tags.some((t) => /予約済み/.test(t))) bad(`${when} ${c.id}: NO なのに予約が押せる`);
    // 確かめる前の出品に OK を出さない（2026-11-22）
    if (c.badge === "OK" && c.tags.some((t) => /相場確認|同じ機種の Sold/.test(t))) bad(`${when} ${c.id}: 確かめる前なのに OK: ${c.tags}`);
    // 朝の価格を「現在」と書かない（2026-11-22）
    if (/現在/.test(c.max)) bad(`${when} ${c.id}: 朝の価格を「現在」と書いている: ${c.max}`);
    for (const [t, h] of c.links) {
      // 外のページは全部 Chrome で（2026-11-22「Safariではなく、Chromeで開いてぜんぶ」）
      if (/^phone/.test(when) && h && /^https:/.test(h)) bad(`${when} ${c.id}: ${t} が Chrome で開かない: ${h.slice(0, 40)}`);
      // eBay の検索語は機種の語だけ（2026-11-22 ヤフオクの商品名そのままで0件）
      if (/^(Sold|Now)$/.test(t)) {
        const q = decodeURIComponent((h.match(/_nkw=([^&]*)/) || [])[1] || "").replace(/\+/g, " ");
        if (!q || q.split(" ").length > 7 || /[^\x20-\x7Eαµ]/.test(q) || /#|\b\d{4,}\b/.test(q)) bad(`${when} ${c.id}: ${t} の検索語がおかしい: 「${q}」`);
      }
    }
  }
}

(async () => {
  const b = await chromium.launch();
  for (const [lab, opt] of [["phone", { ...devices["iPhone 13"] }], ["pc", { viewport: { width: 1280, height: 900 } }]]) {
    const ctx = await b.newContext({ ...opt, permissions: ["clipboard-read", "clipboard-write"] });
    const p = await ctx.newPage();
    p.setDefaultTimeout(8000);   // 画面が壊れていたら待たずに不合格にする
    p.on("pageerror", (e) => bad(`${lab}: 画面のエラー ${e.message}`));
    await ctx.route("https://script.google.com/**", (r) => r.abort());
    await p.goto("file://" + htmlPath);
    await p.click('nav [data-tab="set"]'); await p.setInputFiles("#dataFile", dataPath); await p.waitForTimeout(400);
    await p.click('nav [data-tab="bid"]'); await p.waitForTimeout(200);
    const chips = await p.$$eval("#bidChips button", (bs) => bs.map((x) => x.textContent));
    if (/予約 0/.test(chips[0])) bad(`${lab}: 予約に出品があるのに 0 と出る`);
    // 切り替えのボタンは短い名前で1行に収める（2026-11-26 オーナー「予約・確認不要・相場確認にしてなるべく1行に」）
    { const tops = await p.$$eval("#bidChips .chip", (cs) => cs.map((c) => [c.getBoundingClientRect().top, c.getBoundingClientRect().right, c.parentElement.getBoundingClientRect().right]));
      if (new Set(tops.map((t) => Math.round(t[0]))).size > 1 || tops.some((t) => t[1] > t[2] + 1)) bad(`${lab}: 入札タブの切り替えボタンが1行に収まらない`);
      const names = await p.$$eval("#bidChips .chip", (cs) => cs.map((c) => c.textContent.replace(/\s*\d+$/, "")).join("/"));
      if (names !== "予約/確認不要/相場確認") bad(`${lab}: 切り替えボタンの名前が違う: ${names}`); }
    // 金額の横の言葉: 入れ子のかっこ・個人の税の話・「この価格なら」を出さない（同日「文言のセンス、みやすい構成づくり」）
    { const maxes = await p.$$eval("#bidList .max .s", (ss) => ss.map((x) => x.textContent));
      for (const m of maxes) if (/（[^）]*（|個人|このまま入札|この価格なら/.test(m)) bad(`${lab}: 金額の横の言葉が長い・入れ子: ${m}`); }
    for (const f of ["reserve", "sure", "check"]) {
      const chip = await p.$(`#bidChips [data-f="${f}"]`); if (!chip) continue;
      await chip.click(); await p.waitForTimeout(150); checkCards(await cardTexts(p), `${lab} ${f}`);
    }
    // 管理番号を検索語に入れない・紛らわしい兄弟の機種を外す（2026-11-22 オーナーの2つの画面）
    await p.click('#bidChips [data-f="sure"]'); await p.waitForTimeout(150);
    const o1 = (await cardTexts(p)).find((c) => c.id === "o1"), oq = o1 && decodeURIComponent(((o1.links.find((l) => l[0] === "Sold") || [])[1] || "").match(/_nkw=([^&]*)/)?.[1] || "").replace(/\+/g, " ");
    // レンズ固定のカメラはメーカー＋型だけ（2026-11-22「OLYMPUS XA1 A9M D.ZUIKO 35mm F4」で絞れなかった）
    { await p.click('#bidChips [data-f="check"]'); await p.waitForTimeout(150);
      const x2 = (await cardTexts(p)).find((c) => c.id === "x2"), xq = x2 && decodeURIComponent(((x2.links.find((l) => l[0] === "Sold") || [])[1] || "").match(/_nkw=([^&]*)/)?.[1] || "").replace(/\+/g, " ");
      if (!x2 || !/^olympus xa1$/i.test(xq || "")) bad(`${lab}: XA1 の検索語がメーカー＋型だけでない: 「${xq}」`);
      await p.click('#bidChips [data-f="sure"]'); await p.waitForTimeout(150); }
    if (!o1) bad(`${lab}: o1 の出品が出ない`);
    else { if (/E66/i.test(oq)) bad(`${lab}: 検索語に管理番号 E66: 「${oq}」`); if (!/ -2\.8\b/.test(oq)) bad(`${lab}: F2.8 の別物を外す「-2.8」が無い: 「${oq}」`); }
    // 調べる → Sold を入れて登録 → 入札タブに戻り、その出品に ✓確認済みと出し直した額が出る
    await p.click('#bidChips [data-f="check"]'); await p.waitForTimeout(150);
    const before = (await cardTexts(p)).find((c) => c.id === "x1");
    await p.click('[data-blook="x1"]'); await p.waitForTimeout(250);
    // 仕入れの実績（2026-11-26 オーナー「どの出品者からどの商品をどの状態までなら、利益がでる仕入れができるか」→「実績を表示」）
    { const top = await p.$eval("#resTop", (e) => e.textContent);
      if (!/実績 売れた2件・1万円以上1件・平均利益7,000円/.test(top)) bad(`${lab}: 調べる画面に仕入れの実績が出ない: 「${top.slice(0, 120)}」`);
      if (process.env.STORE_SHOT) await p.screenshot({ path: process.env.STORE_SHOT + "_" + lab.replace(/\W/g, "") + ".png" }); }
    await p.fill("#soldUsd", "90"); await p.selectOption("#soldCond", "Near MINT"); await p.dispatchEvent("#soldCond", "change"); await p.waitForTimeout(200);
    await p.click("#soldReg"); await p.waitForTimeout(500);
    const tab = await p.$eval('nav [aria-selected="true"]', (x) => x.dataset.tab);
    if (tab !== "bid") bad(`${lab}: 登録しても入札タブに戻らない（${tab}）`);
    const after = await cardTexts(p), x1 = after.find((c) => c.id === "x1"), x2 = after.find((c) => c.id === "x2"), set = after.find((c) => c.id === "x3"), c2 = after.find((c) => c.id === "c2");
    if (!x1 || !x1.tags.some((t) => /✓ Sold \$90.*確認済み/.test(t))) bad(`${lab}: 登録した出品に ✓確認済みが出ない`);
    if (x1 && before && x1.max === before.max) bad(`${lab}: 登録しても上限額が変わらない: ${x1.max}`);
    if (x1 && x1.badge !== "NO") bad(`${lab}: Sold $90 で高すぎるのに NO が出ない: ${x1.max}`);
    if (!x2 || !x2.tags.some((t) => /同じ機種の Sold \$90/.test(t))) bad(`${lab}: 同じ機種のほかの出品に反映されない`);
    if (set && set.tags.some((t) => /Sold \$90/.test(t))) bad(`${lab}: セットの出品にボディの Sold が当たった`);
    checkCards(after, `${lab} 登録後`);
    // セットで登録した額は、ボディだけの出品に当てない
    await p.click('[data-blook="c1"]'); await p.waitForTimeout(250);
    await p.fill("#soldUsd", "300"); await p.dispatchEvent("#soldUsd", "input"); await p.waitForTimeout(150);
    await p.click("#soldReg"); await p.waitForTimeout(500);
    const c2b = (await cardTexts(p)).find((c) => c.id === "c2");
    if (c2b && c2b.tags.some((t) => /Sold \$300/.test(t))) bad(`${lab}: ボディだけの出品にセットの Sold が当たった`);
    // 開き直しても残る（保存済みのデータでの起動も兼ねる）
    await p.reload(); await p.waitForTimeout(500); await p.click('nav [data-tab="bid"]'); await p.click('#bidChips [data-f="check"]'); await p.waitForTimeout(200);
    const x1r = (await cardTexts(p)).find((c) => c.id === "x1");
    if (!x1r || !x1r.tags.some((t) => /確認済み/.test(t))) bad(`${lab}: 開き直すと確認済みが消える`);
    // ［確認済み］（2026-11-26 オーナー「スマホアプリの入札タブでも、確認済みボタンほしいね」）: 押すと印が付き、記録され、開き直しても残る。もう1回で外れる
    { await p.click('#bidChips [data-f="sure"]'); await p.waitForTimeout(150);
      await p.click('[data-seen="d1"]'); await p.waitForTimeout(200);
      if (!/確認済み/.test((await p.evaluate(() => localStorage.getItem("storeOutbox"))) || "")) bad(`${lab}: ［確認済み］が記録されない`);
      // 確認済みは一覧の上の1行にまとめてたたむ（2026-11-26 オーナー「確認済みは1行に折り畳んでほしい」「畳んだ1行は上に移動」）
      const fold = async () => p.$eval("[data-fold]", (b) => b.textContent).catch(() => "");
      if (await p.$('[data-card="d1"]')) bad(`${lab}: 確認済みにしてもカードが一覧に残る（1行にまとまらない）`);
      if (!/確認済み 1件/.test(await fold())) bad(`${lab}: 確認済みの1行が出ない: ${await fold()}`);
      { const first = await p.$eval("#bidList", (l) => l.children[0].matches("[data-fold]")); if (!first) bad(`${lab}: 確認済みの1行が一覧のいちばん上にない`); }
      await p.click("[data-fold]"); await p.waitForTimeout(150);
      if (!(await p.$('[data-card="d1"]'))) bad(`${lab}: 確認済みの1行を開いてもカードが出ない`);
      { const on = await p.$eval('[data-seen="d1"]', (b) => b.getAttribute("aria-pressed") + b.textContent).catch(() => "（カードが無い）");
        if (!/^true✓ 確認済み/.test(on)) bad(`${lab}: ［確認済み］を押しても印が付かない: ${on}`); }
      { const op = await p.$eval('[data-card="d1"]', (c) => +getComputedStyle(c).opacity); if (!(op < 0.8)) bad(`${lab}: 確認済みにしてもカードが薄くならない（opacity ${op}）`); }
      // 確認済みのカードは1行にたたむ（2026-11-26 オーナー「確認済みは閉じれるようにしてほしい」）。［開く］で中身が出て、［閉じる］でまたたたむ
      { const vis = async () => p.$eval('[data-card="d1"] .max', (m) => getComputedStyle(m).display !== "none");
        if (await vis()) bad(`${lab}: 確認済みにしてもカードがたたまれない`);
        await p.click('[data-open="d1"]'); await p.waitForTimeout(150);
        if (!(await vis())) bad(`${lab}: ［開く］を押しても確認済みのカードの中身が出ない`);
        await p.click('[data-open="d1"]'); await p.waitForTimeout(150);
        if (await vis()) bad(`${lab}: ［閉じる］を押してもたたまれない`); }
      if (process.env.STORE_SHOT) await p.screenshot({ path: process.env.STORE_SHOT + "_seen_" + lab.replace(/\W/g, "") + ".png" });
      await p.click("[data-fold]"); await p.waitForTimeout(150);
      if (await p.$('[data-card="d1"]')) bad(`${lab}: 確認済みの1行を［たたむ］で閉じられない`);
      await p.reload(); await p.waitForTimeout(500); await p.click('nav [data-tab="bid"]'); await p.click('#bidChips [data-f="sure"]'); await p.waitForTimeout(200);
      if (!/確認済み 1件/.test(await fold())) bad(`${lab}: 開き直すと確認済みの1行が消える`);
      await p.click("[data-fold]"); await p.waitForTimeout(150);
      if ((await p.$eval('[data-seen="d1"]', (b) => b.getAttribute("aria-pressed"))) !== "true") bad(`${lab}: 開き直すと確認済みが消える（入札タブのボタン）`);
      await p.click('[data-seen="d1"]'); await p.waitForTimeout(200);
      if ((await p.$eval('[data-seen="d1"]', (b) => b.getAttribute("aria-pressed"))) !== "false") bad(`${lab}: もう1回押しても確認済みが外れない`);
      if (!(await p.$eval('[data-card="d1"] .max', (m) => getComputedStyle(m).display !== "none"))) bad(`${lab}: 確認済みを外してもカードがたたまれたまま`);
      { const op = await p.$eval('[data-card="d1"]', (c) => +getComputedStyle(c).opacity); if (op !== 1) bad(`${lab}: 確認済みを外してもカードが薄いまま（opacity ${op}）`); }
      if (!/確認済み取消/.test((await p.evaluate(() => localStorage.getItem("storeOutbox"))) || "")) bad(`${lab}: 確認済みを外したことが記録されない`); }
    // 予約できる出品: 帯に上限額 →［予約した］で記録
    await p.click('#bidChips [data-f="sure"]'); await p.waitForTimeout(150);
    await p.evaluate(() => { const a = document.querySelector('[data-snipe="d1"]'); if (a) { a.removeAttribute("target"); a.addEventListener("click", (e) => e.preventDefault()); } });
    await p.click('[data-snipe="d1"]'); await p.waitForTimeout(250);
    if (!/入札欄に入れる額/.test(await p.textContent("#bidBar"))) bad(`${lab}: 予約の帯が出ない`);
    await p.click('[data-bbar="done"]'); await p.waitForTimeout(250);
    if (!/入札予約/.test((await p.evaluate(() => localStorage.getItem("storeOutbox"))) || "")) bad(`${lab}: ［予約した］が記録されない`);
    // おすすめ → 調べる → Sold を登録 → おすすめに戻り、金額が出し直される（2026-11-22 オーナー「おすすめの金額に反映されない」）
    await p.click('nav [data-tab="rec"]'); await p.waitForTimeout(150);
    await p.evaluate(() => { recSel.brand = "Canon"; recSel.kind = "film"; renderBrands(); renderRecs(); });
    const recBuy = async () => (await p.$eval('#recList [data-rec="Canon AE-1"] .buy', (x) => x.textContent.replace(/\s+/g, " ")).catch(() => ""));
    const rb0 = await recBuy();
    if (!rb0) bad(`${lab}: おすすめに Canon AE-1 が出ない`);
    else {
      await p.click('#recList [data-rec="Canon AE-1"] [data-look]'); await p.waitForTimeout(250);
      await p.fill("#soldUsd", "75"); await p.dispatchEvent("#soldUsd", "input"); await p.waitForTimeout(150);
      await p.click("#soldReg"); await p.waitForTimeout(500);
      const t2 = await p.$eval('nav [aria-selected="true"]', (x) => x.dataset.tab);
      if (t2 !== "rec") bad(`${lab}: おすすめから登録しても、おすすめに戻らない（${t2}）`);
      const rb1 = await recBuy();
      if (rb1 === rb0 || !/Sold \$75/.test(rb1)) bad(`${lab}: おすすめの金額に登録した Sold が反映されない: ${rb0} → ${rb1}`);
    }
    // おすすめ: 全部の種類×全部のメーカーで、枠と違う物を出さない（2026-11-22「フィルムカメラで何故レンズが出る？」、2026-11-23 D4s・F値なしのレンズ）。
    // シリーズは一眼レフが先・8mm は最後、同じシリーズは型の名前順（F2 の下に F3。2026-11-23）
    const recChk = await p.evaluate(() => {
      // 上限が千円未満の機種は出さない（2026-11-28「税込30円以下なら買い」）
      const any = DATA.recs.find((r) => r.brand === "Canon") || DATA.recs[0];
      DATA.recs.push({ ...any, label: "Canon FD 50mm F1.4 安すぎ", maxBuy: 30, kind: any.kind });
      const out = [];
      for (const kind of Object.keys(REC_KINDS)) {
        recSel.kind = kind; recSel.high = false; renderBrands();
        for (const b of [...document.querySelectorAll("#brandChips [data-brand]")].map((x) => x.dataset.brand)) {
          recSel.brand = b; renderBrands(); renderRecs();
          const groups = [...document.querySelectorAll("#recList h3.grp")].map((h) => h.firstChild.textContent.trim());
          const labels = [...document.querySelectorAll("#recList [data-rec]")].map((x) => x.dataset.rec);
          out.push({ kind, b, groups, labels, wants: labels.map((l) => (DATA.recs.find((r) => r.label === l) || {})._want),
                     text: document.querySelector("#recList").innerText });
        }
      }
      return out;
    });
    for (const x of recChk) {
      if (x.labels.includes("Canon FD 50mm F1.4 安すぎ")) bad(`${lab}: おすすめ ${x.kind}/${x.b} に上限30円の機種が出る`);
      for (const m of x.text.matchAll(/税込\s*([\d,]+)円\s*以下なら買い/g)) if (+m[1].replace(/,/g, "") < 1000) bad(`${lab}: おすすめ ${x.kind}/${x.b} に「税込${m[1]}円以下なら買い」`);
      if (x.text.includes("過去の利益 平均")) bad(`${lab}: 過去の利益は中央値なのに「平均」と出る`);
      x.labels.forEach((l, i) => { if (x.wants[i] && x.wants[i] !== x.kind) bad(`${lab}: おすすめ ${x.kind}/${x.b} に別の枠の「${l}」（${x.wants[i]}）`); });
      for (const [w, why] of BANNED) if (x.text.includes(w)) bad(`${lab}: おすすめ ${x.kind}/${x.b} に使わない言葉「${w}」（${why}）`);   // 全部の種類×メーカー×「すべて」の値段で見る
      const gi = (g) => x.groups.indexOf(g);
      if (gi("8mm・シネ") >= 0 && gi("8mm・シネ") !== x.groups.length - 1) bad(`${lab}: おすすめ ${x.kind}/${x.b} で 8mm・シネ が最後でない: ${x.groups}`);
      if ((x.kind === "film" || x.kind === "digital") && x.groups.some((g) => /レンズ|テレコン/.test(g))) bad(`${lab}: おすすめ ${x.kind}/${x.b} にレンズの見出し: ${x.groups}`);
      for (const slr of ["フィルム一眼レフ", "デジタル一眼レフ"]) if (gi(slr) > 0) bad(`${lab}: おすすめ ${x.kind}/${x.b} で ${slr} が先頭でない: ${x.groups}`);
      if (x.kind === "film" && x.b === "Nikon") {
        const order = ["Nikon EyeLevel silver", "Nikon F2", "Nikon F2 50mm F1.4", "Nikon F2 55mm", "Nikon F3", "Nikon F4", "Nikon FM2", "Nikon New FM2"];
        const pos = order.map((l) => x.labels.indexOf(l));
        if (pos.some((v, i) => v < 0 || (i && v !== pos[i - 1] + 1))) bad(`${lab}: Nikon のフィルム一眼レフが型の順に並ばない: ${x.labels}`);
      }
    }
    await p.evaluate(() => { recSel.kind = "film"; recSel.high = true; recSel.brand = "Canon"; renderBrands(); renderRecs(); });
    // 棚の読み取り: 見出しを押すとたたむ・もう1回で開く・新しく撮ったら開く（2026-11-28「読取を折り畳みたい」）
    { const fold = await p.evaluate(() => {
        const mk = (n, pr) => ({ name: n, price: pr, notes: "", r: lookup(n, pr, soldOpt(n, { note: "" })) });
        shelfFold = false; shelfRows = [mk("Canon AE-1", 5000), mk("Canon A-1", 8000), mk("Nikon F3", 20000)]; renderShelf();
        const n0 = document.querySelectorAll("#shelf .shelf").length;
        document.querySelector("#shelf [data-fold-shelf]").click();
        const n1 = document.querySelectorAll("#shelf .shelf").length, h1 = !!document.querySelector("#shelf [data-fold-shelf]"), keep = localStorage.getItem("storeShelfFold");
        document.querySelector("#shelf [data-fold-shelf]").click();
        const n2 = document.querySelectorAll("#shelf .shelf").length;
        shelfRows = []; renderShelf();
        return { n0, n1, h1, n2, keep };
      });
      if (fold.n0 !== 3 || fold.n1 !== 0 || !fold.h1 || fold.n2 !== 3 || fold.keep !== "true") bad(`${lab}: 読み取りの一覧がたためない・開けない: ${JSON.stringify(fold)}`); }
    // 横にはみ出さない・使わない言葉が無い（全部のタブ）
    for (const t of ["look", "rec", "sweet", "calc", "bid", "shop", "set"]) {
      await p.click(`nav [data-tab="${t}"]`); await p.waitForTimeout(120);
      const views = t === "bid" ? ["reserve", "sure", "check"] : [""];   // 入札タブは全部の絞り込みを見る（見えていない枠を見逃さない）
      for (const f of views) {
        if (f) { const chip = await p.$(`#bidChips [data-f="${f}"]`); if (!chip) continue; await chip.click(); await p.waitForTimeout(120); }
        const [sw, cw, text] = await p.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.innerText]);
        if (sw > cw + 1) bad(`${lab} ${t}${f}: 横にはみ出す（${sw}>${cw}）`);
        for (const [w, why] of BANNED) if (text.includes(w)) bad(`${lab} ${t}${f}: 使わない言葉「${w}」が出ている（${why}）`);
      }
    }
    await ctx.close();
  }
  await b.close();
  console.log(JSON.stringify({ problems }));
})().catch((e) => { console.log(JSON.stringify({ problems: problems.concat(["止まった: " + e.message.split("\n")[0]]) })); process.exit(0); });   // ブラウザが残っても待たずに終わる
