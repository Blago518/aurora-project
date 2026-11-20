// 自動生成ファイル: tools/build_store.py が tools/extension_template.js の @core 部分をコピーしたもの。
// 直接編集しない（拡張機能と判定を一致させるため）。データは入っていない。
function makeCore(DATA) {
  "use strict";
  const C = DATA.consts;
  // ------------------------------------------------------------------ 文字列処理（appraise.py と同じ規則）
  const BRAND_SET = new Set(Object.values(C.brandsEn).map((b) => b.toLowerCase()));
  const NOISE = new Set(C.noise), THIRD = new Set(C.thirdParty), COLORS = new Set(C.colors);
  const VNORM = C.variantNormalize.map(([p, r]) => [new RegExp(p, "gi"), r]);
  const GENS = new Set(C.generations), PREMIUM = new Set(C.premium), SERIES = new Set(C.series || []);
  // 系統の語（appraise.series_of と同じ）: Zuiko Digital・M.Zuiko と OM Zuiko、Rolleicord と Rolleiflex は別物
  const canonL = (ts) => ts.has("l") && ts.has("canon");  // Canon の L レンズ（appraise.canon_l）
  const seriesOf = (ts, lens) => { const s = new Set([...ts].filter((t) => SERIES.has(t))); if (canonL(ts)) s.add("l"); if (s.has("m.zuiko")) s.add("digital");
    if (!lens) { s.delete("digital"); s.delete("m.zuiko"); } return [...s].sort().join(","); };
  // マウント（appraise.mount_of と同じ）: レンズはマウントの組が両方で同じときだけ相場を当てる
  const MOUNTS = C.mounts || {}, MOUNT_TOKENS = new Set(Object.values(MOUNTS).flatMap((d) => Object.keys(d)));
  // フィルムの判（appraise.format_of と同じ）: 「Pentax 645 200mm F4」「PENTAX-110 50mm F2.8」に 35mm 判の同じ焦点距離の相場を当てない
  const FORMATS = C.formats || {};
  const formatOf = (ts) => [...new Set([...ts].filter((t) => FORMATS[t]).map((t) => FORMATS[t]))].sort().join(",");
  const mountOf = (ts) => { const out = new Set(); for (const [b, w] of Object.entries(MOUNTS)) if (ts.has(b) || (b === "fujifilm" && ts.has("fuji"))) for (const t of ts) if (w[t]) out.add(w[t]); return [...out].sort().join(","); };
  const ACCESSORY_RE = new RegExp(C.accessoryRe, "i");
  const FLAG_RES = C.flagRules.map(([name, pat]) => [name, new RegExp(pat, "i")]);
  const KANTEI = DATA.kantei.map((r) => ({ ...r, re: r.patterns.map((p) => { try { return new RegExp(p, "i"); } catch (e) { return null; } }) }));

  // 出品者の管理番号（Tj036・Ft644461・NA77401・【L256】・OL4709 など）。規則と理由は appraise.py の is_stock_code と同じ:
  // 相場データに出てくる語は型番なので外さない／型番に無い形は外す／型番にもある形はメーカー名より前のときだけ外す
  const STOCK_STRONG = [/^[A-Z][a-z]\d{3,}$/, /^[A-Za-z]{1,3}-?\d{5,}$/], STOCK_WEAK = [/^[A-Za-z]{2,3}\d{4}$/];
  const BRACKET_CODE = /[【\[〔<]\s*([A-Za-z]{0,3}-?\d{2,7}[A-Za-z]?)\s*[】\]〕>]/g;
  const KNOWN_MODELS = new Set();
  for (const r of (DATA.refs || []).concat(DATA.kantei || [])) {
    for (const tok of String(r.title || "").normalize("NFKC").match(/[A-Za-z0-9][A-Za-z0-9-]*/g) || []) {
      const low = tok.toLowerCase().replace(/-/g, "");
      if (/\d/.test(low) && /[a-z]/.test(low)) KNOWN_MODELS.add(low);
    }
  }
  function isStockCode(tok, beforeBrand, bracketed) {
    if (KNOWN_MODELS.has(tok.toLowerCase().replace(/-/g, ""))) return false;
    if (STOCK_STRONG.some((re) => re.test(tok))) return true;
    return beforeBrand && (bracketed || STOCK_WEAK.some((re) => re.test(tok)));
  }
  // 商品名の書き方をそろえる・型番でない語を外す表（appraise.TITLE_FIXES と同じ。2026-11-19 監査）
  const TITLE_FIXES = (C.titleFixes || []).map(([p, r, f]) => [new RegExp(p, "g" + f), r]);
  const KEEP4_AFTER = new Set(C.keep4After || []);
  function titleToQuery(title, maxTokens = 7) {
    let t = title.normalize("NFKC");
    for (const [re, rep] of TITLE_FIXES) t = t.replace(re, rep);
    t = t.replace(/\/[A-Z]{1,2}\d{3}\s*$/, " ");
    // 末尾の管理番号（【9022NT】K11・【K172】K03・【1582-4NT】K17・【25490CK】J26。appraise.title_to_query と同じ。2026-11-19）
    t = t.replace(/[【\[]\s*[A-Za-z]{0,2}\d{2,6}(?:-\d{1,5})?[A-Za-z]{0,3}\s*[】\]]\s*[A-Z]\d{1,3}\s*$/, " ");
    t = t.replace(/[【\[]\s*\d{3,6}(?:-\d{1,5})?[A-Za-z]{1,3}\s*[】\]]\s*$/, " ");
    // 双眼鏡の 6×15・8x30、中判の 6x6 を1語に（数字だけの語どうし。「ZTC1.4x 1103」「DSC-T2×2」はつなげない）。7° は視野角
    t = t.replace(/(?<![A-Za-z0-9.\-])(\d+)\s*[×xX]\s*(\d+)(?![\d.])/g, "$1x$2").replace(/\d+(?:\.\d+)?\s*°/g, " ");
    t = t.replace(/×\s*\d+(?![\d.])/g, " ");  // 残った ×2・×3 は個数
    t = t.replace(BRACKET_CODE, " \u0001$1 ");  // 【L256】→ 管理番号の候補の印（\u0001）を付ける
    t = t.replace(/\d+円[~〜～]?/g, " ");
    t = t.replace(/[∞¶∩§]\s*\S*/g, " ");  // ストアの管理番号（… ∞ 776BC-16・¶ 7774A-5）
    t = t.replace(/\s(?:\S\s+)?\d{3}[0-9A-Z]{1,3}-\d{1,3}\s*$/, " ");  // 末尾の管理番号（… ∴ 7750D-3・》 777D2-1・c 77708-12）
    t = t.replace(/\b\d{3,4}[A-Z]{1,3}\d{0,2}-\d{1,3}\b/g, " ");  // 同じ形の管理番号（7774A-1・776D1-4）
    t = t.replace(/\b[wW]\s*\/\s*/g, " ");
    t = t.replace(/(\d)\s*(mm|㎜)\s*([fF]\s*\/?\s*\d)/g, "$1mm $3");
    t = t.replace(/[①-⑳]/g, " ");
    t = t.replace(/非\s*-?\s*A[iI](?![-sS])/g, " nonAi ");
    for (const [re, rep] of VNORM) t = t.replace(re, rep);  // 型の表記をそろえる（Python と同じ）
    t = t.replace(/(?<![A-Za-z0-9])N[oO]\.\s*[A-Za-z]{0,3}-?\d{3,}[A-Za-z]?(?![A-Za-z0-9])/g, " ");  // No.10131（以前は「NIKON D850」の「N D850」まで消していた）
    t = t.replace(/(\d)\s*[・･]\s*(\d)/g, "$1.$2");
    t = t.replace(/\b[fF]\s*\/\s*(\d)/g, "F$1");
    t = t.replace(/\b1\s*:\s*(\d(?:\.\d)?)\b/g, "F$1");
    t = t.replace(/\b(EF-S|EF|RF|FD|XF|FE)(\d+(?:-\d+)?\s*(?:mm|㎜))/gi, "$1 $2");  // 焦点距離が続くときだけ（Nikon FE2・FE10 は分けない）
    t = t.replace(/\b([fF]\d(?:\.\d)?(?:-\d(?:\.\d)?)?)([LGD])\b/g, "$1 $2");
    t = t.replace(/(?<![\d.-])(\d{2,3})-\s+(?=[fF]\d)/g, "$1mm ");  // 「EF 50- F1.2L」→ 50mm（appraise と同じ）
    t = t.replace(/[【】\[\]（）()「」『』★☆◆◇■□●○◎※!！?？/|｜,、。:：~〜♪#＃]+/g, " ");
    let brand = "";
    const allBrands = [];
    for (const [ja, en] of C.brandsJa) {
      if (t.includes(ja)) { brand = brand || en; allBrands.push(en); t = t.split(ja).join(" \u0002 "); }  // \u0002 = メーカー名の位置
    }
    for (const [ja, en] of C.wordsJa) t = t.split(ja).join(` ${en} `);
    const tokens = [];
    const found = [...t.matchAll(/[A-Za-z0-9αµ][A-Za-z0-9αµ.\-+]*/g)];
    const brandAt = (t.includes("\u0002") ? [t.indexOf("\u0002")] : [])
      .concat(found.filter((m) => C.brandsEn[m[0].toLowerCase().replace(/^[.\-]+|[.\-]+$/g, "")]).map((m) => m.index).slice(0, 1));
    // メーカー名が無いタイトルは「先頭の語」だけを頭とみなす（appraise.py と同じ）
    const firstBrand = brandAt.length ? Math.min(...brandAt) : (found.length ? found[0].index + found[0][0].length : 0);
    const tokAt = {};
    for (const m of found) {
      const tok = m[0];
      const low = tok.toLowerCase().replace(/^[.\-]+|[.\-]+$/g, "");
      if (!(tok in tokAt)) tokAt[tok] = m.index;
      if (!low || NOISE.has(low)) continue;
      if (/^\d+$/.test(low) && ((low.length < 2 && maxTokens <= 7) || low.length >= 4)
        && !(low.length === 4 && tokens.length && KEEP4_AFTER.has(tokens[tokens.length - 1].toLowerCase()))) continue;  // COOLPIX 4500 の4桁は型番
      if (/^no\.?\d+$/.test(low)) continue;
      if (isStockCode(tok, m.index < firstBrand, m.index > 0 && t[m.index - 1] === "\u0001")) continue;  // 出品者の管理番号は検索語に入れない
      const line = low.replace(/^[a-z]\./, "");
      if (C.lensLines[line]) brand = brand || C.lensLines[line];
      if (C.brandsEn[low]) {
        brand = brand || C.brandsEn[low];
        allBrands.push(C.brandsEn[low]);
        if (C.brandsEn[low] !== "Carl" && low !== "lumix") continue;  // LUMIX はシリーズ名として残す
      }
      if (!tokens.some((x) => x.toLowerCase() === low)) tokens.push(tok);
    }
    const makers = allBrands.filter((b) => C.lensMakers.includes(b.toLowerCase()));
    if (makers.length) brand = makers[0];
    // 社外レンズ（Rokinon・Viltrox など）は「キャノンマウント」のメーカーではなく、その社名を頭に（appraise.title_to_query と同じ）
    const tp = tokens.find((x) => THIRD.has(x.toLowerCase()) && !C.lensMakers.includes(x.toLowerCase()));
    // （社名がメーカー名より前にあるときだけ。「Canon AE-1 おまけレンズ付き TEFNON」のボディは Canon のまま）
    if (tp && !THIRD.has(brand.toLowerCase()) && !C.lensMakers.includes(brand.toLowerCase()) && (!brandAt.length || (tokAt[tp] || 0) < Math.min(...brandAt))) {
      if (brand && !allBrands.includes(brand)) allBrands.push(brand);
      brand = tp;
      tokens.splice(tokens.indexOf(tp), 1);
    }
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
  const VLABEL = C.variantLabels || { newfd: "New FD", ssc: "SSC", sc: "SC", nonai: "Non-Ai", ai: "Ai", "ai-s": "Ai-S", aikai: "Ai改" };  // appraise.VARIANT_LABELS
  function variantOf(toks) {
    for (const g of C.variantGroups) {
      if (!g.ctx.some((c) => toks.has(c))) continue;
      const found = Object.keys(g.vars).filter((v) => toks.has(v));
      if (found.length !== 1) continue;
      const v = found[0];
      return { key: v, label: VLABEL[v] || g.vars[v], must: g.neg ? [g.vars[v]] : [],
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
      if (seriesOf(qt, qmm) !== seriesOf(rt, lensy(rt))) continue;
      if (qmm && mountOf(qt) !== mountOf(rt)) continue;
      if (qmm && formatOf(qt) !== formatOf(rt)) continue;  // 110・645・67 判のレンズと 35mm 判のレンズは別物（appraise.format_of）
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
    for (const tier of [0, 1, 2, 3]) {  // 3 = リサーチ（推定）
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
    // 安心ストア（フォロー中）の基準（appraise.py の calculate と同じ。2026-11-16 から全体と同じ値）
    const minP0 = it.trusted ? (g.trustedMinProfit ?? g.minProfit) : g.minProfit;
    const minP = g.lowPriceUsd && rev <= g.lowPriceUsd ? Math.max(minP0, g.lowPriceMinProfit || 0) : minP0;  // 売値$99以下は利益4,000円以上（appraise.low_price_min_profit）
    const minM = it.trusted ? (g.trustedMinMarginPct ?? g.minMarginPct) : g.minMarginPct;
    const maxBid = Math.max(0, roundDown(Math.min((fixed - minP) / k, (fixed - minM / 100 * revJpy) / k)));
    const be = Math.max(0, roundDown(fixed / k));
    const out = { tax: !!it.tax, maxBid, minProfit: minP, breakeven: be, fees: Math.round(fees * fx), intl, dom, net: Math.round(net), netUsd: usd - g.deductionUsd };
    if (it.price != null) {
      out.bidNow = it.price / tm;
      const p = fixed - out.bidNow * k;
      out.profit = Math.round(p);
      out.margin = Math.round((p / revJpy) * 1000) / 10;
    }
    return out;
  }

  // 光学難（2026-11-18 ユーザー決定・おまかせ）: カビ・クモリ・バルサム等がはっきり書かれた出品は × で見送る（実績は平均赤字）。
  // 「カビ・クモリなし」「クモリ：無」のような打ち消しは除く（appraise.py の optical_title_words と同じ）。傷・ヤケは要確認のまま
  const OPTICAL_RE = /(カビ|かび|黴|クモリ|くもり|曇り|白濁|バルサム|分離)/g;
  const OTHER_DEFECT_RE = /ヤケ|傷あり|キズあり|拭き傷/;
  const OPT_NEG_RE = /(なし|無し|ナシ|ありません|有りません|ございません|見られません|見当たりません|確認できません|(?<!少)ない|無い)/;
  function opticalWords(text) {
    const out = [];
    for (const m of String(text || "").matchAll(OPTICAL_RE)) {
      const w = m[1], rest = text.slice(m.index, m.index + 60);
      const kv = rest.slice(w.length).match(/^\s*[：:]\s*([^\s　、。|｜]+)/);
      if (kv) { if (/^(なし|無し|ナシ|無|ない|-|ー|×)/.test(kv[1])) continue; }
      else {
        const cut = rest.search(/[。\n】|｜]/);
        if (OPT_NEG_RE.test(cut < 0 ? rest : rest.slice(0, cut))) continue;
      }
      if (!out.includes(w)) out.push(w);
    }
    return out;
  }
  function judge(it) {
    const c = it.calc;
    if (!c) return ["要確認", "相場なし"];
    let pre = it.flags.includes("ジャンク") ? "ジャンク・" : "";
    const isSet = !!(it.setParts && it.setParts.length >= 2);  // まとめ売りで中身を2点以上当てたもの
    if (it.flags.includes("アダプター等") || (!isSet && it.flags.some((f) => f === "付属品のみ?" || f === "セット品")))
      return ["要確認", pre + it.flags.filter((f) => f !== "ジャンク").join("・") + "の可能性"];
    if (isSet) pre += `まとめ売り${it.setParts.length}点・`;
    const opt = opticalWords(it.title || "");
    if (opt.length) return ["×", pre + "光学難（" + opt.join("・") + "）は見送り"];
    if (it.flags.includes("光学難あり") && OTHER_DEFECT_RE.test(it.title || "")) return ["要確認", pre + "難あり（方針では避ける）"];
    if (c.maxBid <= 0) return ["×", pre + "基準を満たす入札額なし"];
    if (it.price == null) return ["要確認", pre + "現在価格不明"];
    if (c.bidNow <= c.maxBid * 0.8) return ["◎", pre + "余裕あり"];
    if (c.bidNow <= c.maxBid) return ["○", pre + "上限に近い"];
    return ["×", pre + "上限超過"];
  }

  // 機種名に残すシリーズ名（appraise.LINE_WORDS と同じ。2026-11-19 ユーザー指摘「Canon Power Shot S110 → Canon S110」）
  // 機種名（表示・eBay 検索語・相場メモの名前）だけに使い、比較用キー（まとめ方）と相場の当たり方には使わない
  const LINE_WORDS = C.lineWords || {}, KEEP_NUM_AFTER = new Set(C.keepNumAfter || []), FORMAT_NUMS = new Set(C.formatNums || []);
  const DISPLAY_WORDS = C.displayWords || {};  // BODY_SKIP は下の modelWord と共通（const の巻き上げで先に使える）
  function modelKey(title) {
    const toks = titleToQuery(title, 20).split(" ").filter(Boolean);
    // 知らないメーカーでも、先頭が英字の語ならメーカー名として扱う（Rodenstock Apo-Sironar-N など。無いと相場が当たらない）
    const brand = toks.length && (BRAND_SET.has(toks[0].toLowerCase()) || (/^[A-Za-z][A-Za-z-]{2,}$/.test(toks[0]) && !/\d/.test(toks[0]))) ? toks[0] : "";
    const rest = brand ? toks.slice(1) : toks;
    // 管理番号らしい語（C-2436・OL4709・2-2-2441・e8480）、レンズの機種名に混ざった数字（焦点距離と同じ数字・3桁以上。645・110 の判は残す）、
    // ズーム倍率（3x）は機種名に入れない（appraise.model_key と同じ）
    const isLens = rest.some((t) => /mm$/i.test(t));
    const mmNums = new Set(rest.filter((t) => /mm$/i.test(t)).flatMap((t) => t.match(/\d+/g) || []));
    const ratio = (t) => /^\d+(?:\.\d+)?x$/i.test(t);
    const hasModel = rest.some((t) => /\d/.test(t) && !ratio(t));
    const code = (i, t) => {
      if (/^(?:[A-Za-z]{1,2}-\d{4,}|[A-Za-z]{2,3}\d{4,}|\d{4,}-\d{1,3}|[a-z]{1,2}\d{3,5})$/.test(t)) return true;
      if (ratio(t) && hasModel && !/converter|extender|teleplus|テレコン/i.test(title)) return true;
      if (/^\d+$/.test(t) && isLens) {
        if (FORMAT_NUMS.has(t) || (i > 0 && (KEEP_NUM_AFTER.has(rest[i - 1].toLowerCase()) || LINE_WORDS[rest[i - 1].toLowerCase().split("-")[0]]))) return false;
        return t.length >= 3 || mmNums.has(t);
      }
      return false;
    };
    // 世代（II・III・Mark II）と限定・チタンなどの型も機種名に残す（「Sony α7 II」が「Sony α7」になり、［直す］の相場が当たらなかった）
    const gen = (t) => GENS.has(t.toLowerCase()) || PREMIUM.has(t.toLowerCase());
    const keyToks = [];
    rest.forEach((t, i) => {
      if ((/\d/.test(t) && !code(i, t)) || gen(t)) {
        if (!(gen(t) && keyToks.some((x) => x.toLowerCase() === t.toLowerCase()))) keyToks.push(t);
      }
    });
    while (keyToks.filter((t) => !gen(t)).length > 3) keyToks.splice(keyToks.findLastIndex((t) => !gen(t)), 1);
    // シリーズ名（PowerShot・IXY・EOS Kiss・COOLPIX・PEN・mju など）と、そのすぐ後ろの数字の無い型番（EOS RP・PEN F・PEN EE）
    let lines = [];
    rest.forEach((t, i) => {
      const lt = t.toLowerCase();
      if (LINE_WORDS[lt] || (lt.includes("-") && LINE_WORDS[lt.split("-")[0]] && !/\d/.test(lt))) {  // PEN-FT・mju-II
        lines.push(LINE_WORDS[lt] || t);
        const nx = rest[i + 1] || "";
        if (LINE_WORDS[lt] && /^[A-Z]{1,3}$/.test(nx) && !BODY_SKIP.has(nx.toLowerCase()) && !LINE_WORDS[nx.toLowerCase()]) lines.push(nx);
      }
    });
    if (lines.includes("Kiss") && !lines.includes("EOS") && brand.toLowerCase() === "canon") lines.splice(lines.indexOf("Kiss"), 0, "EOS");
    if (brand.toLowerCase() === "nikon" && /(?:nikon|ニコン)\s*1\s*(?:[JVS][1-9]|AW1|nikkor)/i.test(title.normalize("NFKC"))) lines.unshift("1");
    lines = [...new Set(lines)];
    const vt = variantOf(tokensOf(title));
    const hasMount = !!mountOf(tokensOf(title));
    const ser = rest.filter((t, i) => (SERIES.has(t.toLowerCase()) || (hasMount && MOUNT_TOKENS.has(t.toLowerCase()))) && rest.findIndex((x) => x.toLowerCase() === t.toLowerCase()) === i);
    const used = new Set(lines.concat(ser).map((x) => x.toLowerCase()));
    const fallback = rest.slice(0, 2).filter((t, i) => !code(i, t));  // 型番の数字が無い機種
    const body = (keyToks.some((t) => !gen(t)) ? keyToks : fallback).filter((t) => !used.has(t.toLowerCase()));
    // シリーズ名が型番の一部になっているとき（mju-7040・mju-II）は、シリーズ名だけの語を重ねない
    lines = lines.filter((x) => !lines.concat(body).some((y) => y.toLowerCase().startsWith(x.toLowerCase() + "-")));
    // 商品名に書かれた順に並べる（Canon EOS Kiss X5 EF-S 18-55mm。補った EOS・Nikon 1 の 1 はシリーズ名の前）
    const pos = {};
    rest.forEach((t, i) => { if (!(t.toLowerCase() in pos)) pos[t.toLowerCase()] = i; });
    if (lines.includes("Kiss") && !("eos" in pos)) pos.eos = (pos.kiss || 0) - 0.5;
    if (!("1" in pos)) pos["1"] = -1;
    const at = (w) => (w.toLowerCase() in pos ? pos[w.toLowerCase()] : 99);
    const words = (brand ? [brand] : []).concat([...new Set(lines.concat(ser, body))].sort((a, b) => at(a) - at(b)));
    if (vt && !words.some((w) => w.toLowerCase() === vt.key)) words.push(vt.label);  // 型（AEJ・MMJ・SSC）も機種名に。New FD を2回書かない
    if (canonL(tokensOf(title))) words.push("L");  // Canon の L は機種名・検索語に残す
    const label = [...new Set(words.map((w) => DISPLAY_WORDS[w] || w))].join(" ");  // eBay で通じる書き方に（NewFD → New FD）
    const lineLow = new Set(lines.map((x) => x.toLowerCase()));
    const norm = label.toLowerCase().split(" ").filter((t) => !lineLow.has(t)).map((t) => {  // シリーズ名は比較用キーに入れない
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
  // 相場メモ（手で直した相場）が同じ出品に2つ以上当たったら、いちばん新しい1つだけを使う（2026-11-20 ユーザー指摘:
  // 「Canon X10 $540」と新しく［直す］した「$320」の真ん中 $430 で計算され、直しても変わらなかった）。
  // 新しさ: このブラウザで直したもの（t＝直した時刻）＞ Mac の相場メモ（d＝日付）。appraise.match_prefer_memo と同じ
  function newestMemo(m) {
    if (m.length < 2) return m;
    const key = (r) => [r.local ? 1 : 0, r.t || 0, String(r.d || "")];
    const best = m.reduce((a, b) => { const x = key(a), y = key(b); return (y[0] - x[0] || y[1] - x[1] || (y[2] > x[2] ? 1 : y[2] < x[2] ? -1 : 0)) > 0 ? b : a; });
    return [best];
  }
  function matchPreferMemo(query, memoRefsList, otherRefs) {
    const m = matchRefs(query, memoRefsList);
    return m.length ? newestMemo(m) : matchRefs(query, otherRefs);
  }

  // 過去データの状態（商品名の MINT・Exc+++ など。頒布会リストはコンディション欄）。表示用
  // 規則は appraise.py の COND_RULES・COND_RANK・LISTING_COND_RULES（build_extension.py が渡す）
  const COND_RULES = (C.condRules || []).map(([p, o]) => [new RegExp(p, "i"), o]);
  const LISTING_COND = (C.listingCondRules || []).map(([p, r]) => [new RegExp(p), r]);
  function conditionOf(r) {
    // 頒布会リストのコンディション欄・手で直した相場の状態（source の括弧内。例: 頒布会リスト(Near Mint 小傷)・相場メモ(MINT)）を先に、
    // 無ければ商品名を見る
    const h = String(r.source || "").normalize("NFKC").match(/^(頒布会リスト|相場メモ|リサーチ)\((.*?)\)?$/);
    for (const t of [h ? h[2] : "", String(r.title || "").normalize("NFKC")]) {
      for (const [re, out] of COND_RULES) { const m = t.match(re); if (m) return out.includes("$1") ? out.replace("$1", m[1]) : out; }
    }
    if (h && h[1] === "相場メモ") return h[2].trim();
    const w = h && h[2].match(/^[^\s)]+/);
    return w ? w[0] : "";
  }
  // 状態の補正（appraise.py の condition_adjusted と同じ）: 過去データの価格を、この出品の状態に合わせて1ランク step %ずつ動かす（2026-11-18 から割合。実績から推定）
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
    return `この出品は${hit ? "「" + word + "」" : "表記なし"}＝${RANK_LABEL[listingRank(title)]}で補正（1ランク${DATA.cfg.condStep}%）`;
  }
  function conditionAdjusted(refs, title, flags, rank) {
    const step = Number(DATA.cfg.condStep || 0);
    const lr = flags.includes("ジャンク") || !step ? null : rank != null ? rank : listingRank(title);
    return refs.map((r) => {
      const rr = (C.condRank || {})[conditionOf(r)];
      return lr != null && rr != null ? Math.round(Math.max(r.price * Math.pow(1 + step / 100, lr - rr), 0) * 100) / 100 : r.price;
    });
  }
  // ---- まとめ売り（appraise.py の set_part_titles・set_value と同じ。2026-11-18）: 中身ごとに相場を当てて合計する
  const SET_SPLIT_RE = /\s*(?:[＋+、,／/＆&・()（）]|\sと\s|\s(?:and|with)\s)\s*/i;  // かっこはキットの中身（Python と同じ）
  const KIT_RE = /キット|\bkit\b/i;
  function brandOf(title) {
    const t = String(title || "").normalize("NFKC");
    for (const [ja, en] of C.brandsJa || []) if (t.includes(ja)) return en;
    for (const w of t.match(/[A-Za-z]+/g) || []) {
      const lw = w.toLowerCase();
      if (C.brandsEn[lw] && !["OM", "Carl"].includes(C.brandsEn[lw])) return C.brandsEn[lw];
      if (C.lensLines && C.lensLines[lw] && C.lensLines[lw] !== "Zeiss") return C.lensLines[lw];
    }
    return "";
  }
  // 型の語（appraise.py の norm_title・lens_mm_spans・is_f_number・_model_word・set_part_titles と同じ。2026-11-18）
  const FILM_AFTER_RE = new RegExp((C.filmFormatAfter || "(?!)").replace("(?i)", ""), "i");  // 古い相場データ（11/18 より前）に無くても止まらない
  const F_BEFORE_RE = new RegExp("(?:\\d+(?:\\.\\d+)?\\s*(?:mm)?|" + (C.lensWords || "(?!)") + ")\\s*[/,:]?\\s*$", "i");
  const BODY_SKIP = new Set(C.bodyCodeSkip || []), BODY_OK = new Set(C.bodyCodeOk || []);
  const normTitle = (text) => String(text || "").normalize("NFKC").replace(/(\d)\s*(?:ミリ|㎜)/g, "$1mm");
  function lensMmSpans(text) {
    const t = normTitle(text), out = [];
    for (const m of t.matchAll(/(?<![\d.])(\d+(?:-\d+)?)\s*mm(?![a-z])/gi))
      if (!FILM_AFTER_RE.test(t.slice(m.index + m[0].length))) out.push({ start: m.index, end: m.index + m[0].length, n: m[1] });
    return out;
  }
  function isFNumber(tok, text, start) {
    const tl = tok.toLowerCase().replace(/^[.,]+|[.,]+$/g, "");
    if (/^(?:f(?:\/\d+(?:\.\d+)?|\d+\.\d+)|1:\d+(?:\.\d+)?)$/.test(tl)) return true;
    if (/^f\d{1,2}$/.test(tl)) {
      const t = normTitle(text), i = start != null ? start : t.toLowerCase().indexOf(tl);
      return F_BEFORE_RE.test(t.slice(Math.max(0, i - 14), Math.max(0, i)));
    }
    return false;
  }
  function modelWord(title) {
    const t = normTitle(title);
    const mm = new Map(lensMmSpans(t).map((s) => [s.start, s.n + "mm"]));
    const allowed = new Set(titleToQuery(t, 12).toLowerCase().split(" ").filter(Boolean));
    let prev = "";
    for (const m of t.matchAll(/[Ff]\/\d+(?:\.\d+)?|1:\d+(?:\.\d+)?|[A-Za-z0-9][A-Za-z0-9\-]*(?:\.\d+)?/g)) {
      const tok = m[0], tl = tok.toLowerCase();
      if (mm.has(m.index)) return mm.get(m.index);
      if (allowed.has(tl) || allowed.has(tl.replace(/^[.,]+|[.,]+$/g, ""))) {
        if (/\d/.test(tl) && /[a-z]/.test(tl) && !/^\d+(?:-\d+)?mm$/.test(tl) && !isFNumber(tok, t, m.index) && !/^\d+(?:\.\d+)?(?:mp|x|s)$/.test(tl)) return tok;
        if (/^[A-Za-z]{2,3}$/.test(tok) && tok === tok.toUpperCase() && brandOf(prev) && (BODY_OK.has(tl) || !BODY_SKIP.has(tl))) return tok;
      }
      prev = tok;
    }
    return "";
  }
  const FILM_MM_RE = new RegExp("(?<![\\d.])\\d+\\s*mm(?=\\s*(?:判|フィルム|film|カメラ|camera|一眼|レンジファインダー|rangefinder|slr|コンパクト|compact))", "gi");
  function splitByModels(seg) {
    const spans = lensMmSpans(seg);
    if (!spans.length) return [seg];
    const head = seg.slice(0, spans[0].start);
    if (spans.length < 2 && !modelWord(head)) return [seg];
    const cuts = spans.map((s) => s.start);
    const pieces = [head].concat(cuts.map((c, i) => seg.slice(c, i + 1 < cuts.length ? cuts[i + 1] : seg.length)));
    return pieces.filter((x) => x.trim());
  }
  function setPartTitles(title) {
    // 末尾のストアの管理番号（…/ジャンク/N031）は区切りで分ける前に外す（分けた後では型番と見分けられない。appraise と同じ）
    const t = normTitle(title).replace(/\/[A-Z]{1,2}\d{3}\s*$/, " "), b0 = brandOf(t), out = [];
    for (const seg0 of t.split(SET_SPLIT_RE)) {
      for (let seg of splitByModels(seg0.trim())) {
        seg = seg.trim();
        if (!seg || !modelWord(seg)) continue;
        const f = detectFlags(seg);
        if (f.includes("付属品のみ?") || f.includes("アダプター等")) continue;
        seg = seg.replace(FILM_MM_RE, " ").trim();
        if (b0 && !seg.toLowerCase().includes(b0.toLowerCase())) seg = b0 + " " + seg;  // 「AF-S DX Zoom-NIKKOR」にもメーカー名を付ける
        out.push(seg);
      }
    }
    return out.length >= 2 ? out : [];
  }
  function setValue(parts, fx) {
    const extra = (DATA.cfg.deductionUsd || 0) + DATA.cfg.perOrderUsd + DATA.cfg.packing / fx;
    return Math.round((parts.reduce((a, p) => a + p.usd, 0) - (parts.length - 1) * extra) * 100) / 100;
  }
  // ---- 狙い目スペック（講座資料「レンズ狙い目スペック」。店舗アプリも使うので @core の中）: 単焦点の焦点距離×F値で印を付けるだけ（判定・上限額は変えない）
  //      50mm から離れるほど、F値が小さいほど高い。ズーム（28-70mm など）は対象外
  const SWEET = [[24, 99, "24mm以下は基本全部OK"], [31, 2, "28mm は f/2 より明るい"], [40, 2, "35mm は f/2 より明るい"],
    [60, 1.2, "50mm は f/1.2 より明るい"], [89, 99, "85mm は基本全部OK"], [115, 2, "100mm前後は f/2 より明るい"],
    [160, 2, "135mm は f/2 より明るい"], [250, 2.8, "200mm は f/2.8 より明るい"], [350, 2.8, "300mm は f/2.8 より明るい"],
    [99999, 2.8, "400mm以上は f/2.8 より明るい"]];
  function sweetSpot(title) {
    const t = String(title || "").replace(/[\uff01-\uff5e]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0)).replace(/㎜/g, "mm");
    if (/\d\s*[-~〜]\s*\d+\s*mm/i.test(t)) return "";  // ズーム
    const mm = t.match(/(\d{1,4}(?:\.\d)?)\s*mm/i);
    if (!mm) return "";
    const fl = Number(mm[1]);
    const fs = [...t.matchAll(/(?:\bf\s*\/?\s*|1\s*:\s*)(\d{1,2}(?:\.\d{1,2})?)/gi)].map((m) => Number(m[1])).filter((v) => v > 0.6 && v < 33);
    if (!fs.length) return "";
    const f = Math.min(...fs), band = SWEET.find(([max]) => fl <= max);
    return band && f <= band[1] + 1e-9 ? band[2] : "";
  }

  return { titleToQuery, tokensOf, detectFlags, matchRefs, matchKantei, calculate, judge, modelKey, ebay, median, roundDown, matchPreferMemo, isMemo, conditionOf, conditionAdjusted, conditionNote, setPartTitles, setValue, KIT_RE, sweetSpot };
}
if (typeof module !== "undefined" && module.exports) module.exports = { makeCore };
