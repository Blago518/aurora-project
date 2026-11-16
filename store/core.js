// 自動生成ファイル: tools/build_store.py が tools/extension_template.js の @core 部分をコピーしたもの。
// 直接編集しない（拡張機能と判定を一致させるため）。データは入っていない。
function makeCore(DATA) {
  "use strict";
  const C = DATA.consts;
  // ------------------------------------------------------------------ 文字列処理（appraise.py と同じ規則）
  const BRAND_SET = new Set(Object.values(C.brandsEn).map((b) => b.toLowerCase()));
  const NOISE = new Set(C.noise), THIRD = new Set(C.thirdParty), COLORS = new Set(C.colors);
  const VNORM = C.variantNormalize.map(([p, r]) => [new RegExp(p, "gi"), r]);
  const GENS = new Set(C.generations), PREMIUM = new Set(C.premium);
  const ACCESSORY_RE = new RegExp(C.accessoryRe, "i");
  const FLAG_RES = C.flagRules.map(([name, pat]) => [name, new RegExp(pat, "i")]);
  const KANTEI = DATA.kantei.map((r) => ({ ...r, re: r.patterns.map((p) => { try { return new RegExp(p, "i"); } catch (e) { return null; } }) }));

  // 出品者の管理番号（Tj036・Ft644461・NA77401・IT120751・OL4709・R09010 など）。appraise.py の STOCK_CODE と同じ
  const STOCK_CODE = [/^[A-Z][a-z]\d{3,}$/, /^[A-Za-z]{2,3}\d{4,}$/, /^[A-Za-z]-?\d{5,}$/];
  const isStockCode = (tok) => STOCK_CODE.some((re) => re.test(tok));
  function titleToQuery(title, maxTokens = 7) {
    let t = title.normalize("NFKC");
    t = t.replace(/\/[A-Z]{1,2}\d{3}\s*$/, " ");
    t = t.replace(/[【\[〔<]\s*[A-Za-z]{0,3}-?\d{2,7}[A-Za-z]?\s*[】\]〕>]/g, " ");  // 【L256】[A-123] 等の括弧つき管理番号（appraise.py と同じ）
    t = t.replace(/\d+円[~〜～]?/g, " ");
    t = t.replace(/\b[wW]\s*\/\s*/g, " ");
    t = t.replace(/(\d)\s*(mm|㎜)\s*([fF]\s*\/?\s*\d)/g, "$1mm $3");
    t = t.replace(/[①-⑳]/g, " ");
    t = t.replace(/非\s*-?\s*A[iI](?![-sS])/g, " nonAi ");
    for (const [re, rep] of VNORM) t = t.replace(re, rep);  // 型の表記をそろえる（Python と同じ）
    t = t.replace(/[#＃No.]{1,3}\s*[A-Za-z]{0,3}-?\d{3,}[A-Za-z]?\b/g, " ");
    t = t.replace(/(\d)\s*[・･]\s*(\d)/g, "$1.$2");
    t = t.replace(/\b[fF]\s*\/\s*(\d)/g, "F$1");
    t = t.replace(/\b1\s*:\s*(\d(?:\.\d)?)\b/g, "F$1");
    t = t.replace(/\b(EF-S|EF|RF|FD|XF|FE)(\d)/gi, "$1 $2");
    t = t.replace(/\b([fF]\d(?:\.\d)?(?:-\d(?:\.\d)?)?)([LGD])\b/g, "$1 $2");
    t = t.replace(/[【】\[\]（）()「」『』★☆◆◇■□●○◎※!！?？/|｜,、。:：~〜♪#＃]+/g, " ");
    let brand = "";
    const allBrands = [];
    for (const [ja, en] of C.brandsJa) {
      if (t.includes(ja)) { brand = brand || en; allBrands.push(en); t = t.split(ja).join(" "); }
    }
    for (const [ja, en] of C.wordsJa) t = t.split(ja).join(` ${en} `);
    const tokens = [];
    for (const m of t.matchAll(/[A-Za-z0-9αµ][A-Za-z0-9αµ.\-+]*/g)) {
      const tok = m[0];
      const low = tok.toLowerCase().replace(/^[.\-]+|[.\-]+$/g, "");
      if (!low || NOISE.has(low)) continue;
      if (/^\d+$/.test(low) && ((low.length < 2 && maxTokens <= 7) || low.length >= 4)) continue;
      if (/^no\.?\d+$/.test(low)) continue;
      if (isStockCode(tok)) continue;  // 出品者の管理番号は eBay の検索語に入れない（appraise.py の STOCK_CODE と同じ）
      const line = low.replace(/^[a-z]\./, "");
      if (C.lensLines[line]) brand = brand || C.lensLines[line];
      if (C.brandsEn[low]) {
        brand = brand || C.brandsEn[low];
        allBrands.push(C.brandsEn[low]);
        if (C.brandsEn[low] !== "Carl") continue;
      }
      if (!tokens.some((x) => x.toLowerCase() === low)) tokens.push(tok);
    }
    const makers = allBrands.filter((b) => C.lensMakers.includes(b.toLowerCase()));
    if (makers.length) brand = makers[0];
    const toks = maxTokens <= 7 ? tokens.map((x) => (x === "nonAi" ? "Non-Ai" : x)) : tokens;
    const words = (brand ? [brand] : []).concat(toks);
    if (maxTokens > 7) { for (const b of new Set(allBrands)) if (b !== brand) words.push(b); return words.join(" "); }
    return words.slice(0, maxTokens).join(" ");
  }

  function tokensOf(s) {
    let q = titleToQuery(s, 20).normalize("NFKC").toLowerCase();
    q = q.replace(/(\d)\s*mm(?![a-z])/g, "$1mm").replace(/\bf\s*\/\s*(\d)/g, "f$1").replace(/[()\[\]+&]/g, " ");
    const out = new Set();
    for (let t of q.split(/[\s/,]+/)) {
      t = t.replace(/^[.\-]+|[.\-]+$/g, "");
      if (!t || NOISE.has(t) || t === "body" || t === "lens" || t === "本体") continue;
      if (/^\d{1,2}\.\d$/.test(t)) t = "f" + t;
      if (t === "ais" || t === "ai-s") t = "ai-s";
      out.add(t);
    }
    return out;
  }

  const has = (set, pred) => { for (const x of set) if (pred(x)) return true; return false; };
  const sub = (a, b) => { for (const x of a) if (!b.has(x)) return false; return true; };
  const inter = (a, b) => { let n = 0; for (const x of a) if (b.has(x)) n++; return n; };
  function variantsConflict(qt, rt) {
    for (const g of C.variantGroups) {
      if (!g.ctx.some((c) => qt.has(c) || rt.has(c))) continue;
      const vs = Object.keys(g.vars);
      const qv = vs.filter((v) => qt.has(v)).sort().join("|"), rv = vs.filter((v) => rt.has(v)).sort().join("|");
      if (g.strict ? qv !== rv : qv && rv && qv !== rv) return true;
    }
    return false;
  }
  // タイトルの型（1つに決まるときだけ）: label は機種名に入れる表記、must/not は eBay出品中の集計で使う
  const VLABEL = { newfd: "New FD", ssc: "SSC", sc: "SC", nonai: "Non-Ai", ai: "Ai", "ai-s": "Ai-S", aikai: "Ai改" };
  function variantOf(toks) {
    for (const g of C.variantGroups) {
      if (!g.ctx.some((c) => toks.has(c))) continue;
      const found = Object.keys(g.vars).filter((v) => toks.has(v));
      if (found.length !== 1) continue;
      const v = found[0];
      return { label: VLABEL[v] || g.vars[v], must: g.neg ? [g.vars[v]] : [],
        not: g.neg ? Object.entries(g.vars).filter(([k]) => k !== v).map(([, d]) => d) : [] };
    }
    return null;
  }
  // eBay 検索語に型を入れ、neg のグループはほかの型を除外（Python の ebay_search_query と同じ）
  function ebaySearchQuery(query, title) {
    const toks = tokensOf(query + " " + (title || ""));
    let words = query.split(" ").filter(Boolean);
    for (const g of C.variantGroups) {
      if (!g.ctx.some((c) => toks.has(c))) continue;
      const found = Object.keys(g.vars).filter((v) => toks.has(v));
      if (found.length !== 1) continue;
      const v = found[0], disp = g.vars[v];
      words = words.map((w) => (w.toLowerCase() === v ? disp : w));
      if (!words.includes(disp)) words.push(disp);
      if (g.neg) for (const [k, d] of Object.entries(g.vars)) if (k !== v && d !== disp) words.push("-" + d);
    }
    return [...new Set(words)].join(" ");
  }
  const pick = (set, pool) => [...set].filter((x) => pool.has(x)).sort().join("|");

  function detectFlags(title) {
    return FLAG_RES.filter(([, re]) => re.test(title)).map(([n]) => n);
  }

  // 焦点距離（50mm・5cm）があればレンズ
  const lensy = (set) => has(set, (t) => t.endsWith("mm") || /^\d+(?:\.\d+)?cm$/.test(t));
  function matchRefs(query, refs) {
    const qt = tokensOf(query);
    if (!qt.size) return [];
    const qmm = lensy(qt);
    const qAcc = ACCESSORY_RE.test(query);
    const scored = [];
    for (const r of refs) {
      const rt = r.tset || (r.tset = new Set(r.tokens));
      if ([...qt].some((t) => THIRD.has(t) && !rt.has(t))) continue;
      const rb = [...rt].filter((t) => BRAND_SET.has(t));
      if (rb.length && !rb.some((t) => qt.has(t))) continue;
      if (qmm !== lensy(rt)) continue;
      if (r.acc && !qAcc) continue;
      if (pick(qt, PREMIUM) !== pick(rt, PREMIUM)) continue;
      if ((qt.has("nonai") && (rt.has("ai") || rt.has("ai-s"))) || (rt.has("nonai") && (qt.has("ai") || qt.has("ai-s")))) continue;
      if (pick(qt, GENS) !== pick(rt, GENS)) continue;
      if (variantsConflict(qt, rt)) continue;
      // 実績側の英字の語（メーカー・レンズ銘など）が1つも無ければ別物（YASHICA 35mm 2.8 に ALPA 35mm 2.8 が当たっていた）
      if (![...rt].some((t) => !/\d/.test(t) && !COLORS.has(t) && qt.has(t))) continue;
      const digits = new Set([...rt].filter((t) => /\d/.test(t)));
      if (!sub(digits, qt)) continue;
      if (!digits.size) {
        const core = new Set([...rt].filter((t) => !COLORS.has(t)));
        if (![...core].some((t) => !BRAND_SET.has(t)) || !sub(core, qt)) continue;
      }
      const score = inter(qt, rt) / rt.size;
      if (score >= 0.6) scored.push([score, r]);
    }
    for (const tier of [0, 1, 2]) {
      const c = scored.filter(([, r]) => r.tier === tier);
      if (c.length) return c.sort((a, b) => b[0] - a[0]).slice(0, 10).map(([, r]) => r);
    }
    return [];
  }

  function matchKantei(title) {
    const t = title.normalize("NFKC").toLowerCase();
    const qt = tokensOf(title);
    const qmm = lensy(qt);
    for (const r of KANTEI) {
      if (!r.re.every((re) => re && re.test(t))) continue;
      const rt = new Set(r.tokens);
      if ([...qt].some((x) => THIRD.has(x) && !rt.has(x))) continue;
      if (qt.has("nonai") && (rt.has("ai") || rt.has("ai-s"))) continue;
      if (qmm !== lensy(rt)) continue;
      const model = [...rt].filter((x) => /\d/.test(x) && !x.endsWith("mm") && !x.startsWith("f"));
      if (model.length && !model.every((x) => qt.has(x))) continue;
      // 焦点距離・F値は表の側の値がタイトルに全部あること（「55mm F3.5」に「18mm f/3.5」が当たっていた）
      if (![...rt].filter((x) => /^(?:[\d.\-]+mm|f[\d.\-]+)$/.test(x)).every((x) => qt.has(x))) continue;
      return [r];
    }
    return [];
  }

  // ------------------------------------------------------------------ 利益計算
  function roundDown(p) {
    for (const [limit, step] of C.increments) if (p < (limit || Infinity)) return Math.floor(p / step) * step;
    return Math.floor(p);
  }

  function calculate(it, usd, fx) {
    const g = DATA.cfg;
    const rev = usd + g.buyerShippingUsd;
    const fees = rev * g.feePct / 100 + g.perOrderUsd;
    const net = (rev - fees) * fx * (1 - g.convLossPct / 100);
    const revJpy = rev * fx;
    const dom = it.ship == null ? g.defaultDomestic : it.ship;
    const intl = Math.round(g.deductionUsd * fx);
    const tm = it.tax ? 1 + g.storeTaxPct / 100 : 1;  // ストアは入札が税抜、支払いは×1.1
    const k = tm * (1 - g.refundRatio);
    const fixed = net - dom - intl - g.packing;
    // 安心ストア（フォロー中）は基準を緩める（appraise.py の calculate と同じ）
    const minP = it.trusted ? (g.trustedMinProfit ?? g.minProfit) : g.minProfit;
    const minM = it.trusted ? (g.trustedMinMarginPct ?? g.minMarginPct) : g.minMarginPct;
    const maxBid = Math.max(0, roundDown(Math.min((fixed - minP) / k, (fixed - minM / 100 * revJpy) / k)));
    const be = Math.max(0, roundDown(fixed / k));
    const out = { tax: !!it.tax, maxBid, breakeven: be, fees: Math.round(fees * fx), intl, dom, net: Math.round(net), netUsd: usd - g.deductionUsd };
    if (it.price != null) {
      out.bidNow = it.price / tm;
      const p = fixed - out.bidNow * k;
      out.profit = Math.round(p);
      out.margin = Math.round((p / revJpy) * 1000) / 10;
    }
    return out;
  }

  function judge(it) {
    const c = it.calc;
    if (!c) return ["要確認", "相場なし"];
    const pre = it.flags.includes("ジャンク") ? "ジャンク・" : "";
    if (it.flags.some((f) => f === "付属品のみ?" || f === "アダプター等" || f === "セット品"))
      return ["要確認", pre + it.flags.filter((f) => f !== "ジャンク").join("・") + "の可能性"];
    if (it.flags.includes("光学難あり")) return ["要確認", pre + "光学難あり（方針では避ける）"];
    if (c.maxBid <= 0) return ["×", pre + "基準を満たす入札額なし"];
    if (it.price == null) return ["要確認", pre + "現在価格不明"];
    if (c.bidNow <= c.maxBid * 0.8) return ["◎", pre + "余裕あり"];
    if (c.bidNow <= c.maxBid) return ["○", pre + "上限に近い"];
    return ["×", pre + "上限超過"];
  }

  function modelKey(title) {
    const toks = titleToQuery(title, 20).split(" ").filter(Boolean);
    const brand = toks.length && BRAND_SET.has(toks[0].toLowerCase()) ? toks[0] : "";
    const rest = brand ? toks.slice(1) : toks;
    // 管理番号らしい語（C-2436・OL4709・IC0144・A-164、レンズの機種名に混ざった 3桁以上の数字）は機種名に入れない
    const isLens = rest.some((t) => /mm$/i.test(t));
    const code = (t) => /^[A-Za-z]{1,2}-\d{3,}$/.test(t) || /^[A-Za-z]{2,3}\d{4,}$/.test(t) || /^\d{4,}-\d{1,3}$/.test(t) || (isLens && /^\d{3,}$/.test(t));
    // 世代（II・III・Mark II）と限定・チタンなどの型も機種名に残す（「Sony α7 II」が「Sony α7」になり、［直す］の相場が当たらなかった）
    const gen = (t) => GENS.has(t.toLowerCase()) || PREMIUM.has(t.toLowerCase());
    const keyToks = rest.filter((t) => (/\d/.test(t) && !code(t)) || gen(t)).filter((t, i, a) => !gen(t) || a.findIndex((x) => x.toLowerCase() === t.toLowerCase()) === i);
    while (keyToks.filter((t) => !gen(t)).length > 3) keyToks.splice(keyToks.findLastIndex((t) => !gen(t)), 1);
    const vt = variantOf(tokensOf(title));
    const label = (brand ? [brand] : []).concat(keyToks.some((t) => !gen(t)) ? keyToks : rest.slice(0, 2))
      .concat(vt ? [vt.label] : []).join(" ");
    const norm = label.toLowerCase().split(" ").map((t) => {
      if (/^f\/?\d+(\.\d)?(-\d+(\.\d)?)?$/.test(t)) t = t.replace(/^f\/?/, "");
      return /^\d+(\.\d)?(-\d+(\.\d)?)?$/.test(t) && t.includes(".") ? "f" + t : t;
    });
    return [label, norm.join(" ")];
  }

  const ebay = (q) => ({
    sold: "https://www.ebay.com/sch/i.html?_nkw=" + encodeURIComponent(q).replace(/%20/g, "+") + "&LH_Sold=1&LH_Complete=1&_sop=16&_ipg=120",
    now: "https://www.ebay.com/sch/i.html?_nkw=" + encodeURIComponent(q).replace(/%20/g, "+") + "&_sop=16&_ipg=120",
    pr: "https://www.ebay.com/sh/research?marketplace=EBAY-US&keywords=" + encodeURIComponent(q) + "&dayRange=90&tabName=SOLD",
  });

  const median = (a) => { const s = [...a].sort((x, y) => x - y), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };
  // 相場メモ（手で直した相場）は過去データより優先する。appraise.py の analyze() と同じ
  const isMemo = (r) => String(r.source || "").startsWith("相場メモ");
  function matchPreferMemo(query, memoRefsList, otherRefs) {
    const m = matchRefs(query, memoRefsList);
    return m.length ? m : matchRefs(query, otherRefs);
  }

  // 過去データの状態（商品名の MINT・Exc+++ など。頒布会リストはコンディション欄）。表示用
  // 規則は appraise.py の COND_RULES・COND_RANK・LISTING_COND_RULES（build_extension.py が渡す）
  const COND_RULES = (C.condRules || []).map(([p, o]) => [new RegExp(p, "i"), o]);
  const LISTING_COND = (C.listingCondRules || []).map(([p, r]) => [new RegExp(p), r]);
  function conditionOf(r) {
    // 頒布会リストのコンディション欄・手で直した相場の状態（source の括弧内。例: 頒布会リスト(Near Mint 小傷)・相場メモ(MINT)）を先に、
    // 無ければ商品名を見る
    const h = String(r.source || "").normalize("NFKC").match(/^(頒布会リスト|相場メモ)\((.*?)\)?$/);
    for (const t of [h ? h[2] : "", String(r.title || "").normalize("NFKC")]) {
      for (const [re, out] of COND_RULES) { const m = t.match(re); if (m) return out.includes("$1") ? out.replace("$1", m[1]) : out; }
    }
    if (h && h[1] === "相場メモ") return h[2].trim();
    const w = h && h[2].match(/^[^\s)]+/);
    return w ? w[0] : "";
  }
  // 状態の補正（appraise.py の condition_adjusted と同じ）: 過去データの価格を、この出品の状態に合わせて1ランク step ドルずつ動かす
  function listingRank(title) {
    const t = String(title || "").normalize("NFKC");
    for (const [re, r] of LISTING_COND) if (re.test(t)) return r;
    return C.listingDefaultRank || 7;
  }
  const RANK_LABEL = { 11: "Unused", 10: "TOP MINT", 9: "MINT", 8: "Almost MINT", 7: "Near MINT", 6: "Exc+5", 5: "Exc+4", 4: "Exc+3", 3: "Exc+2", 2: "Exc+1", 1: "Exc" };
  // 補正したときの説明（例: この出品は「美品」→ Near MINT として、状態1ランク＝$10で補正）。補正しなかったら ""
  function conditionNote(refs, title, flags, rank) {
    if (flags.includes("ジャンク") || !Number(DATA.cfg.condStep || 0)) return "";
    if (!refs.some((r) => (C.condRank || {})[conditionOf(r)] != null)) return "";
    if (rank != null) return `この出品は［直す］で ${RANK_LABEL[rank]} と指定`;
    const t = String(title || "").normalize("NFKC");
    const hit = (C.listingCondRules || []).find(([p]) => new RegExp(p).test(t));
    const word = hit ? t.match(new RegExp(hit[0]))[0] : "表記なし";
    return `この出品は${hit ? "「" + word + "」" : "表記なし"}＝${RANK_LABEL[listingRank(title)]}で補正（1ランク$${DATA.cfg.condStep}）`;
  }
  function conditionAdjusted(refs, title, flags, rank) {
    const step = Number(DATA.cfg.condStep || 0);
    const lr = flags.includes("ジャンク") || !step ? null : rank != null ? rank : listingRank(title);
    return refs.map((r) => {
      const rr = (C.condRank || {})[conditionOf(r)];
      return lr != null && rr != null ? Math.round(Math.max(r.price + step * (lr - rr), 0) * 100) / 100 : r.price;
    });
  }
  return { titleToQuery, tokensOf, detectFlags, matchRefs, matchKantei, calculate, judge, modelKey, ebay, median, roundDown, matchPreferMemo, isMemo, conditionOf, conditionAdjusted, conditionNote };
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeCore };
