// ==UserScript==
// @name         ヤフオク入札鑑定
// @namespace    aurora-research
// @version      __VERSION__
// @description  ヤフオクの検索結果・出品者ページを開くと、販売実績・相場データと照合して上限入札額と利益を表示します
// @match        https://auctions.yahoo.co.jp/search/*
// @match        https://auctions.yahoo.co.jp/seller/*
// @match        https://auctions.yahoo.co.jp/category/*
// @match        https://auctions.yahoo.co.jp/follow/*
// @run-at       document-idle
// @grant        none
// @noframes
// ==/UserScript==
// このファイルは build_extension.py で自動生成されます。直接編集せず、data/ と config.toml を更新して作り直してください。
(function (EMBEDDED) {
  "use strict";
  // 相場データ: 拡張機能のフォルダの data.json（Mac が毎朝作り直す）を、ページを開くたびに読む → chrome://extensions の ↻ は不要。
  // 読めないとき（Safari の Userscripts・テストなど）は、このファイルに埋め込んだ相場を使う
  const isModule = typeof module !== "undefined" && module.exports;
  if (!isModule && typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
    try {
      chrome.runtime.sendMessage({ type: "getData" }, (d) => {
        void chrome.runtime.lastError;
        main(d && d.consts && d.refs ? d : EMBEDDED);
      });
    } catch (e) { main(EMBEDDED); }
  } else {
    main(EMBEDDED);
  }

  function main(DATA) {
  const C = DATA.consts;
  const LS_SEEN = "yhkSeenV1", LS_MEMO_HIST = "yhkMemoHistV1", LS_MEMO = "yhkMemoV3", LS_MEMO_OLD = "yhkMemoV2", LS_FX = "yhkFxV2", LS_OPEN = "yhkOpenV2";

  // @core-start（ここから @core-end までは store/core.js にそのままコピーされる。tools/build_store.py）
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
  // 紛らわしい兄弟の機種を eBay の検索から外す語（2026-11-22 オーナーが「Olympus ZUIKO AUTO-W 24mm F2」に自分で「-2.8」を足した）。
  // 相場データに、同じメーカー・同じ焦点距離で F値だけ違うレンズがあれば「-2.8」（ボディの世代語は外さない: 「F3」とだけ書いた HP の売れた記録まで消えて相場を低く読むため）。
  // 表示・リンクだけ（照合・判定には使わない）。Python の appraise.sibling_excludes と同じ
  const MM_RE = /^\d+(?:-\d+)?mm$/, FNUM_RE = /^f\d+(?:\.\d+)?$/;
  function siblingExcludes(query, refs, limit = 3) {
    const qt = tokensOf(query), brands = [...qt].filter((t) => BRAND_SET.has(t));
    if (!brands.length) return [];
    const mm = [...qt].filter((t) => MM_RE.test(t)).sort().join("|"), qf = [...qt].filter((t) => FNUM_RE.test(t));
    const out = [];
    for (const r of refs || []) {
      const rt = r.tset || (r.tset = new Set(r.tokens || tokensOf(r.title || "")));
      if (!brands.some((b) => rt.has(b))) continue;
      if (mm) {   // レンズだけ
        if ([...rt].filter((t) => MM_RE.test(t)).sort().join("|") !== mm || !qf.length) continue;
        const rf = [...rt].filter((t) => FNUM_RE.test(t));
        if (!rf.length || rf.some((f) => qt.has(f))) continue;
        for (const f of rf.sort()) if (f.includes(".")) out.push(f.slice(1));   // 「-2」のような整数は広すぎるので外さない
      }
    }
    return [...new Set(out)].slice(0, limit).map((w) => "-" + w);
  }
  // eBay の検索語は機種の語だけ（build_store.model_query と同じ。決まりの正規表現は C.modelQuery で Python から渡す）
  const MQ = C.modelQuery ? {
    lensLike: new RegExp(C.modelQuery.lensLike, "i"), kit: new RegExp(C.modelQuery.kitWords, "i"), fixed: new RegExp(C.modelQuery.fixedBody, "i"),
    family: new RegExp(C.modelQuery.lensFamily, "i"), maker: new RegExp("^(?:" + C.modelQuery.lensMaker + ")", "i"), skip: new RegExp("^(?:" + C.modelQuery.bodySkip + ")$"),
    seriesBody: new Set(C.modelQuery.seriesBody), line: new Set(C.modelQuery.lensLine), series: new RegExp("^(?:" + C.modelQuery.series + ")$", "i"),
    gen: new RegExp("^(?:" + C.modelQuery.gen + ")$", "i"), keep: new RegExp("^(?:" + C.modelQuery.lensKeep + ")$") } : null;
  function bodyBeforeMmQ(title) {
    const t0 = String(title || "").normalize("NFKC");
    if (MQ.kit.test(t0)) return true;
    const hasFor = /\bfor\b|用/i.test(t0);
    if (MQ.fixed.test(t0) && !hasFor) return true;
    const toks = titleToQuery(title, 20).split(" ").filter(Boolean);
    if (!hasFor && !MQ.maker.test(t0)) {
      for (const t of toks.slice(1)) {
        const tl = t.toLowerCase();
        if (/^\d+(-\d+)?mm$/.test(tl) || MQ.family.test(tl)) break;
        if (MQ.line.has(tl) || MQ.skip.test(tl)) continue;
        if (/[a-zα]/.test(tl) && /\d/.test(tl)) return true;
      }
    }
    if (MQ.family.test(t0) || hasFor) return false;
    for (const t of toks.slice(1)) {
      const tl = t.toLowerCase();
      if (/^\d+(-\d+)?mm$/.test(tl)) return false;
      if (MQ.line.has(tl) || MQ.skip.test(tl)) continue;
      if (/[a-zα]/.test(tl) && /\d/.test(tl)) return true;
      if (MQ.seriesBody.has(tl)) return true;
    }
    return false;
  }
  function modelWordsQ(ws) {
    if (!ws.length) return ws;
    const t = ws.join(" ");
    if (MQ.lensLike.test(t) && !bodyBeforeMmQ(t)) {
      const out = [ws[0]];
      for (const w of ws.slice(1)) {
        const l = w.toLowerCase();
        if (/^\d+(-\d+)?mm$/.test(l) || /^f\/?\d/.test(l) || MQ.line.has(l) || MQ.family.test(l) || MQ.keep.test(l)) out.push(w);
      }
      return out.slice(0, 7);
    }
    const out = [ws[0]]; let model = false;
    for (const w of ws.slice(1)) {
      const l = w.toLowerCase();
      if (!model && (MQ.series.test(l) || l === "new")) { out.push(w); continue; }
      if (!model && ((/[a-zα]/.test(l) && /\d/.test(l)) || MQ.fixed.test(w) || /^\d{2,4}$/.test(l))) { out.push(w); model = true; continue; }
      if (model && MQ.gen.test(l)) { out.push(w); continue; }
      if (model) break;
    }
    return model ? out : ws.slice(0, 3);
  }
  // 今までの検索語から余計な語だけを引く（Python の appraise.clean_query と同じ）
  const JUNK_QUERY_WORDS = new Set(["point", "shoot", "film", "camera", "compact"]);
  function cleanQuery(q) {
    const ws = String(q || "").split(" ").filter((w) => w && !JUNK_QUERY_WORDS.has(w.toLowerCase()));
    if (ws.length > 2 && /^[A-Za-z]{1,3}\d{1,5}$/.test(ws[ws.length - 1]) && !/^([Ff]\d+|[LlMm]39|[Mm]42|[Tt]\d)$/.test(ws[ws.length - 1]) && ws.slice(1, -1).some((w) => /\d/.test(w))) ws.pop();   // 末尾の管理番号は1つだけ。F値は引かない
    return ws.join(" ");
  }
  function queryForTitle(title, query) {   // appraise.query_for_title と同じ
    const t0 = String(title || "").normalize("NFKC");
    // レンズ固定のカメラで、後ろにレンズの仕様がある物だけ（Hasselblad 500C/M は除く）
    if (MQ && MQ.fixed.test(t0) && !/\bfor\b|用/i.test(t0) && /\d\s?mm/i.test(t0)) {
      const ws = [];
      for (const w of cleanQuery(ebaySearchQuery(query || titleToQuery(title), title)).split(" ").filter(Boolean)) {
        if (/^(\d+(?:-\d+)?mm|[Ff]\/?\d+(?:\.\d+)?)$/.test(w)) break;
        if (MQ.family.test(w) || /^[Aa]\d{1,2}[Mm]?$/.test(w)) continue;
        ws.push(w);
      }
      // 型の名前（Mamiya 7 の 7 など、1桁で落ちる語）を補う
      const m = t0.match(MQ.fixed);
      for (const x of (m ? m[0].split(/\s+/) : [])) if (ws.length && !ws.some((w) => w.toLowerCase() === x.toLowerCase())) ws.splice(1, 0, x);
      if (ws.length >= 2) return ws.join(" ");
    }
    const q = query || titleToQuery(title);
    return cleanQuery(ebaySearchQuery(q, title));
  }
  function modelQuery(title) {
    if (!MQ) return "";   // 決まりが届いていない古いデータ（呼ぶ側が自分の決まりか titleToQuery に戻す）
    return modelWordsQ(titleToQuery(title, 20).split(" ").filter(Boolean)).join(" ");
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
  // 二重の打ち消し（2026-11-23 「クモリがないわけではない」）は、打ち消しではなく「ある」（appraise.py の is_negated と同じ）
  const DOUBLE_NEG_RE = /(?:ない|無い|なし|無し)(?:という)?(?:わけ|訳)(?:では|じゃ)|(?:なく|無く)は(?:ない|無い|ありません)|(?:ない|無い|なし|無し)とは(?:言え|いえ|言い切れ|限り)/;
  function isNegated(seg) { return OPT_NEG_RE.test(seg) && !DOUBLE_NEG_RE.test(seg); }
  function opticalWords(text) {
    const out = [];
    for (const m of String(text || "").matchAll(OPTICAL_RE)) {
      const w = m[1], rest = text.slice(m.index, m.index + 60);
      const kv = rest.slice(w.length).match(/^\s*[：:]\s*([^\s　、。|｜]+)/);
      if (kv) { if (/^(なし|無し|ナシ|無|ない|-|ー|×)/.test(kv[1])) continue; }
      else {
        const cut = rest.search(/[。\n】|｜]/);
        if (isNegated(cut < 0 ? rest : rest.slice(0, cut))) continue;
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

  // @core-end

  // ------------------------------------------------------------------ 安心ストア（フォロー中のストア）
  // DATA.trusted（Mac の data/trusted_sellers.csv）＋このブラウザでの追加・削除（LS_TRUST: {id: {name, on}}）
  const LS_TRUST = "yhkTrustV1";
  function trustedList() {
    const m = new Map((DATA.trusted || []).map((t) => [t.id, t.name || ""]));
    for (const [id, v] of Object.entries(load(LS_TRUST, {}))) { if (v.on) m.set(id, v.name || m.get(id) || ""); else m.delete(id); }
    return m;
  }
  const trustedIds = () => new Set(trustedList().keys());
  // ---- 入札記録: ［入札した］で記録 → ダウンロード/aurora_bids.csv（全件）→ Mac の data/bid_log.csv。
  //      毎朝 update_all.py が終わった出品の最終価格を見て、落札率・負けた差・利益が出る落札の率をまとめる
  const LS_BIDS = "yhkBidsV1";
  const BID_FIELDS = ["auction_id", "title", "max_bid", "pay_max", "usd", "profit_at_max", "end", "trusted", "tentative", "store_tax", "ship", "bid_at"];
  function bidCsv() {
    const q = (v) => '"' + String(v ?? "").replace(/"/g, '""') + '"';
    return BID_FIELDS.join(",") + "\n" + Object.values(load(LS_BIDS, {})).map((b) => BID_FIELDS.map((f) => q(b[f])).join(",")).join("\n");
  }
  function toggleBid(info) {
    const all = load(LS_BIDS, {});
    if (all[info.auction_id]) delete all[info.auction_id];
    else all[info.auction_id] = Object.assign({}, info, { bid_at: new Date().toISOString() });
    save(LS_BIDS, all);
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id)
      try { chrome.runtime.sendMessage({ type: "saveFile", name: "aurora_bids.csv", csv: bidCsv() }, () => void chrome.runtime.lastError); } catch (e) {}
  }
  const bidOf = (id) => load(LS_BIDS, {})[id];
  // 行の［入札した］ボタン。押した後は「入札済み」（もう一度押すと取り消し）
  function bidButton(i, c, usd, tentative) {
    if (!c || !c.maxBid) return "";
    const tm = i.tax ? 1 + DATA.cfg.storeTaxPct / 100 : 1;
    const at = calculate(Object.assign({}, i, { price: Math.round(c.maxBid * tm) }), usd, state.fx);
    state.bidInfo[i.id] = { auction_id: i.id, title: i.title, max_bid: c.maxBid, pay_max: Math.round(c.maxBid * tm), usd,
      profit_at_max: at.profit, end: i.end, trusted: i.trusted ? 1 : 0, tentative: tentative ? 1 : 0, store_tax: i.tax ? 1 : 0, ship: i.ship ?? "" };
    const done = bidOf(i.id);
    return `<div><button data-a="bid" data-id="${esc(i.id)}" class="${done ? "bidon" : ""}" title="上限額で入札（予約）したら押す。オークファンの予約一覧を開けば自動で記録されます">${done ? "✓入札済み" : "入札した"}</button></div>`;
  }

  function setTrusted(id, name, on) {
    const t = load(LS_TRUST, {}); t[id] = { name, on }; save(LS_TRUST, t);
    saveTrustedFile();
  }
  // ヤフオクの「フォロー中」の一覧（/follow/list）: 並んでいる出品者（/seller/<id> へのリンク）をまとめて安心ストアに。
  // 自分で外したストアは戻さない。登録した名前は画面右上に出す（違うものが入っていたらストアのページで外せる）
  function trustFollowList() {
    const t = load(LS_TRUST, {}), have = trustedIds(), found = new Map();
    for (const a of document.querySelectorAll('a[href*="/seller/"]')) {
      if (a.closest("#yhk, #yhk-follow")) continue;
      const m = (a.getAttribute("href") || "").match(/\/seller\/([A-Za-z0-9_\-]+)/);
      if (!m) continue;
      const name = (a.textContent || "").replace(/\s+/g, " ").trim() || (a.querySelector("img") || {}).alt || "";
      if (!found.has(m[1]) || (!found.get(m[1]) && name)) found.set(m[1], name.slice(0, 60));
    }
    const added = state.followAdded || (state.followAdded = new Map());  // このページで新しく登録した分（何回見直しても数え直さない）
    let n = 0, fresh = false;
    for (const [id, name] of found) {
      if (t[id] && t[id].on === false) continue;  // 自分で外したストア
      n++;
      if (!have.has(id)) { added.set(id, name || id); fresh = true; }
      t[id] = { name: name || (t[id] && t[id].name) || "", on: true };
    }
    if (!n) return 0;
    save(LS_TRUST, t);
    if (fresh) saveTrustedFile();
    let b = document.getElementById("yhk-follow");
    if (!b) {
      b = document.createElement("div"); b.id = "yhk-follow";
      b.style.cssText = "position:fixed;top:12px;right:12px;z-index:2147483647;max-width:420px;max-height:60vh;overflow:auto;padding:14px 16px;" +
        "border-radius:12px;background:#1f5c3a;color:#fff;font:14px/1.5 -apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;box-shadow:0 8px 30px #0005";
      document.body.appendChild(b);
    }
    b.innerHTML = `<b>Aurora</b>: このページの出品者 ${n}件を★安心ストアにしました（新しく ${added.size}件・全部で ${trustedIds().size}件）。` +
      `次のページがあれば開いてください。` +
      (added.size ? `<div style="font-size:12px;margin-top:6px;opacity:.9">新しく登録: ${[...added.values()].map(esc).join("・")}</div>` : "");
    return n;
  }
  function saveTrustedFile() {
    const q = (s) => '"' + String(s).replace(/"/g, '""') + '"';
    const csv = "user_id,name,on\n" + [...trustedList()].map(([i, n]) => `${q(i)},${q(n)},1`).join("\n");
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id)  // Mac の data/trusted_sellers.csv へ（update_all.py が取り込む）
      try { chrome.runtime.sendMessage({ type: "saveFile", name: "aurora_trusted_sellers.csv", csv }, () => void chrome.runtime.lastError); } catch (e) {}
  }
  // ストアのページ（/seller/<id>）: 「フォロー中」なら自動で安心ストアに（自分で外したストアは自動では戻さない）
  const pageSeller = () => { const m = location.pathname.match(/^\/seller\/([^/?#]+)/); return m ? m[1] : ""; };
  function autoTrustFollowed() {
    const id = pageSeller();
    if (!id || id in load(LS_TRUST, {}) || trustedIds().has(id)) return false;
    const followed = [...document.querySelectorAll("button, a, span")].some((e) => e.childElementCount === 0 && e.textContent.trim() === "フォロー中");
    if (!followed) return false;
    const name = (document.title || "").replace(/\s*[-|｜].*$/, "").trim();
    setTrusted(id, name, true);
    return true;
  }

  // ------------------------------------------------------------------ 相場メモ（このブラウザに保存）
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  // 相場メモ: { 機種名: {usd, cond} }（以前の形 { 機種名: usd } も読める）。状態は source の括弧に入れる（相場メモ(MINT)）
  const memoVal = (v) => (v && typeof v === "object" ? { usd: Number(v.usd), cond: v.cond || "" } : { usd: Number(v), cond: "" });
  // 2026-11-15: 今までの相場メモは sold の商品代（送料別）で入れていたのに、送料込みとして扱っていた（$40 安く計算）。
  // 1回だけ送料を足して送料込みに直し、Mac（data/price_memo.csv）にも保存し直す。戻り値: 直した件数
  // 確認済みのチェック（行をグレーにするだけ。消さない）。14日より古い記録は捨てる
  function seenAll() {
    const m = load(LS_SEEN, {}), old = Date.now() - 14 * 864e5;
    for (const k of Object.keys(m)) if (m[k] < old) delete m[k];
    return m;
  }
  const seenOf = (id) => !!load(LS_SEEN, {})[id];
  // ［直す］で選んだ状態＝この出品の状態（出品ごと。14日で忘れる）。{id: {cond, t}}
  const LS_LCOND = "yhkListCondV1";
  function listCondRank(id) {
    const v = id && load(LS_LCOND, {})[id];
    const r = v && (C.condRank || {})[v.cond];
    return r != null ? r : null;
  }
  function setListCond(id, cond) {
    const m = load(LS_LCOND, {}), old = Date.now() - 14 * 864e5;
    for (const k of Object.keys(m)) if (m[k].t < old) delete m[k];
    if (cond && (C.condRank || {})[cond] != null) m[id] = { cond, t: Date.now() }; else delete m[id];
    save(LS_LCOND, m);
  }
  const seenBox = (id) => `<label class="seen-box" title="確認したらチェック（グレーになります。もう一度押すと戻ります）"><input type="checkbox" data-seen="${esc(id)}"${seenOf(id) ? " checked" : ""}>確認済み</label>`;
  function migrateMemo() {
    try {
      if (localStorage.getItem(LS_MEMO) != null) return 0;
      const old = JSON.parse(localStorage.getItem(LS_MEMO_OLD) || "null");
      const out = {};
      for (const [k, v] of Object.entries(old || {})) {
        const m = memoVal(v);
        if (m.usd > 0) out[k] = { usd: Math.round((m.usd + (DATA.cfg.deductionUsd || 0)) * 100) / 100, cond: m.cond };
      }
      save(LS_MEMO, out);
      return Object.keys(out).length;
    } catch (e) { return 0; }
  }
  const memoSource = (cond) => (cond ? `相場メモ(${cond})` : "相場メモ");
  // 相場データ＋このブラウザの相場メモ。同じ機種の相場メモが Mac 側（data/price_memo.csv）にもあれば、新しいこちらだけを使う
  // （［直す］し直す前の古い登録が残り、2つの真ん中で計算されていた: $150 Near MINT と古い Exc+5 $150 → $155）
  function allRefs() {
    const mine = memoRefs(), keys = new Set(mine.map((r) => r.title.toLowerCase()));
    return DATA.refs.filter((r) => !(isMemo(r) && keys.has(String(r.title || "").toLowerCase()))).concat(mine);
  }
  function memoRefs() {
    return Object.entries(load(LS_MEMO, {})).map(([title, v]) => {
      const m = memoVal(v);
      return { title, price: m.usd, tier: 0, source: memoSource(m.cond), tokens: [...tokensOf(title)], acc: false,
        local: true, t: (v && typeof v === "object" && v.t) || 0 };
    });
  }
  // ［直す］［登録］の入力欄（金額＋状態）
  // ［直す］の状態（2026-11-15 ユーザー指定: Exc+5 より下は使わない。1ランク $10）
  const CONDS = ["Unused", "TOP MINT", "MINT", "Almost MINT", "Near MINT", "Exc+5"];
  function memoInputs(label, attrs, btn) {
    const mv = memoVal(load(LS_MEMO, {})[label]), cur = mv.cond;
    if (mv.usd) attrs = attrs.replace(/value="[^"]*"/, "").replace(/class="sug"/, "") + ` value="${netOf(mv.usd)}"`;  // 登録した額（送料別）を残して見せる
    return `${mv.usd ? `<span class="okb" title="この機種の相場は登録済み（送料別 $${netOf(mv.usd)}${cur ? "・" + esc(cur) : ""}）。直すときは金額を変えて［登録］">登録済み</span> ` : ""}<input data-memo="${esc(label)}" ${attrs}><select data-cond="${esc(label)}" title="状態（売れた物の状態）"><option value="">状態</option>${
      CONDS.map((c) => `<option${c === cur ? " selected" : ""}>${esc(c)}</option>`).join("")}</select><button data-a="memo" data-k="${esc(label)}">${btn}</button>`;
  }

  // ------------------------------------------------------------------ ページの出品データ
  function listingFromNextData(json) {
    const stack = [json];
    while (stack.length) {
      const d = stack.pop();
      if (d && typeof d === "object") {
        if (Array.isArray(d.items) && d.items.length && d.items[0] && d.items[0].auctionId) return d;
        for (const v of Object.values(d)) if (v && typeof v === "object") stack.push(v);
      }
    }
    return null;
  }

  function parseItems(json) {
    const l = listingFromNextData(json);
    if (!l) return [];
    // 同じオークションが一覧に2回入っていることがある（PR枠など）→ 1件にする（件数が2になり［全て開く］で同じページが2つ開いていた。2026-11-19）
    return l.items.filter((x, i, a) => !x.isFleamarketItem && (!x.auctionId || a.findIndex((y) => y.auctionId === x.auctionId) === i)).map((x) => ({
      id: x.auctionId, title: x.title || "", price: x.price ?? null, buyNow: x.buyNowPrice || null,
      bids: x.bidCount ?? 0, end: x.endTime || "", ship: x.isFreeShipping ? 0 : (x.currentPriceShippingFee ?? null),
      watch: x.watchCount ?? 0, store: !!(x.seller && x.seller.isStore), tax: x.taxFlag === 1,
      sellerId: String((x.seller && x.seller.userId) || ""), sellerName: String((x.seller && x.seller.displayName) || ""),
    }));
  }

  function totalFromJson(json) {
    const m = JSON.stringify(json).match(/"totalResultsAvailable":(\d+)/);
    return m ? Number(m[1]) : null;
  }

  // robots.txt（https://auctions.yahoo.co.jp/robots.txt）で Disallow の引数（n=・s1=・o1=・select= など）を外した URL（2026-11-19 ユーザー決定）。
  // 規則は Mac の robots.py が拡張機能に入れる（DATA.robotsParams = [パスの正規表現, 引数の末尾]）。引数は「名前=」がその末尾で終われば外す
  // （robots.txt の文字の当たり方と同じ。robots.safe_url と同じ結果）。ユーザーが開いているページそのものには使わない（続きのページを読むときだけ）
  const ROBOTS_FALLBACK = [["^/", "n="], ["^/", "s1="], ["^/", "o1="], ["^/", "select="], ["^/", "mode="]];
  function robotsSafeUrl(href) {
    const u = new URL(href);
    if (u.hostname !== "auctions.yahoo.co.jp") return href;
    const rules = (DATA.robotsParams && DATA.robotsParams.length ? DATA.robotsParams : ROBOTS_FALLBACK).map(([p, t]) => [new RegExp(p), t]);
    for (const k of [...new Set(u.searchParams.keys())]) {
      if (rules.some(([re, t]) => re.test(u.pathname) && (k + "=").endsWith(t))) u.searchParams.delete(k);
    }
    return u.toString();
  }
  const withoutB = (href) => { const u = new URL(href); u.searchParams.delete("b"); return u.toString(); };

  async function fetchPage(start) {
    const u = new URL(robotsSafeUrl(location.href));  // 1ページの件数（n=）・並べ替えは付けない（並べ替えは読んだ後に拡張機能の中で）
    u.searchParams.set("b", String(start));
    const html = await (await fetch(u.toString(), { credentials: "include" })).text();
    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    return m ? JSON.parse(m[1]) : null;
  }

  // ------------------------------------------------------------------ 鑑定

  function evaluate(items, fx) {
    const refs = allRefs();
    const memoPool = refs.filter(isMemo), otherPool = refs.filter((r) => !isMemo(r));
    const trusted = trustedIds();
    for (const it of items) {
      it.trusted = !!it.sellerId && trusted.has(it.sellerId);
      it.flags = detectFlags(it.title);
      it.query = titleToQuery(it.title);
      it.eq = [queryForTitle(it.title, it.query)].concat(siblingExcludes(it.query, refs)).join(" ");  // eBay のリンク用（型と除外語・紛らわしい兄弟の機種を外す語入り）
      it.setParts = [];
      if (it.flags.includes("セット品") || KIT_RE.test(it.title)) {  // まとめ売り・レンズキットは中身ごとに
        for (const seg of setPartTitles(it.title)) {
          const ms = matchPreferMemo(titleToQuery(seg) + " " + seg, memoPool, otherPool);
          if (ms.length) it.setParts.push({ label: seg, usd: Math.round(median(ms.map((r) => r.price)) * 100) / 100, ref: ms[0] });
        }
        if (it.setParts.length >= 2) {
          it.flags = it.flags.filter((f) => f !== "付属品のみ?");
          it.refs = [it.setParts[0].ref];
          it.usd = setValue(it.setParts, fx);
          if (it.ship0 === undefined) it.ship0 = it.ship;
          it.calc = calculate(it, it.usd, fx);
          [it.mark, it.reason] = judge(it);
          continue;
        }
        it.setParts = [];
      }
      let m = matchPreferMemo(it.query + " " + it.title, memoPool, otherPool);
      if (!m.length) m = matchKantei(it.title);
      it.refs = m;
      it.lrank = listCondRank(it.id);  // ［直す］で指定したこの出品の状態（無ければタイトルから）
      it.usd = m.length ? Math.round(median(conditionAdjusted(m, it.title, it.flags, it.lrank)) * 100) / 100 : null;
      // ジャンクの割引（config の junk_price_factor。2026-11-16 から 1.0＝割引なし）。［直す］でこの出品の状態と相場を指定したときは、その額をそのまま使う
      if (it.usd != null && it.flags.includes("ジャンク") && DATA.cfg.junkFactor < 1 && it.lrank == null)
        it.usd = Math.round(it.usd * DATA.cfg.junkFactor * 100) / 100;
      // 送料: 検索結果に無ければ出品ページの送料（読めていれば）。どちらも無ければ calculate が既定の額（1,000円）で計算
      if (it.ship0 === undefined) it.ship0 = it.ship;
      const pd = state.desc && state.desc[it.id];
      it.ship = it.ship0 != null ? it.ship0 : (pd && pd.ship != null ? pd.ship : null);
      it.shipSrc = it.ship0 == null && it.ship != null ? "page" : "";
      it.calc = it.usd != null ? calculate(it, it.usd, fx) : null;
      [it.mark, it.reason] = judge(it);
    }
    return items;
  }

  // ------------------------------------------------------------------ 表示
  const yen = (v) => (v == null ? "-" : v.toLocaleString("ja-JP") + "円");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // eBay の$は「送料を除いた商品代（net）」で表示・入力する（2026-11-15 ユーザー指定）。相場データと計算は送料込みのまま
  const SHIP = () => DATA.cfg.deductionUsd || 0;
  const netOf = (g) => (g == null || isNaN(g) ? null : Math.round((Number(g) - SHIP()) * 100) / 100);
  const nd = (g) => { const v = netOf(g); return v == null ? "-" : "$" + (Number.isInteger(v) ? v : v.toFixed(2)); };
  const endStr = (e) => { if (!e) return "-"; const d = new Date(e); return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
  const remain = (e) => {
    if (!e) return ["-", false];
    const ms = new Date(e) - Date.now();
    if (ms <= 0) return ["終了", false];
    const h = ms / 3600000;
    if (h < 1) return [`あと${Math.max(1, Math.round(ms / 60000))}分`, true];
    if (h < 24) return [`あと${Math.floor(h)}時間`, h < 3];
    return [endStr(e), false];
  };
  const srcLabel = (r) => (r.source || "").replace(/\(.*$/, "");
  // まとめ売りの内訳（中身ごとの相場・送料別）。2点目からは送料・1注文の手数料・梱包を引いて合計している
  const setLine = (i) => (i.setParts && i.setParts.length >= 2
    ? `まとめ売り ${i.setParts.length}点: ` + i.setParts.map((p) => `${esc(p.label)} ${nd(p.usd)}`).join(" ＋ ") +
      `（相場が分かった中身だけ。2点目からは送料$${SHIP()}・手数料・梱包を引いて合計）`
    : "");
  // 売れ筋＝根拠の販売実績で利益が出ていた機種（build_extension.py が appraise.sold_hit で付ける）。$200未満は下に回す（2026-11-18・11-20）
  const SORT_DOWN_USD = 200;  // 売値（送料込み）$200 未満は下に回す（2026-11-20 ユーザー決定。appraise.SORT_DOWN_USD）
  const soldHit = (i) => !!(i.refs && i.refs[0] && i.refs[0].hit && !isMemo(i.refs[0]));
  // 勝ちやすさ（Mac の win_score.py → data/win_score.csv。機種の比較用キーごと）: ヤフオクの終わった出品のうち、今の上限入札額以下で終わった割合。
  // 1割未満の機種は入札候補の下に回す（appraise.bid_priority と同じ。2026-11-19 ユーザー決定。判定・上限額は変えない）。記録が無い機種は今の並び・表示なし
  const winOf = (i) => {
    if (!i || !i.title || (i.setParts && i.setParts.length >= 2)) return null;
    if (i.wkey === undefined) i.wkey = modelKey(i.title)[1];
    return (DATA.winScore || {})[i.wkey] || null;
  };
  const isLowWin = (i) => { const w = winOf(i); return !!w && w.rate < (DATA.cfg.winLowRate ?? 0.1); };
  const bidPrio = (i) => (i.usd != null && i.usd < SORT_DOWN_USD ? 2 : 0) + (soldHit(i) ? 0 : 1) + (isLowWin(i) ? 4 : 0);
  // 表示: 「勝ちやすさ 46%（直近12件）」（appraise.win_label と同じ）
  const winText = (i) => { const w = winOf(i); return w && w.n ? `勝ちやすさ ${Math.round(w.rate * 100)}%（直近${w.n}件）` : ""; };
  const winLine = (i) => {
    const t = winText(i);
    if (!t) return "";
    const low = isLowWin(i);
    return `<div class="s" title="${esc(`ヤフオクの終わった出品のうち、今の上限入札額以下で終わった割合（Mac が毎朝 終わった出品の検索で集計）。${low ? "1割未満なので入札候補の下に回しています。" : ""}判定・上限額は変えていません`)}">${t}${low ? "・下に" : ""}</div>`;
  };
  // 根拠が販売履歴（自分・Chatwork・頒布会）なら、元の売値も並べる（自分の分は eBayStock・取引明細の合計と同じ）。
  // Chatwork は報告額（送料$30込み）に戻して出す（appraise.chatwork_gross の逆。2026-11-18）。相場メモは出さない
  const money = (v) => "$" + Number(v).toFixed(2).replace(/\.00$/, "");
  const soldLine = (r) => {
    if (!r || r.price == null || isMemo(r) || !/販売実績|^頒布会/.test(r.source || "")) return "";
    if (/^Chatwork/.test(r.source || "")) {
      const cw = Number(DATA.cfg.chatworkShipUsd || 30);
      return `<div class="s" title="Chatwork の報告額（スクールの方針で送料$${cw}込み）。商品代＝報告額−$${cw}。うちは送料$${SHIP()}で出品する前提で計算">報告 ${money(r.price - SHIP() + cw)}（送料$${cw}込み）</div>`;
    }
    return `<div class="s" title="根拠の実際の売値（送料込み）。eBayStock・eBayの取引明細の合計と同じ。上の送料別は、ここから送料$${SHIP()}を引いた額">売値 ${money(r.price)}（送料込み）</div>`;
  };

const css = `
#yhk,#akit{position:fixed;top:8px;right:8px;z-index:2147483647;width:min(1100px,calc(100vw - 16px));max-height:calc(100vh - 16px);overflow:auto;
background:#fff;color:#14263a;border-radius:14px;box-shadow:0 10px 40px #0004;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif}#yhk.min,#akit.min{width:auto}#yhk.min .bd,#akit.min .bd{display:none}#yhk .hd,#akit .hd{position:sticky;top:0;background:#0b1728;color:#fff;padding:9px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}#yhk .hd b,#akit .hd b{font-size:14px}#yhk .hd .sp,#akit .hd .sp{flex:1}#yhk button,#akit button{border:0;border-radius:8px;padding:6px 10px;background:#e8f2f8;color:#14415a;font-weight:700;cursor:pointer;font-size:12px}#yhk .hd button,#akit .hd button{background:#24496a;color:#fff}#yhk .bd,#akit .bd{padding:10px 12px}#yhk table,#akit table{width:100%;border-collapse:collapse}#yhk th,#yhk td,#akit th,#akit td{padding:5px 6px;border-bottom:1px solid #e6ecf2;text-align:left;vertical-align:top}#yhk button.bidon,#akit button.bidon{background:#1f7a5c;color:#fff}#yhk tr.seen td,#akit tr.seen td{opacity:.38}#yhk tr.seen td.m,#akit tr.seen td.m{opacity:1}#yhk tr.seen td.m>*:not(.seen-box),#akit tr.seen td.m>*:not(.seen-box){opacity:.38}#yhk label.seen-box,#akit label.seen-box{display:block;width:max-content;margin:0 0 4px;cursor:pointer;font-size:10px;color:#9fb3c8;white-space:nowrap}#yhk label.seen-box input,#akit label.seen-box input{width:16px;height:16px;vertical-align:middle;margin:0 3px 0 0;cursor:pointer}#yhk .pinmove,#akit .pinmove{margin-top:4px;padding:4px 8px;border-radius:6px;background:#fff3cd;color:#6b4e00;font-size:12px;font-weight:700}
@keyframes yhkflash{0%,100%{background:transparent}30%{background:#ffe08a}}#yhk tr.flash td,#akit tr.flash td{animation:yhkflash 1.2s ease 2}#yhk-toast,#akit-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:2147483647;background:#0b1728;color:#fff;padding:10px 16px;border-radius:10px;
font:14px/1.4 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif;box-shadow:0 6px 24px #0005;opacity:0;pointer-events:none;transition:opacity .2s}#yhk-toast.show,#akit-toast.show{opacity:1}#yhk .trustb,#akit .trustb{color:#d4a017;font-weight:900;margin-right:3px}#yhk .tsep.trust,#akit .tsep.trust{background:#e9f6ee;color:#1f5c3a}#yhk .tsep.chal,#akit .tsep.chal{background:#eef3f8;color:#2d4a63}#yhk .tmark,#akit .tmark{display:inline-block;font-size:10px;background:#8a6514;color:#fff;border-radius:4px;padding:0 4px;margin-right:2px;vertical-align:middle}#yhk tr.tent td,#akit tr.tent td{background:#fffaf0}#yhk .tsep,#akit .tsep{background:#fff3d6;color:#6b4e0e;font-size:12px;padding:6px 8px}#yhk .fix,#akit .fix{margin-top:4px;max-width:140px;margin-left:auto}#yhk .fix input,#akit .fix input{width:50px;padding:2px 3px;font-size:12px}#yhk .fix select,#akit .fix select{font-size:11px;padding:1px;margin-left:2px;max-width:62px}#yhk .fix button,#akit .fix button{padding:3px 6px;margin-left:2px}#yhk th,#akit th{font-size:11px;color:#5d7083;font-weight:600}#yhk td:nth-child(2),#akit td:nth-child(2){min-width:240px}#yhk td.n,#akit td.n{text-align:right;white-space:nowrap}#yhk input.sug,#akit input.sug{color:#8a6d1f;font-style:italic}#yhk a,#akit a{color:#0b5d8c;text-decoration:none}#yhk .m,#akit .m{font-size:15px;font-weight:800}#yhk .g,#akit .g{color:#1f7a5c}#yhk .o,#akit .o{color:#1b6387}#yhk .x,#akit .x{color:#a4473a}#yhk .instant-tag,#akit .instant-tag{display:inline-block;font-size:11px;font-weight:800;color:#fff;background:#1f7a5c;border-radius:5px;padding:1px 6px;margin-bottom:3px;white-space:nowrap}#yhk .trend,#akit .trend{font-weight:800;font-size:13px;white-space:nowrap}#yhk .s,#akit .s{font-size:11px;color:#6a7c8e}#yhk details,#akit details{margin-top:10px}#yhk summary,#akit summary{cursor:pointer;font-weight:700}#yhk input,#akit input{width:72px;padding:4px;border:1px solid #c6d3de;border-radius:6px}#yhk a.t,#akit a.t{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-weight:600}#yhk a.usd,#akit a.usd{font-size:15px;font-weight:800;text-decoration:underline dotted}#yhk a.now,#yhk a.pill,#akit a.now,#akit a.pill{display:inline-block;margin-top:3px;font-size:11px;font-weight:700;padding:1px 7px;border:1px solid #9fc0d4;border-radius:6px}#yhk .okb,#akit .okb{display:inline-block;font-size:10px;font-weight:700;color:#1f6b50;background:#e3f4ec;border-radius:5px;padding:0 5px}#yhk .pills,#akit .pills{display:flex;gap:4px;justify-content:flex-end}#yhk button.open3,#akit button.open3{margin-top:3px;font-size:11px;padding:2px 8px}#yhk a.pill.sold,#akit a.pill.sold{background:#1b6387;color:#fff;border-color:#1b6387}#yhk td.m,#akit td.m{white-space:nowrap;width:58px}#yhk td.um,#akit td.um{white-space:normal;min-width:260px}#yhk .setpart,#akit .setpart{margin-top:6px;padding:6px 8px;border-left:3px solid #9fc0d4;background:#f4f8fb;border-radius:6px}#yhk .setpart b,#akit .setpart b{font-size:13px;color:#14263a}#yhk .fix.fixl,#akit .fix.fixl{margin-left:0;max-width:none;margin-top:6px}#yhk .fix.fixl input,#akit .fix.fixl input{width:64px}#yhk tr.soon td,#akit tr.soon td{background:#fff6ec}#yhk .hot,#akit .hot{color:#c0392b;font-weight:700}#yhk table.hist{width:auto;font-size:11px;border:1px solid #e6ecf2;border-radius:6px}#yhk table.hist td,#yhk table.hist th{padding:2px 8px;white-space:nowrap;min-width:0}#yhk .flag.sweet{background:#e3f4ec;color:#1f6b50;font-weight:700}#yhk .flag,#akit .flag{background:#fff3d6;color:#7a5a12;border-radius:6px;padding:0 5px;font-size:11px;margin-left:4px}
.yhk-badge{display:inline-block;margin:2px 0;padding:2px 7px;border-radius:7px;font:700 12px -apple-system,sans-serif;color:#fff}
.yhk-badge.g{background:#1f7a5c}.yhk-badge.o{background:#1b6387}.yhk-badge.x{background:#9aa6b2}
@media (prefers-color-scheme:dark){#yhk tr.soon td,#akit tr.soon td{background:#2a2216}#yhk tr.tent td,#akit tr.tent td{background:#1f2129}#yhk .tsep,#akit .tsep{background:#3a2f17;color:#f2d79a}#yhk .tsep.trust,#akit .tsep.trust{background:#173a28;color:#a8e6c1}#yhk .tsep.chal,#akit .tsep.chal{background:#1c2c3c;color:#b9d3ea}#yhk,#akit{background:#152231;color:#e2eaf2}#yhk th,#yhk td,#akit th,#akit td{border-color:#26394b}#yhk a,#akit a{color:#7cc4ee}#yhk button,#akit button{background:#24384c;color:#d6e6f2}#yhk input,#akit input{background:#0f1a26;color:#e2eaf2;border-color:#36506a}#yhk .setpart,#akit .setpart{background:#1b2c3e;border-left-color:#7cc4ee}#yhk .setpart b,#akit .setpart b{color:#f2f6fa}#yhk select,#akit select{background:#0f1a26;color:#e2eaf2;border:1px solid #36506a}#yhk .okb,#akit .okb{background:#173a28;color:#a8e6c1}}
#akit tr.kdim td{opacity:.55}
`;

  let state = { bidInfo: {}, items: [], total: null, fx: Number(load(LS_FX, DATA.cfg.fx)) || DATA.cfg.fx, loading: "", browse: {} };

  // ------------------------------------------------------------------ eBay出品中の相場（公式 Browse API・裏方の background.js 経由）
  const canBrowse = () => DATA.cfg.browse && typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id;
  // 仮の相場（送料込$）: eBay出品中の最安値（送料無料の出品は出品額−$30＝background.js で計算済み）。件数が少なければ出さない
  // 2026-11-16 ユーザー決定（tentative.py の suggest_usd と同じ）
  function suggestUsd(b) {
    if (!b || !b.med || b.n < DATA.cfg.activeMinCount) return null;
    // b.min は送料込み。送料を引いた商品代（画面の額）が 0 以下・下限以下なら案にしない（以前は送料込みで 0 と比べ「案 $-1」が出た。tentative.suggest_usd と同じ）
    const sg = Math.floor(b.min);
    return netOf(sg) > 0 && netOf(sg) >= (DATA.cfg.suggestMinNetUsd || 0) ? sg : null;  // 送料別 $20 未満は案にしない（2026-11-19 ユーザー決定）
  }
  // 落札見込み（bid_results.py の win_odds と同じ規則）: 上限額の価格帯 × ストア/個人の過去の勝率。3件未満なら価格帯だけ、それも3件未満なら出さない
  function winOdds(maxBid, store) {
    const st = DATA.winStats || {};
    const band = maxBid < 10000 ? "1万円未満" : maxBid < 30000 ? "1〜3万円" : "3万円以上";
    for (const k of [`${band}|${store ? "1" : "0"}`, `${band}|*`]) {
      const [won, n] = st[k] || [0, 0];
      if (n >= 3) { const rate = won / n; return { label: rate >= 0.6 ? "高" : rate >= 0.3 ? "中" : "低", won, n, key: k }; }
    }
    return null;
  }
  // ヤフオクの落札相場（Mac の data/yahoo_results.csv → yahoo_results.model_stats。機種の比較用キーごと）。
  // 候補に出た出品が実際にいくらで落札されたか。表示だけ（判定・上限額には使わない）。記録が無ければ何も出さない
  // 注意を出す出品者（config.toml [caution]。2026-11-19 ユーザー決定「注意書きだけ出す」）。判定・上限額・並びは変えない
  function cautionLine(i) {
    const c = DATA.caution || {}, name = String(i.sellerName || "");
    return name && (c.sellers || []).some((w) => w && name.includes(w)) ? `<div class="s x" title="${esc(name)}">⚠ ${esc(c.note || "注意する出品者")}</div>` : "";
  }
  function yahooLine(key) {
    const y = key && (DATA.yahooStats || {})[key];
    if (!y || !y.n) return "";
    const days = DATA.cfg.yahooDays || 90;
    const tip = `ヤフオクで実際に落札された額（ストアは税抜。毎朝 Mac が終わった出品ページで記録）\n機種: ${y.label} ・ 直近${days}日 ${y.n}件 ・ ${yen(y.min)}〜${yen(y.max)}${y.unsold ? `\n入札なしで終わった出品 ${y.unsold}件` : ""}\n判定・上限額には使っていません`;
    return `<div class="s yres" title="${esc(tip)}">ヤフオク落札 中央値 ${yen(y.med)}（${y.n}件・直近${days}日${y.n > 1 ? `・${yen(y.min)}〜${yen(y.max)}` : ""}）</div>`;
  }
  // ---- 落札相場の見張り: ◎○・仮◎○・確認してから に出た出品 → ダウンロード/aurora_seen.csv（全件）→ Mac の yahoo_results.py が
  //      終了後に出品ページで最終価格を記録する。1日に足すのは DATA.cfg.seenMaxPerDay 件まで。終了から7日たった分は消す
  const LS_WATCH = "yhkWatchV1";
  const WATCH_FIELDS = ["auction_id", "title", "end", "mark", "max_bid", "store_tax", "seen_at"];
  function watchAdd(list, now) {
    now = now || new Date();
    const all = load(LS_WATCH, {}), day = now.toISOString().slice(0, 10), old = now.getTime() - 7 * 86400000;
    let pruned = 0;
    for (const [id, w] of Object.entries(all)) { const t = Date.parse(w.end); if (t && t < old) { delete all[id]; pruned++; } }
    const cap = DATA.cfg.seenMaxPerDay ?? 300;
    let today = Object.values(all).filter((w) => String(w.seen_at).slice(0, 10) === day).length, added = 0;
    for (const x of list) {
      if (!x.i || !x.i.id || all[x.i.id] || today >= cap) continue;
      all[x.i.id] = { auction_id: x.i.id, title: x.i.title, end: x.i.end || "", mark: x.mark, max_bid: x.maxBid ?? "",
        store_tax: x.i.tax ? 1 : 0, seen_at: now.toISOString() };
      today++; added++;
    }
    if (added || pruned) save(LS_WATCH, all);
    return added;
  }
  function watchCsv() {
    const q = (v) => '"' + String(v ?? "").replace(/"/g, '""') + '"';
    return WATCH_FIELDS.join(",") + "\n" + Object.values(load(LS_WATCH, {})).map((w) => WATCH_FIELDS.map((f) => q(w[f])).join(",")).join("\n");
  }
  function browseCell(label) {
    const b = state.browse[label];
    if (!b) return '<span class="s">…</span>';
    if (b.error) return `<span class="s" title="${esc(b.error)}">取得できず</span>`;
    if (!b.n) return '<span class="s">出品なし</span>';
    const sg = suggestUsd(b);
    return `中央値 ${nd(b.med)}<div class="s">最安 ${nd(b.min)}・${b.n}件${b.jp ? `（日本から${b.jp}）` : ""}・送料別</div>` +
      (sg ? `<div class="s">案 ${nd(sg)}（最安値）</div>` : `<div class="s">件数が少なく案なし</div>`);
  }
  let browsing = false;
  async function fetchBrowse(labels, after) {
    if (!canBrowse() || browsing) return;
    browsing = true;
    let fresh = 0;
    try {
      for (const label of labels) {
        if (state.browse[label]) continue;
        fresh++;
        const res = await new Promise((ok) => {
          try {
            const vt = variantOf(tokensOf(label));
            chrome.runtime.sendMessage({ type: "browse", q: label, must: vt ? vt.must : [], not: vt ? vt.not : [] },
              (r) => ok(r || { error: (chrome.runtime.lastError || {}).message || "応答なし" }));
          } catch (e) { ok({ error: String(e.message || e) }); }
        });
        state.browse[label] = res;
        const td = document.querySelector(`#yhk td[data-browse="${CSS.escape(label)}"]`);
        if (td) td.innerHTML = browseCell(label);
        const inp = document.querySelector(`#yhk input[data-memo="${CSS.escape(label)}"]`);
        const sg = suggestUsd(res);
        if (inp && sg && !inp.value) { inp.value = netOf(sg); inp.classList.add("sug"); }  // 入力欄は送料別（以前は送料込みのまま入れ、［登録］で $40 多く保存された）
      }
    } finally {
      browsing = false;
      if (fresh) (after || render)();  // sold確認待ちの一覧を作り直す
    }
  }

  // ------------------------------------------------------------------ 説明文の状態チェック（◎○の出品だけ自動。出品ページを1件ずつ間隔を空けて読む）
  const LS_DESC = "yhkDescV3";  // 判定の語を変えたら番号を上げて、保存済みの結果を読み直させる
  // 動作の不良の語と、同じ文にあれば決まり文句（「動作しない場合は返品可」）とみなす語（appraise.py の MECH_PAT と同じ）
  const MECH_PAT = String.raw`(?<!途)(?:回|動き|動か|切れ|降り|下り|作動し|動作し|通電し|戻|開き|開か|閉じ|閉ま)(?:りません|ません|らない|ない|らず|ず)|開きっぱなし|(?:電源|スイッチ)(?:が|は)?入(?:りません|らない|らず)|巻き?上げ(?:が|は)?(?:でき|出来)(?:ません|ない)|動作確認(?:が|は)?(?:でき|出来)て(?:いません|いない|おりません)|ピント(?:が|は|も)?(?:完全に)?(?:合いません|合わない|合わず|甘い)|合わない(?:時|とき)|不安定|(?:ヒビ|ひび)(?:割れ)?|クラック|割れ(?!物)|カタカタ|異音|(?:リング|ヘリコイド)(?:が|は|も)?(?:かなり|やや|少し|とても|非常に)?(?:固い|硬い|固く|硬く|重い|重く)`;
  const MECH_RE = new RegExp("^(?:" + MECH_PAT + ")$"), MECH_COND_RE = /^(?:こと|事)?(?:が|は)?(?:場合(?![がも](?:あり|ござい))|際(?:は|に)|(?:とき|時)(?:は|には)|(?:など|等)[^。\n]{0,10}(?:初期不良|返品|保証))/, HAS_RE = /^(?:が|も|は)?(?:あり(?!ませ)|有り(?!ませ)|ござい(?!ませ))/;
  const DEFECT_RE = new RegExp(String.raw`(カビ|かび|黴|クモリ|くもり|曇り|白濁|バルサム|分離|コーティング(?:剥がれ|はがれ|ハガレ|剥離|劣化|ムラ|傷|キズ)|(?<!塗装)(?<!塗装の)(?<!ペイント)(?<!ペイントの)剥離|ヤケ|黄変|拭き傷|拭き跡|絞り粘り|絞りに粘り|油染み|油浮き|油滲み|オイル染み|オイル漏れ|動作未確認|不動|故障|写りに影響|` + MECH_PAT + ")", "g");
  // 光学の欄で状態を書かず「写真を確認して」とだけ書くのは、写真に難が写っている合図のことが多い
  const PHOTO_ONLY_RE = /光学[^\n]{0,15}\n?(?:[^\n]{0,60}\n?){0,3}?[^\n]{0,40}写真[^\n]{0,10}確認/;
  // 同じ文の中にあれば「〜なし」と打ち消しているとみなす語
  const NEG_RE = /(なし|無し|ナシ|ありません|有りません|ございません|見られません|見当たりません|確認できません|(?<!少)ない|無い)/;
  function descHits(text, cond) {
    const hits = [];
    for (const m of text.matchAll(DEFECT_RE)) {
      if (text[m.index - 1] === "「") continue;  // 「動作未確認品」は返品対象外 など、規約の定型文は除く
      const around = text.slice(Math.max(0, m.index - 15), m.index + 25);
      // 「初期不良の自然故障は返品対応」「故障した場合は無償修理」など、保証・返品の規約文は除く
      if (m[1] === "故障" && /初期不良|自然故障|返品|保証|修理|場合/.test(around)) continue;
      // 返品規約の「微細なゴミやカビ、くもり、サビ…など」のような列挙（前に返品・対象外などがあり、同じ文に傷みの語が3つ以上）
      const sStart = Math.max(text.lastIndexOf("。", m.index), text.lastIndexOf("\n", m.index)) + 1;
      const sEnd = text.slice(m.index).search(/[。\n]/);
      const sentence = text.slice(sStart, sEnd < 0 ? m.index + 80 : m.index + sEnd);
      if (/返品|返金|対象外|保証|免責/.test(text.slice(Math.max(0, m.index - 200), m.index)) &&
          (sentence.match(/カビ|かび|くもり|クモリ|曇り|サビ|錆|キズ|傷|磨耗|摩耗|劣化|ゴミ|チリ|ホコリ/g) || []).length >= 3) continue;
      // 露出計の不良は拾わない（2026-11-23 オーナー「露出計不良はオッケー」。記載して出せば売れる）
      if ((MECH_RE.test(m[1]) || /^(不動|故障|動作未確認)$/.test(m[1])) && /(?:露出計|メーター|測光)[^、。\n]{0,10}$/.test(text.slice(Math.max(0, m.index - 14), m.index))) continue;
      if (MECH_RE.test(m[1]) && MECH_COND_RE.test(text.slice(m.index + m[1].length, m.index + m[1].length + 30))) continue;  // 「万が一動作しない場合は」などは規約の文
      const rest = text.slice(m.index, m.index + 60);
      const kv = rest.slice(m[1].length).match(/^\s*[：:]\s*([^\s　、。|｜]+)/);  // 「クモリ：ごくわずか」のような項目形式は値だけ見る
      if (kv) { if (/^(なし|無し|ナシ|無|ない|-|ー|×)/.test(kv[1])) continue; }
      else {
        const after = rest.slice(m[1].length);  // 語そのもの（合わない・回りません）を打ち消しと読まないよう、語の後ろだけ見る
        const cut = after.search(/[。\n】|｜]/);
        if (!HAS_RE.test(after.replace(/^割れ/, "")) && isNegated(cut < 0 ? after : after.slice(0, cut))) continue;
      }
      if (hits.some((h) => h.word === m[1])) continue;
      hits.push({ word: m[1], snip: text.slice(Math.max(0, m.index - 18), m.index + 32).replace(/\s+/g, " ").trim() });
    }
    const ph = text.match(PHOTO_ONLY_RE);
    if (ph) hits.push({ word: "光学は写真で確認", snip: ph[0].replace(/\s+/g, " ").trim().slice(0, 60) });
    if (cond && /状態が悪い/.test(cond)) hits.push({ word: "状態が悪い", snip: "商品の状態: " + cond });
    return hits;
  }
  function descStore() {
    const all = load(LS_DESC, {}), now = Date.now();
    for (const [k, v] of Object.entries(all)) if (!v || now - v.t > 7 * 864e5) delete all[k];
    return all;
  }
  state.desc = descStore();
  async function readDesc(id) {
    const html = await (await fetch(`/jp/auction/${id}`, { credentials: "include" })).text();
    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    if (!m) throw new Error("出品ページを読めませんでした");
    const j = JSON.parse(m[1]);
    const it = ((((j.props || {}).pageProps || {}).initialState || (j.props || {}).initialState || {}).item || {}).detail;
    const item = (it || {}).item || {};
    const text = String(item.descriptionHtml || item.description || "")
      .replace(/<br\s*\/?>/gi, "\n").replace(/<\/(p|div|li|tr|h\d)>/gi, "\n").replace(/<[^>]+>/g, " ")
      .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">");
    return { t: Date.now(), cond: item.conditionName || "", hits: descHits(text, item.conditionName || ""), ship: shipFee(item) };
  }
  // 出品ページの送料（appraise.py の item_shipping と同じ）: 出品者負担なら0、配送方法の送料（一律）の一番安いもの。分からなければ null
  function shipFee(item) {
    if (item.chargeForShipping === "seller") return 0;
    const fees = (((item.shipping || {}).methods) || []).map((m) => m && m.shippingFee).filter((v) => typeof v === "number" && v >= 0);
    return fees.length ? Math.min(...fees) : null;
  }
  // 送料の表示: 検索結果の送料／出品ページの送料／不明（既定の額で計算）
  const shipLabel = (i) => (i.ship == null ? `送?→${yen(DATA.cfg.defaultDomestic)}で計算` : `送${yen(i.ship)}${i.shipSrc === "page" ? "（出品ページ）" : ""}`);
  let descBusy = false;
  async function checkDescs(ids) {
    if (descBusy) return;
    descBusy = true;
    let fresh = 0;
    try {
      for (const id of ids) {
        if (state.desc[id] && !state.desc[id].error) continue;
        state.desc[id] = { loading: true };
        try { state.desc[id] = await readDesc(id); } catch (e) { state.desc[id] = { t: Date.now(), error: String(e.message || e) }; }
        fresh++;
        const all = load(LS_DESC, {}); all[id] = state.desc[id]; save(LS_DESC, all);
        evaluate(state.items, state.fx);  // 出品ページの送料で計算し直す
        render();
        await new Promise((r) => setTimeout(r, 1500));  // ヤフオクに負荷をかけないよう間隔を空ける
      }
    } finally { descBusy = false; }
    return fresh;
  }
  function descLine(id) {
    const d = state.desc[id];
    if (!d) return "";
    if (d.loading) return '<div class="s">説明文を確認中…</div>';
    if (d.error) return `<div class="s" title="${esc(d.error)}">説明文を読めませんでした</div>`;
    if (!d.hits.length) return `<div class="s g">✓ 説明文に光学難・故障の記載なし${d.cond ? `（${esc(d.cond)}）` : ""}</div>`;
    return `<div class="s x">⚠ 説明文: ${d.hits.map((h) => `「…${esc(h.snip)}…」`).join(" ")}</div>`;
  }
  // 過去データの状態（自分・Chatwork の販売実績と頒布会リスト）。例: 状態: Near MINT $255 ・ Exc+++ $265
  // 上限額で落札したときの利益（上限額の下に表示）。ストアは入札額×1.1を払う
  function atMax(i) {
    const c = i.calc;
    if (!c || !c.maxBid || i.usd == null) return "";
    const tm = i.tax ? 1 + DATA.cfg.storeTaxPct / 100 : 1;
    const x = calculate(Object.assign({}, i, { price: Math.round(c.maxBid * tm) }), i.usd, state.fx);
    if (x.profit == null) return "";
    // 実績どおりに売れたとき（2026-11-20 ユーザー決定「まず表示だけ」。判定・上限額は変えない。appraise.realized_profit と同じ）
    const ded = DATA.cfg.deductionUsd || 0, ratio = realizedRatio(i.usd - ded, i.lrank ?? listingRank(i.title || ""));
    let low = "";
    if (ratio != null) {
      const y = calculate(Object.assign({}, i, { price: Math.round(c.maxBid * tm) }), ded + (i.usd - ded) * ratio, state.fx);
      if (y.profit != null) low = `<div class="s${y.profit < 0 ? " x" : ""}" title="ヤフオクで落札した物は、見込みの売値（送料別）に対して実際は${Math.round(ratio * 100)}%で売れていた（落札73件の実績）。表示だけで、判定・上限額は変えない">実績どおりなら 利益${yen(y.profit)}（見込みの${Math.round(ratio * 100)}%）</div>`;
    }
    return `<div class="s" title="上限額ちょうどで落札したときの利益">落札時 利益${yen(x.profit)}（${x.margin}%）</div>` + low;
  }
  // 実績の売値の割合（config.toml [realized]）。商品名が MINT 以上なら mint、それ以外は見込み（送料別$）の帯で
  function realizedRatio(netUsd, rank) {
    const r = DATA.cfg.realized;
    if (!r || netUsd == null) return null;
    if (rank != null && rank >= 9 && r.mint) return r.mint;
    const v = netUsd < 100 ? r.under_100 : netUsd < 200 ? r.under_200 : r.over_200;
    return v || null;
  }
  function condLine(i) {
    const rs = (i.refs || []).filter((r) => r.tier <= 2).slice(0, 5);
    if (!rs.length) return "";
    const note = conditionNote(i.refs, i.title, i.flags, i.lrank);
    return `<div class="s">状態: ${rs.map((r) => `${esc(conditionOf(r) || "記載なし")} ${nd(r.price)}${isMemo(r) ? "（直した）" : ""}`).join(" ・ ")}` +
      `${note ? " → " + esc(note) : ""}</div>`;
  }
  function applyDesc(items) {
    for (const i of items) {
      const d = state.desc[i.id];
      if (d && d.hits && d.hits.length && (i.mark === "◎" || i.mark === "○")) {
        const opt = d.hits.map((h) => h.word).filter((w) => /^(カビ|かび|黴|クモリ|くもり|曇り|白濁|バルサム|分離)$/.test(w));
        // 説明文の光学難は見送り（appraise.py の judge と同じ）。それ以外の難（傷・ヤケ・動作未確認など）は要確認
        if (opt.length) { i.mark = "×"; i.reason = "説明文に光学難（" + opt.join("・") + "）は見送り"; }
        else { i.mark = "要確認"; i.reason = "説明文に " + d.hits.map((h) => h.word).join("・"); }
      }
    }
  }

  // 出品の本来の欄: good（入札候補）/ weak（確認してから・根拠が弱い）/ check（要確認）/ bad（×）/ wait（仮◎○）/ acc（付属品）
  function naturalSec(i, isAcc, sure) {
    if (isAcc(i)) return "acc";
    if (i.mark === "◎" || i.mark === "○") return sure(i) ? "good" : "weak";
    if (i.mark === "要確認" && i.calc) return "check";
    if (i.mark === "×") return "bad";
    return state.waitIds && state.waitIds.has(i.id) ? "wait" : "";
  }
  const SEC_LABEL = { good: "入札候補", weak: "確認してから入札", check: "要確認", bad: "× 見送り", wait: "仮◎○", acc: "付属品など（除外）", "": "相場なし" };

  function render() {
    const ALL = !!(state.allView && pageSeller());  // ［人気ストア］: このストアの全ページの全商品を判定付きで1つの表に
    let root = document.getElementById("yhk");
    if (!root) {
      const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
      root = document.createElement("div"); root.id = "yhk"; document.body.appendChild(root);
      if (!load(LS_OPEN, true)) root.classList.add("min");
    }
    const items = state.items;
    applyDesc(items);
    // 付属品だけ（カタログ・フード・キャップ・説明書など）・アダプター等は仕入れの対象外 → 表示しない（件数だけ）
    const isAcc = (i) => i.flags.some((f) => f === "付属品のみ?" || f === "アダプター等");
    const accN = items.filter(isAcc).length;
    // 確認不要＝根拠が 自分の販売実績・Chatwork実績・頒布会（tier 0〜2。講師陣が売れやすい物を選んだ。2026-11-18・22 オーナー）・自分で登録した相場。鑑定表・リサーチ（推定）は確認してから
    const sure = (i) => !!(i.refs && i.refs.length && i.refs[0].tier <= 2);  // 2026-11-18: 頒布会も売れた記録（appraise.SURE_TIER）
    const pin = state.pin || (state.pin = {});
    const secOf = (i) => pin[i.id] || naturalSec(i, isAcc, sure);
    const byEnd = (a, b) => (a.end < b.end ? -1 : 1);
    // 入札候補の並び（appraise.py の bid_priority と同じ）: 売れ筋・売値$99超を上に、同じ順位の中は終了が近い順
    const byPrio = (a, b) => bidPrio(a) - bidPrio(b) || byEnd(a, b);
    const good = items.filter((i) => secOf(i) === "good").sort(byPrio), weak = items.filter((i) => secOf(i) === "weak").sort(byPrio);
    const check = items.filter((i) => secOf(i) === "check");
    // 即決で今すぐ買える出品（2026-11-19 ユーザー「利益商品を本当に落札できない」）: 即決価格が上限額以下なら、競りにならず確実に買える。
    // 判定・上限額は変えない（今の ◎○・確認してから の中から、即決価格 ≦ 上限額 のものを一番上に並べ直すだけ）
    const instant = items.filter((i) => (secOf(i) === "good" || secOf(i) === "weak") && i.buyNow && i.calc && i.calc.maxBid && i.buyNow <= i.calc.maxBid && i.buyNow >= 0.4 * i.calc.maxBid)  // 4割未満は付属品・別物の取り違えが多い（appraise.INSTANT_MIN_RATIO）
      .sort((a, b) => (b.calc.maxBid - b.buyNow) - (a.calc.maxBid - a.buyNow));
    const bad = items.filter((i) => secOf(i) === "bad");
    const pinnedWaits = items.filter((i) => secOf(i) === "wait" && i.calc);  // 仮◎○の行で［登録］した物
    const priced = items.filter((i) => i.calc).length;
    const groups = new Map();
    for (const i of items) {
      if (i.calc || i.flags.some((f) => ["付属品のみ?", "アダプター等"].includes(f))) continue;
      const parts = (i.flags.includes("セット品") || KIT_RE.test(i.title)) ? setPartTitles(i.title) : [];
      if (i.flags.includes("セット品") && !parts.length) continue;  // 中身が分からないまとめ売りは今までどおり除く
      const [label, key] = parts.length ? [parts.map((p) => modelKey(p)[0]).join(" ＋ "), "set:" + parts.map((p) => modelKey(p)[1]).join("|")] : modelKey(i.title);
      if (!parts.length && label.split(" ").length < 2) continue;
      const g = groups.get(key) || { label, key, n: 0, prices: [], ex: i.title, ids: [], parts: parts.map((p) => modelKey(p)[0]) };
      if (i.id && g.ids.includes(i.id)) continue;  // 同じ出品は1回だけ数える
      g.n++; if (i.price) g.prices.push(i.price); if (i.id) g.ids.push(i.id); groups.set(key, g);
    }
    // 高い物ほど1件の利益が大きい（実績: $200以上は1件平均6,500円〜、$99以下は344円）ので、ヤフオクの価格が高い機種を上に。同じなら件数の多い順
    const gp = (g) => (g.prices.length ? median(g.prices) : 0);
    const groupList = [...groups.values()].sort((a, b) => gp(b) - gp(a) || b.n - a.n);
    const unpriced = groupList.slice(0, 40);
    // sold確認待ち: 相場が未登録の出品を「eBay出品中×係数」で仮に計算し、利益が出る物だけ（登録するまで◎○には入れない）
    const waits = [];
    for (const i of items) {
      if (i.calc || i.flags.some((f) => ["付属品のみ?", "アダプター等", "セット品", "光学難あり"].includes(f))) continue;
      const [label] = modelKey(i.title);
      // 「Contax 3 2 12」のように型番でない数字が2つ以上ある機種名は、eBay検索が当てにならないので除く
      if (label.split(" ").filter((t) => /^\d+$/.test(t)).length >= 2) continue;
      if (!/\d/.test(label)) continue;  // 「Contax Yashica」のようにメーカー名だけの機種名は、eBay検索が広すぎるので除く
      const sg = suggestUsd(state.browse[label]);
      if (!sg) continue;
      const usd = i.flags.includes("ジャンク") && DATA.cfg.junkFactor < 1 ? Math.round(sg * DATA.cfg.junkFactor) : sg;
      const c = calculate(i, usd, state.fx);
      const [mk] = judge(Object.assign({}, i, { calc: c }));
      if (mk === "◎" || mk === "○") waits.push({ i, c, usd, sg, mk, label, b: state.browse[label] });
    }
    waits.sort((a, b) => (a.i.end < b.i.end ? -1 : 1));
    state.waitIds = new Set(waits.map((w) => w.i.id));
    // 落札相場の見張り（◎○・確認してから・仮◎○）→ 新しく足したときだけ ダウンロード/aurora_seen.csv を書き直す
    const mb = (i) => (i.calc ? i.calc.maxBid : "");
    if (watchAdd([...good.map((i) => ({ i, mark: i.mark, maxBid: mb(i) })), ...weak.map((i) => ({ i, mark: "確認してから" + i.mark, maxBid: mb(i) })),
      ...pinnedWaits.map((i) => ({ i, mark: "仮" + i.mark, maxBid: mb(i) })), ...waits.map((w) => ({ i: w.i, mark: "仮" + w.mk, maxBid: w.c.maxBid }))])
      && typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
      clearTimeout(state.seenTimer);  // 続けて読むときに何度も保存しないよう、30秒まとめて1回
      state.seenTimer = setTimeout(() => {
        try { chrome.runtime.sendMessage({ type: "saveFile", name: "aurora_seen.csv", csv: watchCsv() }, () => void chrome.runtime.lastError); } catch (e) {}
      }, 30000);
    }
    const more = !state.done && state.total && state.total >= (state.next || 1);

    // ［直す］した行: 本来の欄が変わっても、ここに残す（ページを開き直すと本来の欄へ）
    const pinNote = (i) => {
      if (!pin[i.id]) return "";
      const nat = naturalSec(i, isAcc, sure);
      return `<div class="pinmove">［直す］で再計算しました: 判定 ${esc(i.mark)}${i.reason ? "（" + esc(i.reason) + "）" : ""}${nat !== pin[i.id] ? ` ・ 本来の欄は「${SEC_LABEL[nat] || nat}」。ページを開き直すとそちらへ移ります` : ""}</div>`;
    };
    const oddsLine = (i, c) => {
      if (!c || !(i.mark === "◎" || i.mark === "○")) return "";
      const o = winOdds(c.maxBid, i.tax);
      return o ? `<div class="s" title="${esc(`過去の入札（${o.key.split("|")[0]}・${o.key.endsWith("|1") ? "ストア" : o.key.endsWith("|0") ? "個人" : "ストア＋個人"}）で${o.n}件中${o.won}件落札`)}">落札見込み <b>${o.label}</b>（${o.won}/${o.n}件）</div>` : "";
    };
    const row = (i) => {
      const c = i.calc, r = i.refs[0] || {}, e = ebay(i.eq || i.query);
      const cls = i.mark === "◎" ? "g" : i.mark === "○" ? "o" : "x";
      const junk = i.flags.includes("ジャンク") && DATA.cfg.junkFactor < 1 && i.lrank == null;
      const sw = sweetSpot(i.title);
      const flags = (soldHit(i) ? `<span class="flag sweet" title="売れ筋: この機種は自分（またはChatwork）の販売実績で、入札基準以上の利益が出ていました。入札候補の上に並べています（判定・上限額は変えていません）">売れ筋</span>` : "") +
        (sw ? `<span class="flag sweet" title="${esc("狙い目スペック: " + sw + "（講座資料。判定・上限額は変えていません）")}">狙い目</span>` : "") +
        i.flags.filter((f) => !["動作品", "美品"].includes(f)).map((f) => `<span class="flag">${esc(f)}</span>`).join("");
      const tip = `eBay検索語: ${i.eq || i.query}（価格+送料の高い順）\n根拠: ${r.title || ""} ${nd(r.price)}（送料別） [${r.source || ""}]` +
        (junk ? `\nジャンク評価 ×${Math.round(DATA.cfg.junkFactor * 100)}%` : "") + `\n送料別の商品代 ${nd(i.usd)}（送料$${SHIP()}込みなら $${i.usd}）`;
      const [left, soon] = remain(i.end);
      const markHtml = ALL && !c ? '<span class="s">相場なし</span>' : i.mark;
      return `<tr class="${soon ? "soon" : ""}${seenOf(i.id) ? " seen" : ""}"><td class="m ${cls}">${seenBox(i.id)}${markHtml}<div class="s ${soon ? "hot" : ""}">${left}</div>${ALL && c && i.reason ? `<div class="s">${esc(i.reason)}</div>` : ""}</td>
<td>${i.trusted ? '<span class="trustb" title="安心ストア（フォロー中）">★</span>' : ""}<a class="t" href="https://auctions.yahoo.co.jp/jp/auction/${i.id}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>${flags}
${cautionLine(i)}<div class="s">${setLine(i) || (r.title ? `根拠: ${esc(r.title)} [${esc(srcLabel(r))}]${isMemo(r) ? "（手で直した相場）" : ""}` : "相場データなし ― sold を見て［直す$］に売れた商品代を入れると判定します")}</div>${isMemo(r) ? memoTrend(r.title) : ""}${condLine(i)}${yahooLine(modelKey(i.title)[1])}${descLine(i.id)}${pinNote(i)}</td>
<td class="n"><a class="usd" href="${e.pr}" target="_blank" rel="noopener" title="${esc("Product Research を開く（Best Offer込みの成約も確認できます）\n" + tip)}">${nd(i.usd)}</a>${trendArrow(i)}<div class="s">送料別</div>${soldLine(r)}${junk ? `<div class="s">元${nd(r.price)}</div>` : ""}
<div class="pills"><a class="pill sold" href="${e.sold}" target="_blank" rel="noopener" title="eBayで売れた履歴（高い順）">sold</a><a class="pill" href="${e.now}" target="_blank" rel="noopener" title="eBayに出品中（高い順）">now</a></div><div class="pills"><button class="open3" data-a="open3" data-urls="${esc(JSON.stringify([`https://auctions.yahoo.co.jp/jp/auction/${i.id}`, e.sold, e.now]))}" title="ヤフオクの出品ページ・sold・now を、新しいタブに左からこの順で開きます">全て開く</button></div>
<div class="fix" title="${esc(`相場を直す（機種: ${modelKey(i.title)[0]}）\nsold の商品代（送料を含まない$）と、売れた物の状態を入れてください。計算では送料$${SHIP()}を足して手数料を出します。同じ機種の過去データより優先されます`)}">${memoInputs(modelKey(i.title)[0], 'placeholder="直す$"', "直す")}</div></td>
<td class="n">${yen(i.price)}${i.tax ? `<div class="s">（税込）入札額では ${yen(c ? Math.round(c.bidNow) : null)}</div>` : ""}<div class="s">入札${i.bids}・${shipLabel(i)}</div></td>
<td class="n"><b>${yen(c && c.maxBid)}</b>${bidButton(i, c, i.usd, false)}${i.mark === "◎" || i.mark === "○" ? '<div><span class="okb" title="この金額をヤフオクの入札欄にそのまま入力してください">このまま入札OK</span></div>' : ""}${atMax(i)}${oddsLine(i, c)}${i.mark === "◎" || i.mark === "○" ? winLine(i) : ""}${i.tax && c ? `<div class="s" title="ストアは入札額に消費税10%が上乗せされます">支払い ${yen(Math.round(c.maxBid * (1 + DATA.cfg.storeTaxPct / 100)))}（税込）</div>` : ""}<div class="s">分岐 ${yen(c && c.breakeven)}</div></td>
<td class="n">${yen(c && c.profit)}<div class="s">${c && c.margin != null ? c.margin + "%" : ""}</div></td></tr>`;
    };
    // 安心枠（フォロー中のストア）の行
    const trustRows = (list) => {
      const t = list.filter((i) => i.trusted);
      return t.length ? `<tr><td colspan="6" class="tsep trust">★ 安心枠（フォロー中のストア） ${t.length}件 ― 基準 利益${(DATA.cfg.trustedMinProfit ?? DATA.cfg.minProfit).toLocaleString()}円以上・利益率${DATA.cfg.trustedMinMarginPct ?? DATA.cfg.minMarginPct}%以上</td></tr>${t.map(row).join("")}` : "";
    };
    const waitRow = (w) => {
      const i = w.i, c = w.c, e = ebay(w.label);
      const [left, soon] = remain(i.end);
      const d = state.desc[i.id];
      return `<tr class="${soon ? "soon" : ""} tent${seenOf(i.id) ? " seen" : ""}"><td class="m ${w.mk === "◎" ? "g" : "o"}">${seenBox(i.id)}<span class="tmark">仮</span>${w.mk}<div class="s ${soon ? "hot" : ""}">${left}</div></td>
<td><a class="t" href="https://auctions.yahoo.co.jp/jp/auction/${i.id}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>
<div class="s">機種: ${esc(w.label)} ・ eBay出品中（送料別）最安${nd(w.b.min || 0)}・中央値${nd(w.b.med)}から、最安値の案 ${nd(w.sg)}${w.usd !== w.sg ? "×ジャンク" + Math.round(DATA.cfg.junkFactor * 100) + "%" : ""} で仮計算（sold未確認）</div>${yahooLine(modelKey(i.title)[1])}
${d ? descLine(i.id) : `<button data-a="desc" data-id="${esc(i.id)}">説明文を確認</button>`}</td>
<td class="n"><div class="pills"><a class="pill sold" href="${e.sold}" target="_blank" rel="noopener" title="eBayで売れた履歴（高い順）">sold</a><a class="pill" href="${e.now}" target="_blank" rel="noopener">now</a></div><div class="pills"><button class="open3" data-a="open3" data-urls="${esc(JSON.stringify([`https://auctions.yahoo.co.jp/jp/auction/${i.id}`, e.sold, e.now]))}" title="ヤフオクの出品ページ・sold・now を、新しいタブに左からこの順で開きます">全て開く</button></div><a class="s" href="${e.pr}" target="_blank" rel="noopener">Product Research</a></td>
<td class="n">${yen(i.price)}<div class="s">入札${i.bids}・${shipLabel(i)}</div></td>
<td class="n"><b>${yen(c.maxBid)}</b>${bidButton(Object.assign({}, i, { calc: c }), c, w.usd, true)}<div class="s">仮（sold確認前）</div>${atMax(Object.assign({}, i, { calc: c, usd: w.usd }))}<div class="s">分岐 ${yen(c.breakeven)}</div></td>
<td class="n">${yen(c.profit)}<div class="s">${c.margin != null ? c.margin + "%" : ""}</div>
<div class="fix" title="sold を見て、売れた商品代（送料を含まない$）がこの額以上なら、そのまま［登録］。低ければ直して［登録］。同じ機種の今後の出品にも効きます">${memoInputs(w.label, `value="${netOf(w.sg)}" class="sug"`, "登録")}</div></td></tr>`;
    };
    // ［人気ストア］の表: 全商品を終了が近い順に。仮◎○の行は仮の計算で、それ以外は通常の行（×・相場なし・付属品も含む）
    const allTable = () => {
      const wmap = new Map(waits.map((w) => [w.i.id, w]));
      const list = items.slice().sort(byEnd);
      const cnt = (f) => list.filter(f).length;
      return `<h4 style="margin:10px 0 4px">人気ストア: 全 ${list.length}件${state.total ? `（このストアの出品 ${state.total}件）` : ""} ― 判定付き・終了が近い順</h4>
<div class="s" style="margin-bottom:4px">◎○ ${cnt((i) => i.mark === "◎" || i.mark === "○")}件 ・ 仮◎○ ${wmap.size}件 ・ 要確認 ${cnt((i) => i.mark === "要確認" && i.calc)}件 ・ × ${cnt((i) => i.mark === "×")}件 ・ 相場なし ${cnt((i) => !i.calc && !wmap.has(i.id))}件${state.loading ? " ・ 読み込み中（読めた分から表示）" : ""}。相場なしの行は［直す$］に sold の商品代を入れると即判定します</div>
<table>${head}${list.map((i) => (wmap.has(i.id) && !pin[i.id] ? waitRow(wmap.get(i.id)) : row(i))).join("")}</table>`;
    };
    const head = `<tr><th>判定</th><th>商品（タイトル→ヤフオク）</th><th title="送料を含まない商品代（net）。金額→Product Research ／ sold→売れた履歴 ／ now→出品中">eBay相場（送料別）</th><th>現在</th><th title="ヤフオクの入札欄にそのまま入れる金額">入札する上限額</th><th title="今の価格のまま落札できたときの利益">利益（今の価格で落札）</th></tr>`;

    root.innerHTML = `<div class="hd"><b>入札鑑定</b><span>${items.length}${state.total ? "/" + state.total : ""}件 ・ 相場あり ${priced}件 ・ 入札候補 ${good.length}件${weak.length + waits.length + pinnedWaits.length ? ` ・ 確認してから ${weak.length + waits.length + pinnedWaits.length}件` : ""}${accN ? ` ・ 付属品など ${accN}件は除外` : ""}</span>
<span class="sp"></span>${state.loading ? `<span>${esc(state.loading)}</span>` : more ? `<button data-a="all" title="ヤフオクに負担をかけないよう、一度に読むのは1000件まで">続けて鑑定（次の${Math.min(LOAD_STEP, state.total - ((state.next || 1) - 1))}件・残り${state.total - ((state.next || 1) - 1)}件）</button>` : ""}
${pageSeller() ? `<button data-a="store" title="このストアの全ページの全商品を読み込み、判定付きで終了が近い順に1つの表で表示します（×・相場なしも含む）">${ALL ? "通常の表示に戻す" : "人気ストア"}</button>` : ""}${pageSeller() ? `<button data-a="trust" title="このストアを安心枠に入れる／外す（フォロー中のストアは開くと自動で入ります）">${trustedIds().has(pageSeller()) ? "★安心ストア（外す）" : "☆安心ストアに入れる"}</button>` : ""}<button data-a="openGood" title="入札候補（◎○）を、勝ちやすさの高い順（1割未満の機種は最後）で最大10件、新しいタブにまとめて開きます。上限額の一覧はコピーされるので、メモ帳などに貼って見ながら入札できます（入札はご自身で。自動入札はしません）">◎○をまとめて開く</button><button data-a="copy">◎○をコピー</button><button data-a="min">${root.classList.contains("min") ? "開く" : "最小化"}</button></div>
<div class="bd"><div class="s">為替 1USD=<input data-a="fx" value="${state.fx}">円 ・ 基準 利益${DATA.cfg.minProfit.toLocaleString()}円以上（売値$${DATA.cfg.lowPriceUsd || 99}以下は${(DATA.cfg.lowPriceMinProfit || 0).toLocaleString()}円以上）・利益率${DATA.cfg.minMarginPct}%以上 ・ 手数料${DATA.cfg.feePct.toFixed(2)}%+$${DATA.cfg.perOrderUsd} ・ eBayの$は送料別（計算では送料$${SHIP()}込みに戻して手数料を計算）${DATA.cfg.junkFactor < 1 ? ` ・ ジャンク${Math.round(DATA.cfg.junkFactor * 100)}%評価` : ""} ・ 相場データ ${DATA.refs.length}件（${esc(DATA.built)}作成）</div>
${ALL ? allTable() : `${instant.length ? `<h4 style="margin:10px 0 4px" class="g">即決で今すぐ買える ${instant.length}件 ― 即決価格が上限額以下。競りにならず確実に買える（確認してからの物は sold を見てから）</h4>
<table>${head}${instant.map((i) => row(i).replace(/(<td class="m[^>]*>)/, `$1<div class="instant-tag" title="即決 ${yen(i.buyNow)}（上限額 ${yen(i.calc.maxBid)} より ${yen(i.calc.maxBid - i.buyNow)} 安い）。ヤフオクのページで［今すぐ落札する］">即決 ${yen(i.buyNow)}</div>`)).join("")}</table>` : ""}
<h4 style="margin:10px 0 4px">入札候補（終了が近い順）― 上限額をそのまま入札欄へ</h4>
${good.length ? `<table>${head}${trustRows(good)}${good.filter((i) => !i.trusted).length ? `<tr><td colspan="6" class="tsep chal">チャレンジ枠（その他の出品者） ${good.filter((i) => !i.trusted).length}件 ― 基準 利益${DATA.cfg.minProfit.toLocaleString()}円以上・利益率${DATA.cfg.minMarginPct}%以上</td></tr>` : ""}${good.filter((i) => !i.trusted).map(row).join("")}</table>` : `<div class="s">このページには、確認なしで入札できる出品はありません（下の「確認してから入札」を見てください）。</div>`}
${weak.length || waits.length || pinnedWaits.length ? `<h4 style="margin:14px 0 4px">確認してから入札 ${weak.length + waits.length + pinnedWaits.length}件 ― sold を見て、売れた商品代が相場以上なら［直す$］［登録］→ 上の入札候補に上がります</h4>
<table>${head}${weak.length ? `<tr><td colspan="6" class="tsep">根拠が鑑定表・リサーチ（推定）の ◎○ ${weak.length}件 ― 売れた価格ではないので sold で確認</td></tr>${weak.map(row).join("")}` : ""}${waits.length || pinnedWaits.length ? `<tr><td colspan="6" class="tsep">仮◎○ ${waits.length + pinnedWaits.length}件 ― 相場は未登録。eBay出品中（公式API）の最安値（送料無料は−$30）で仮に計算</td></tr>${pinnedWaits.map(row).join("")}${waits.map(waitRow).join("")}` : ""}</table>` : ""}
${canBrowse() && !waits.length ? `<div class="s">${Object.keys(state.browse).length ? "仮◎○はありません" : "eBay出品中の価格を調べています…（仮◎○）"}</div>` : ""}
<details><summary>要確認（相場はあるがセット品・光学難・説明文の難などの可能性） ${check.length}件</summary><table>${head}${check.map(row).join("")}</table></details>
<details><summary>× 見送り ${bad.length}件</summary><table>${head}${bad.map(row).join("")}</table></details>
<details ${priced < items.length / 3 ? "open" : ""}><summary>相場が未登録の機種（ヤフオクの価格が高い順） ${groups.size}種類 ― 想定$を入れて登録すると即反映</summary>
${canBrowse() ? `<div class="s" style="margin:4px 0">eBay出品中＝公式APIで調べた<b>出品中</b>の価格（米国向け・中古。送料$${SHIP()}を引いた送料別の額で表示）。想定$には最安値（送料無料の出品は出品額−$30）を<b>案</b>として入れています。<b>sold を見て確認してから［登録］</b>してください（登録するまで判定には使いません）。</div>` : ""}
<table><tr><th title="機種名→ヤフオクの出品ページ（同じ機種が複数あれば1件目）">機種（→ヤフオク）・想定$（送料別）を入れて登録</th><th>件数</th><th>ヤフオク現在値</th>${canBrowse() ? "<th>eBay出品中</th>" : ""}</tr>${unpriced.map((g) => { const yh = g.ids.slice(0, 3).map((id) => `https://auctions.yahoo.co.jp/jp/auction/${id}`), sk = "m:" + g.label;
  const sug = (l) => suggestUsd(state.browse[l]) ? ` class="sug" value="${netOf(suggestUsd(state.browse[l]))}"` : "";
  const line = (l) => `<a class="pill sold" href="${ebay(l).sold}" target="_blank" rel="noopener">sold</a> <a class="pill" href="${ebay(l).now}" target="_blank" rel="noopener">now</a> <button class="open3" data-a="open3" data-urls="${esc(JSON.stringify([...yh.slice(0, 1), ebay(l).sold, ebay(l).now]))}" title="ヤフオクの出品ページ（行の1件目）・sold・now を、新しいタブに左からこの順で開きます。同じ機種のほかの出品は件数の下のリンクから">全て開く</button> <a class="s" href="${ebay(l).pr}" target="_blank" rel="noopener">Product Research</a>`;
  const body = g.parts && g.parts.length
    ? `<a href="${yh[0] || "#"}" target="_blank" rel="noopener" title="ヤフオクの出品ページを開く">${esc(g.ex.slice(0, 70))}</a><div class="s">セット・キット ${g.parts.length}点 ― 相場あり ${g.parts.filter((l) => memoVal(load(LS_MEMO, {})[l]).usd).length}/${g.parts.length}点${g.parts.every((l) => memoVal(load(LS_MEMO, {})[l]).usd) ? "（そろいました。上の表で判定されます）" : " ― 全部そろうと合計から判定します"}</div>`
      + g.parts.map((l, n) => `<div class="setpart"><b>${n + 1}. ${esc(l)}</b> ${line(l)}<div class="fix fixl">${memoInputs(l, `placeholder="想定$"${sug(l)}`, "登録")}</div></div>`).join("")
    : `<a href="${yh[0] || ebay(g.label).pr}" target="_blank" rel="noopener" title="ヤフオクの出品ページを開く">${esc(g.label)}</a> ${line(g.label)}<div class="s">${esc(g.ex.slice(0, 60))}</div>${yahooLine(g.key)}<div class="fix fixl">${memoInputs(g.label, `placeholder="想定$"${sug(g.label)}`, "登録")}</div>`;
  return `<tr class="${seenOf(sk) ? "seen" : ""}"><td class="um">${seenBox(sk)}${body}</td>
<td class="n">${g.n}${yh.length > 1 ? `<div class="s">ほかに同じ機種 ${yh.slice(1).map((u, k) => `<a href="${u}" target="_blank" rel="noopener">${k + 1}</a>`).join(" ")}${g.n > yh.length ? " …" : ""}</div>` : ""}</td><td class="n">${yen(g.prices.length ? median(g.prices) : null)}</td>
${canBrowse() ? `<td class="n" data-browse="${esc(g.label)}">${browseCell(g.label)}</td>` : ""}</tr>`; }).join("")}</table>
<div style="margin-top:6px"><button data-a="memocopy">登録した相場メモをコピー（${Object.keys(load(LS_MEMO, {})).length}件）</button>
<span class="s">「直す」「登録」した相場は ダウンロード/aurora_price_memo.csv に自動保存されます。Mac で build_store.py か build_extension.py を実行すると data/price_memo.csv に取り込まれ、スマホにも反映されます</span></div></details>`}</div>`;
    badges();
    applyFlash();
    if (!state.loading) fetchBrowse(groupList.slice(0, DATA.cfg.browseMaxModels).map((g) => g.label));
    checkDescs(good.map((i) => i.id));
  }

  function badges() {
    for (const i of state.items) {
      if (!i.calc || i.flags.some((f) => f === "付属品のみ?" || f === "アダプター等")) continue;
      const a = document.querySelector(`a[href*="/auction/${i.id}"]:not(#yhk a)`);
      if (!a || a.parentElement.querySelector(".yhk-badge")) continue;
      const b = document.createElement("span");
      b.className = "yhk-badge " + (i.mark === "◎" ? "g" : i.mark === "○" ? "o" : "x");
      b.textContent = `${i.mark} 入札上限${yen(i.calc.maxBid)}`;
      a.insertAdjacentElement("afterend", b);
    }
  }

  // Node でのテスト用
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { cautionLine, robotsSafeUrl, winOf, winText, isLowWin, trendOf, suggestUsd, sweetSpot, bidPrio, isFNumber, modelWord, setPartTitles, titleToQuery, tokensOf, detectFlags, matchRefs, matchKantei, calculate, judge, evaluate, parseItems, modelKey, roundDown, descHits, ebaySearchQuery, siblingExcludes, modelQuery, cleanQuery, queryForTitle, variantsConflict, conditionOf, matchPreferMemo, isMemo, shipFee, winOdds, yahooLine, watchAdd, watchCsv };
    return;
  }

  // 相場メモ（このブラウザ）→ CSV。Mac では ダウンロード/aurora_price_memo.csv に自動保存し、build_store.py などが取り込む
  function memoCsv() {
    const q = (s) => '"' + String(s).replace(/"/g, '""') + '"';
    return "title,sold_price_usd,source\n" + Object.entries(load(LS_MEMO, {}))
      .map(([k, v]) => { const m = memoVal(v); return `${q(k)},${m.usd},${q(memoSource(m.cond))}`; }).join("\n");
  }
  // ---- 相場の動き: ［直す］［登録］のたびに {機種: [{usd, cond, d}]} を残す（usd は送料込み、d は日付）。
  //      Mac にも ダウンロード/aurora_price_memo_history.csv で送り、data/price_memo_history.csv に貯まる（DATA.memoHist で戻ってくる）
  function addMemoHist(k, old, usd, cond) {
    const all = load(LS_MEMO_HIST, {}), h = all[k] || [];
    if (!h.length && old) { const m = memoVal(old); h.push({ usd: m.usd, cond: m.cond, d: "" }); }  // 記録を始める前の相場（日付不明）
    h.push({ usd, cond, d: new Date().toISOString().slice(0, 10) });
    all[k] = h.slice(-30); save(LS_MEMO_HIST, all);
    const q = (x) => '"' + String(x ?? "").replace(/"/g, '""') + '"';
    const csv = "title,sold_price_usd,cond,date\n" + Object.entries(all).flatMap(([t, hs]) => hs.map((e) => [t, e.usd, e.cond, e.d].map(q).join(","))).join("\n");
    if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id)
      try { chrome.runtime.sendMessage({ type: "saveFile", name: "aurora_price_memo_history.csv", csv }, () => void chrome.runtime.lastError); } catch (e) {}
  }
  // その機種の相場の動き（2つ以上の違う値があるときだけ）: 「相場の動き $110(10/5) → $125(11/17) ↑+14%」
  function memoHistList(k) {
    const seen = new Set(), list = [];
    for (const e of [...((DATA.memoHist || {})[k] || []).map(([usd, d, cond]) => ({ usd, d, cond })), ...(load(LS_MEMO_HIST, {})[k] || [])]) {
      const key = e.usd + "|" + (e.d || "");
      if (!seen.has(key)) { seen.add(key); list.push(e); }
    }
    list.sort((a, b) => String(a.d || "").localeCompare(String(b.d || "")));
    return list;
  }
  const mdOf = (d) => (d ? `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}` : "以前");
  // ［履歴］: その機種の相場メモの履歴を全部（新しい順・前回との差つき）
  function memoHistTable(k) {
    const list = memoHistList(k);
    if (!list.length) return "";
    const rows = list.map((e, i) => {
      const p = i ? list[i - 1].usd : null, dif = p ? Math.round((e.usd - p) / p * 100) : null;
      return `<tr><td>${e.d ? esc(e.d) : "（記録前）"}</td><td class="n">${nd(e.usd)}</td><td>${esc(e.cond || "-")}</td><td class="n">${dif == null ? "" : dif > 0 ? `<span class="g">↑ +${dif}%</span>` : dif < 0 ? `<span class="x">↓ ${dif}%</span>` : "→ 0%"}</td></tr>`;
    }).reverse().join("");
    return `<table class="hist" style="margin-top:4px"><tr><th>日付</th><th class="n">相場（送料別）</th><th>状態</th><th class="n">前回比</th></tr>${rows}</table>`;
  }
  function memoTrend(k) {
    const list = memoHistList(k);
    const btn = list.length ? ` <button data-a="hist" data-k="${esc(k)}" style="padding:1px 7px;font-size:11px" title="［直す］［登録］で入れたこの機種の相場の履歴を全部表示">${(state.histOpen || new Set()).has(k) ? "履歴を閉じる" : "履歴"}（${list.length}）</button>` : "";
    const open = (state.histOpen || new Set()).has(k) ? memoHistTable(k) : "";
    const vals = list.filter((e, i) => i === 0 || e.usd !== list[i - 1].usd);  // 同じ額が続く所は1つに
    if (vals.length < 2) return btn ? `<div class="s">${btn}</div>${open}` : "";
    const last = vals[vals.length - 1], prev = vals[vals.length - 2];
    const pct = Math.round((last.usd - prev.usd) / prev.usd * 100);
    const arrow = last.usd > prev.usd ? `<b style="color:#1f7a5c">↑ +${pct}%</b>` : `<b style="color:#a4473a">↓ ${pct}%</b>`;
    const md = (d) => `（${mdOf(d)}）`;
    const path = vals.slice(-4).map((e) => `${nd(e.usd)}${md(e.d)}`).join(" → ");
    return `<div class="s" title="［直す］［登録］で上書きした相場の履歴（送料別の商品代）。上向きなら強気、下向きなら控えめに">相場の動き ${path} ${arrow}${btn}</div>${open}`;
  }
  // 相場の上がり下がり（↑↓。2026-11-19 ユーザー指定「わかるものだけでいい」）。判定・上限額は変えない（表示だけ）
  //   1) 手で直した相場（相場メモ）: 前回の登録額と今の額
  //   2) 日付のある販売実績（自分・Chatwork・頒布会）: いちばん新しい日から90日以内と、それより前の中央値
  //   どちらも ±10% 以上動いたときだけ矢印。データが足りなければ出さない
  const TREND_PCT = 10;
  function trendOf(i) {
    const r = i.refs && i.refs[0];
    if (!r || i.setParts && i.setParts.length) return null;
    if (isMemo(r)) {
      const vals = memoHistList(r.title).filter((e, k, a) => k === 0 || e.usd !== a[k - 1].usd);
      if (vals.length >= 2) {
        const last = vals[vals.length - 1], prev = vals[vals.length - 2];
        return { pct: Math.round((last.usd - prev.usd) / prev.usd * 100), why: `相場メモ ${nd(netOf(prev.usd) ?? prev.usd)}（${mdOf(prev.d)}）→ ${nd(netOf(last.usd) ?? last.usd)}（${mdOf(last.d)}）` };
      }
    }
    const pts = i.refs.filter((x) => x.tier <= 2 && /^\d{4}-\d\d-\d\d$/.test(x.d || "")).sort((a, b) => a.d.localeCompare(b.d));
    if (pts.length < 2) return null;
    const newest = Date.parse(pts[pts.length - 1].d), cut = newest - 90 * 864e5;
    const recent = pts.filter((x) => Date.parse(x.d) >= cut), older = pts.filter((x) => Date.parse(x.d) < cut);
    if (!recent.length || !older.length) return null;
    const a = median(older.map((x) => x.price)), b = median(recent.map((x) => x.price));
    return { pct: Math.round((b - a) / a * 100), why: `売れた価格 90日より前 ${nd(netOf(a))}（${older.length}件）→ 直近90日 ${nd(netOf(b))}（${recent.length}件）` };
  }
  function trendArrow(i) {
    const t = trendOf(i);
    if (!t || Math.abs(t.pct) < TREND_PCT) return "";
    const up = t.pct > 0;
    return ` <span class="trend ${up ? "g" : "x"}" title="${esc((up ? "相場が上がっている" : "相場が下がっている") + "（判定・上限額は今の相場のまま。表示だけ）\n" + t.why)}">${up ? "↑" : "↓"}${up ? "+" : ""}${t.pct}%</span>`;
  }
  function saveMemoFile() {
    if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.id) return;
    try { chrome.runtime.sendMessage({ type: "saveMemo", csv: memoCsv() }, () => void chrome.runtime.lastError); } catch (e) {}
  }

  // 描き直しても数秒は光らせる（説明文を読んだ後などに表を描き直すため）
  function applyFlash() {
    const f = state.flash;
    if (!f || Date.now() > f.until) return;
    const a = document.querySelector(`#yhk a.t[href*="/auction/${CSS.escape(f.id)}"]`);
    const row = a && a.closest("tr");
    if (!row) return;
    const det = row.closest("details");
    if (det) det.open = true;
    row.classList.add("flash");
  }
  // ［直す］［登録］の後: 出品が移った先へスクロールして光らせ、どの欄に移ったかを表示（たたまれた欄なら開く）
  function showMoved(id) {
    const root = document.getElementById("yhk");
    const it = state.items.find((x) => x.id === id);
    const a = root && root.querySelector(`a.t[href*="/auction/${CSS.escape(id)}"]`);
    const row = a && a.closest("tr");
    const kitLeft = it && !it.calc && (it.flags.includes("セット品") || KIT_RE.test(it.title)) ? setPartTitles(it.title).filter((p) => !memoVal(load(LS_MEMO, {})[modelKey(p)[0]]).usd).length : 0;
    const where = !it ? "" : kitLeft ? `登録しました。セット・キットの残り ${kitLeft}点の相場を登録すると、合計から判定します` : !it.calc ? "登録した相場がこの出品に当たりませんでした。機種名が違う可能性があります（サトシに知らせてください）" : `判定 ${it.mark}・入札する上限額 ${it.calc.maxBid ? it.calc.maxBid.toLocaleString() + "円" : "なし"}（この行はこの場所に残します）`;
    state.flash = { id, until: Date.now() + 3000 };
    if (row) {
      const det = row.closest("details");
      if (det) det.open = true;
      row.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    applyFlash();
    let t = document.getElementById("yhk-toast");
    if (!t) { t = document.createElement("div"); t.id = "yhk-toast"; document.body.appendChild(t); }
    t.textContent = `登録しました → ${where || "相場に反映"}`;
    t.className = "show";
    clearTimeout(t._h); t._h = setTimeout(() => (t.className = ""), 3500);
  }

  document.addEventListener("change", (ev) => {
    const box = ev.target.closest && ev.target.closest("#yhk input[data-seen]");
    if (!box) return;
    const m = seenAll();
    if (box.checked) m[box.dataset.seen] = Date.now(); else delete m[box.dataset.seen];
    save(LS_SEEN, m);
    const tr = box.closest("tr");
    if (tr) tr.classList.toggle("seen", box.checked);  // 並びは変えない（消さない）
  });

  // ［全て開く］の予備: このページから開く。Chrome は1回のクリックで1つしか開かせないので、開けなかった数を返す
  const openHere = (urls) => urls.filter((u) => !window.open(u, "_blank")).length;
  // 拡張機能を ↻ した後は、開いていたヤフオクのページと拡張機能のつながりが切れる（chrome.runtime.id が無くなる）
  const RELOAD_MSG = (n) => `${n}件を開けませんでした。拡張機能を ↻ した後は、このページを再読み込み（⌘R）してください`;
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest && ev.target.closest("#yhk button");
    if (!btn) return;
    const a = btn.dataset.a, root = document.getElementById("yhk");
    if (a === "min") { root.classList.toggle("min"); save(LS_OPEN, !root.classList.contains("min")); render(); }
    if (a === "all") loadAll(LOAD_STEP);
    if (a === "store") {  // 人気ストア: 全ページを読み終えるまで続けて読む（1000件ずつ。途中も読めた分から表示）
      state.allView = !state.allView; render();
      if (state.allView && !state.done) loadAll(LOAD_STEP);
    }
    if (a === "open3") {  // ヤフオク・sold・now を今のタブの右に、この順で開く
      let urls = [];
      try { urls = JSON.parse(btn.dataset.urls || "[]"); } catch (e) { urls = []; }
      const done = (msg) => { btn.textContent = msg; setTimeout(() => (btn.textContent = "全て開く"), msg.length > 8 ? 8000 : 2000); };
      if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
        try {
          chrome.runtime.sendMessage({ type: "openTabs", urls }, (r) => {
            const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : r && r.error;
            if (!err && r && r.opened >= urls.length) return done("開きました");
            // 裏方で開けなかった分は、このページから開く（予備）。理由はボタンに出す
            const rest = urls.slice((r && r.opened) || 0);
            const ng = openHere(rest);
            done(ng ? RELOAD_MSG(ng) : `開きました（予備の開き方: ${String(err || "途中で止まった").slice(0, 60)}）`);
          });
        } catch (e) { const ng = openHere(urls); done(ng ? RELOAD_MSG(ng) : "開きました（予備の開き方）"); }
      } else { const ng = openHere(urls); done(ng ? RELOAD_MSG(ng) : "開きました"); }
    }
    if (a === "bid") {
      const info = state.bidInfo[btn.dataset.id];
      if (info) { toggleBid(info); render(); }
    }
    if (a === "trust") {
      const id = pageSeller();
      if (id) { setTrusted(id, (document.title || "").replace(/\s*[-|｜].*$/, "").trim(), !trustedIds().has(id)); evaluate(state.items, state.fx); render(); }
    }
    if (a === "memo") {
      const k = btn.dataset.k, box = btn.parentElement;
      const inp = box.querySelector("input[data-memo]") || root.querySelector(`input[data-memo="${CSS.escape(k)}"]`);
      const sel = box.querySelector("select[data-cond]");
      const vNet = Number((inp && inp.value || "").replace(/[$,\s]/g, ""));
      if (!vNet) return;
      const v = Math.round((vNet + SHIP()) * 100) / 100;  // 入力は送料別 → 保存は送料込み
      const memo = load(LS_MEMO, {});
      addMemoHist(k, memo[k], v, sel ? sel.value : "");  // 上書きする前の相場も残す（相場の上がり下がりを見る）
      memo[k] = { usd: v, cond: sel ? sel.value : "", t: Date.now() }; save(LS_MEMO, memo);  // t: 直した時刻（新しい相場メモを優先）
      // 押した行の出品: 選んだ状態＝この出品の状態（入れた金額がそのままこの行の相場になる）。行は今の欄に残す
      const link = btn.closest("tr") && btn.closest("tr").querySelector('a[href*="/auction/"]');
      const id = link ? (link.getAttribute("href").match(/auction\/([^/?#]+)/) || [])[1] : "";
      const it0 = id && state.items.find((x) => x.id === id);
      if (it0) {
        const sure0 = (i) => !!(i.refs && i.refs.length && i.refs[0].tier <= 2);
        const isAcc0 = (i) => i.flags.some((f) => f === "付属品のみ?" || f === "アダプター等");
        (state.pin || (state.pin = {}))[id] = state.pin[id] || naturalSec(it0, isAcc0, sure0);
        setListCond(id, sel ? sel.value : "");
      }
      evaluate(state.items, state.fx); render();
      saveMemoFile();
      if (id) showMoved(id);
    }
    if (a === "hist") {  // 相場の履歴を開く／閉じる（開いた機種は描き直しても開いたまま）
      const h = state.histOpen || (state.histOpen = new Set()), k = btn.dataset.k;
      if (h.has(k)) h.delete(k); else h.add(k);
      render();
    }
    if (a === "desc") { btn.disabled = true; btn.textContent = "確認中…"; checkDescs([btn.dataset.id]); }
    if (a === "memocopy") {
      const csv = memoCsv();
      saveMemoFile();
      await navigator.clipboard.writeText(csv).catch(() => {});
      btn.textContent = "コピーしました";
    }
    if (a === "openGood") {  // 入札候補（◎○）を最大10件まとめて開き、上限額の一覧をコピー（2026-11-18 ユーザー決定）
      const seen = load(LS_SEEN, {});
      // 勝ちやすさ順（2026-11-19）: 1割未満の機種は最後、それ以外は勝ちやすさの高い順（記録が無い機種は目安の割合）→ 今の並び → 終了が近い順
      const wr = (i) => { const w = winOf(i); return w ? w.rate : (DATA.cfg.defaultWinRate ?? 0.2); };
      const good = state.items.filter((i) => (i.mark === "◎" || i.mark === "○") && !seen[i.id])
        .sort((x, y) => isLowWin(x) - isLowWin(y) || wr(y) - wr(x) || bidPrio(x) - bidPrio(y) || (x.end < y.end ? -1 : 1)).slice(0, 10);
      if (!good.length) { btn.textContent = "開く候補がありません"; setTimeout(() => (btn.textContent = "◎○をまとめて開く"), 3000); return; }
      const urls = good.map((i) => `https://auctions.yahoo.co.jp/jp/auction/${i.id}`);
      const txt = good.map((i, n) => `${n + 1}. ${i.mark} 入札上限 ${yen(i.calc.maxBid)}（このまま入札）${i.tax ? `・ストア支払い${yen(Math.round(i.calc.maxBid * (1 + DATA.cfg.storeTaxPct / 100)))}` : ""} ${endStr(i.end)}終了 ${i.title}`).join("\n");
      await navigator.clipboard.writeText(txt).catch(() => {});
      const done = (msg) => { btn.textContent = msg; setTimeout(() => (btn.textContent = "◎○をまとめて開く"), 6000); };
      if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id) {
        try {
          chrome.runtime.sendMessage({ type: "openTabs", urls, max: 10 }, (r) => {
            const err = chrome.runtime.lastError ? chrome.runtime.lastError.message : r && r.error;
            const ng = openHere(urls.slice((r && r.opened) || 0));
            done(ng ? RELOAD_MSG(ng) + "（上限額はコピー済み）" : err ? `${urls.length}件開きました（予備の開き方）・上限額をコピー` : `${urls.length}件開きました・上限額をコピーしました`);
          });
        } catch (e) { const ng = openHere(urls); done(ng ? RELOAD_MSG(ng) + "（上限額はコピー済み）" : `${urls.length}件開きました・上限額をコピー`); }
      } else { const ng = openHere(urls); done(ng ? RELOAD_MSG(ng) + "（上限額はコピー済み）" : `${urls.length}件開きました・上限額をコピー`); }
    }
    if (a === "copy") {
      const good = state.items.filter((i) => i.mark === "◎" || i.mark === "○").sort((x, y) => (x.end < y.end ? -1 : 1));
      const txt = good.map((i) => `${i.mark} ${endStr(i.end)} 入札上限${yen(i.calc.maxBid)}（このまま入札）${i.tax ? `・ストア支払い${yen(Math.round(i.calc.maxBid * (1 + DATA.cfg.storeTaxPct / 100)))}` : ""} 現在${yen(i.price)} ${i.title}\nhttps://auctions.yahoo.co.jp/jp/auction/${i.id}\nsold: ${ebay(i.eq || i.query).sold}\nnow: ${ebay(i.eq || i.query).now}`).join("\n\n");
      await navigator.clipboard.writeText(txt).catch(() => {});
      btn.textContent = "コピーしました";
    }
  });
  document.addEventListener("change", (ev) => {
    if (ev.target.matches && ev.target.matches('#yhk input[data-a="fx"]')) {
      const v = Number(ev.target.value); if (v > 50) { state.fx = v; save(LS_FX, v); evaluate(state.items, state.fx); render(); }
    }
  });

  // ------------------------------------------------------------------ 全ページの読み込み（ヤフオクの既定の件数ずつ、1.5秒間隔）
  // 続きのページを count 件分だけ読む（一度に読むのは最大1000件。ヤフオクに負担をかけないため。続きはボタンで）
  // robots.txt に合わせ、続きのページは Disallow の引数を外した URL で読む（robotsSafeUrl）。開いているページと並び・件数が違うことがあるので、
  // そのときは1ページ目から読み、同じ出品は1回だけ数える（並べ替えは表示のときに拡張機能の中で）
  const LOAD_STEP = 1000, LOAD_DELAY_MS = 1500;
  async function loadAll(count) {
    if (state.loading) return;
    const seen = new Set(state.items.map((i) => i.id));
    const cur = Number(new URL(location.href).searchParams.get("b") || 1);
    const sameQuery = withoutB(robotsSafeUrl(location.href)) === withoutB(location.href);  // 外す引数が無い＝開いているページと同じ並び・件数
    const from = state.next || 1;
    let last = Math.min(state.total || 0, from + Math.min(count, LOAD_STEP) - 1);
    let failed = false;
    try {
      for (let start = from; start <= last;) {
        if (sameQuery && start === cur && state.pageSize) { start += state.pageSize; state.next = start; continue; }  // 今開いているページは読み込み済み
        state.loading = `読み込み中… ${Math.min(start - 1, last)}/${last}件`; render();
        const json = await fetchPage(start);
        const l = json && listingFromNextData(json);
        const size = l ? l.items.length : 0;
        if (!size) { state.next = (state.total || 0) + 1; break; }
        if (!sameQuery) {  // 外した引数で件数が変わることがある → 読んだページの件数に合わせる
          const t = totalFromJson(json);
          if (t != null) { state.total = t; last = Math.min(t, from + Math.min(count, LOAD_STEP) - 1); }
        }
        start += size; state.next = start;
        const fresh = parseItems(json || {}).filter((it) => !seen.has(it.id));
        fresh.forEach((it) => seen.add(it.id));
        state.items.push(...evaluate(fresh, state.fx));  // ページごとに鑑定して順次表示
        await new Promise((r) => setTimeout(r, LOAD_DELAY_MS));
      }
    } catch (e) {
      failed = true;
      alert("読み込みに失敗しました: " + e.message);
    } finally {
      state.loading = ""; state.done = (state.next || 1) > (state.total || 0); render();
      if (state.allView && !state.done && !failed) setTimeout(() => loadAll(LOAD_STEP), 1500);  // 人気ストア: 全ページまで続ける
    }
  }

  // ------------------------------------------------------------------ 起動

  // ------------------------------------------------------------------ キタムラ（shop.kitamura.jp）: 開いているページの商品を判定
  // キタムラへの通信は増やさない（画面に出ている商品を読むだけ）。値札は税込・国内送料0円で計算（店舗アプリと同じ）
  const KIT_PRICE = /([\d,]{3,})\s*円/g;
  function kitItems() {
    const out = new Map();
    for (const a of document.querySelectorAll("a[href]")) {
      if (a.closest("#akit")) continue;
      const href = a.href || "";
      if (!/\/ec\/(?:used|pd)\/[^?#]*\d{4,}/.test(href)) continue;  // 商品ページ（番号付き）だけ
      // 商品のまとまり: 価格（○円）を含む一番近い親（大きすぎるものは除く）
      let box = a, card = null;
      for (let k = 0; k < 7 && box.parentElement; k++) {
        box = box.parentElement;
        const t = box.innerText || "";
        if (t.length > 600 || box.querySelector("#akit")) break;
        const n = (t.match(KIT_PRICE) || []).length;
        if (n > 3) break;  // 価格がたくさん＝商品が何個も入ったまとまり
        if (n) { card = box; break; }
      }
      if (!card) continue;
      const key = href.split("#")[0].split("?")[0];
      const name = [...card.querySelectorAll("a[href]")].map((x) => (x.innerText || "").replace(/\s+/g, " ").trim())
        .concat([(card.querySelector("img[alt]") || {}).alt || ""]).sort((x, y) => y.length - x.length)[0] || "";
      const prices = [...(card.innerText || "").matchAll(KIT_PRICE)].map((m) => Number(m[1].replace(/,/g, ""))).filter((v) => v >= 100);
      if (!name || !prices.length || out.has(key)) continue;
      out.set(key, { url: key, name: name.slice(0, 140), prices: [...new Set(prices)], text: (card.innerText || "").replace(/\s+/g, " ").slice(0, 300) });
    }
    if (out.size) return [...out.values()];
    // 予備: リンクの形が違うページ。「価格:157,200円」の表示から商品のまとまりを探し、その上の行を商品名にする
    const seen = new Set();
    for (const el of document.querySelectorAll("body *")) {
      if (el.closest("#akit") || el.children.length > 3) continue;
      const own = (el.innerText || "").trim();
      if (!/^(?:価格|販売価格|税込)?[:：]?\s*[\d,]{3,}\s*円/.test(own) && !/^価格[:：]/.test(own)) continue;
      let card = el;
      for (let k = 0; k < 6 && card.parentElement; k++) {
        card = card.parentElement;
        const lines = (card.innerText || "").split("\n").map((x) => x.trim()).filter(Boolean);
        if ((card.innerText || "").length > 600 || card.querySelector("#akit") || ((card.innerText || "").match(KIT_PRICE) || []).length > 3) { card = null; break; }
        const pi = lines.findIndex((x) => /[\d,]{3,}\s*円/.test(x));
        const name = lines.slice(0, Math.max(pi, 0)).filter((x) => !/円|^価格|^\S+\([A-Za-z ]+\)$/.test(x)).sort((a, b) => b.length - a.length)[0];
        if (name && name.length >= 6) {
          if (seen.has(card)) break;
          seen.add(card);
          const a = card.querySelector("a[href]");
          const prices = [...(card.innerText || "").matchAll(KIT_PRICE)].map((m) => Number(m[1].replace(/,/g, ""))).filter((v) => v >= 100);
          const url = a ? a.href : location.href;
          if (prices.length && !out.has(url + name)) out.set(url + name, { url, name: name.slice(0, 140), prices: [...new Set(prices)], text: "" });
          break;
        }
      }
    }
    return [...out.values()];
  }
  function kitJudge(x) {
    const refs = allRefs();
    const flags = detectFlags(x.name), q = titleToQuery(x.name);
    let m = matchPreferMemo(q + " " + x.name, refs.filter(isMemo), refs.filter((r) => !isMemo(r)));
    if (!m.length) m = matchKantei(x.name);
    let usd = m.length ? Math.round(median(conditionAdjusted(m, x.name, flags)) * 100) / 100 : null;
    if (usd != null && flags.includes("ジャンク") && DATA.cfg.junkFactor < 1) usd = Math.round(usd * DATA.cfg.junkFactor * 100) / 100;
    const price = Math.min(...x.prices);  // 表示の中の一番安い額（セール・クーポン後）で計算。条件は商品ページで確認
    const it = { price, ship: 0, tax: false, flags };
    // 相場データが無ければ、ヤフオクの仮◎○と同じく eBay 出品中（公式API）の最安値（送料無料は−$30）で仮に計算
    const [label] = modelKey(x.name);
    let tent = null;
    if (usd == null && /\d/.test(label) && label.split(" ").length >= 2) {
      const b = state.browse[label], sg = suggestUsd(b);
      if (sg) {
        usd = flags.includes("ジャンク") && DATA.cfg.junkFactor < 1 ? Math.round(sg * DATA.cfg.junkFactor) : sg;
        tent = { label, b, sg };
      }
    }
    it.calc = usd != null ? calculate(it, usd, state.fx) : null;
    const [mark, reason] = judge(it);
    return Object.assign({}, x, { flags, q, refs: m, usd, price, calc: it.calc, mark, reason, label, tent,
      acc: flags.some((f) => f === "付属品のみ?" || f === "アダプター等") });
  }
  function kitRender() {
    const all = kitItems().map(kitJudge);
    let root = document.getElementById("akit");
    if (!root) {
      const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
      root = document.createElement("div"); root.id = "akit"; document.body.appendChild(root);
    }
    const rank = (i) => (i.acc ? 9 : i.mark === "◎" ? 0 : i.mark === "○" ? 1 : i.mark === "要確認" && i.calc ? 2 : i.calc ? 3 : 4);
    const list = all.sort((a, b) => rank(a) - rank(b) || (b.calc && b.calc.profit || 0) - (a.calc && a.calc.profit || 0));
    const buy = list.filter((i) => rank(i) <= 1).length, buyWeak = list.filter((i) => rank(i) <= 1 && (i.tent || (i.refs[0] && i.refs[0].tier >= 3))).length;
    const tier = (r) => (r == null ? "" : ["自分の販売実績", "Chatwork実績", "頒布会リスト", "鑑定表・リサーチ（推定）"][r] || "");
    const row = (i) => {
      const c = i.acc ? null : i.calc, r = i.refs[0] || {}, e = ebay(i.q);
      const cls = i.mark === "◎" ? "g" : i.mark === "○" ? "o" : "x";
      const weak = c && (i.tent || r.tier >= 3);  // 根拠が仮・鑑定表 → sold で確認してから（頒布会は 2026-11-18 から売れた記録）
      return `<tr class="${rank(i) >= 3 ? "kdim" : ""}"><td class="m ${cls}">${i.acc ? "除外" : i.calc ? `${i.tent ? '<span class="tmark">仮</span>' : ""}${esc(i.mark)}` : "－"}</td>
<td><a class="t" href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.name)}</a>${weak && rank(i) <= 1 ? '<span class="flag">sold で確認してから</span>' : ""}${i.flags.filter((f) => !["動作品", "美品"].includes(f)).map((f) => `<span class="flag">${esc(f)}</span>`).join("")}
<div class="s">${i.acc ? "付属品・アダプターなどのため対象外"
      : i.tent ? `仮: 相場データなし → eBay出品中（送料別）最安${nd(i.tent.b.min || 0)}・中央値${nd(i.tent.b.med)}から案 ${nd(i.tent.sg)} で計算（sold未確認） ・ ${esc(i.reason || "")}`
      : i.calc ? `根拠: ${esc(r.title || "")} [${esc(tier(r.tier))}] ・ ${esc(i.reason || "")}`
      : canBrowse() && /\d/.test(i.label) && i.label.split(" ").length >= 2 && !state.browse[i.label] ? "相場データなし → eBay出品中の価格を調べています…"
      : "相場データなし（sold で確認。eBay出品中も件数が少ないか、機種名が読み取れません）"}</div></td>
<td class="n">${i.usd != null && !i.acc ? `${nd(i.usd)}<div class="s">送料別</div>${soldLine(r)}` : "-"}<div class="pills"><a class="pill sold" href="${e.sold}" target="_blank" rel="noopener">sold</a><a class="pill" href="${e.now}" target="_blank" rel="noopener">now</a></div></td>
<td class="n">${yen(i.price)}${i.prices.length > 1 ? `<div class="s">表示: ${i.prices.map((v) => yen(v)).join(" / ")}</div>` : ""}</td>
<td class="n"><b>${yen(c && c.maxBid)}</b>${c ? '<div class="s">この値段（税込）以下なら買い</div>' : ""}</td>
<td class="n">${yen(c && c.profit)}<div class="s">${c && c.margin != null ? c.margin + "%" : ""}</div></td></tr>`;
    };
    const need = [...new Set(list.filter((i) => !i.acc && !i.refs.length && !i.tent && /\d/.test(i.label) && i.label.split(" ").length >= 2 && !state.browse[i.label]).map((i) => i.label))];
    if (need.length && canBrowse()) setTimeout(() => fetchBrowse(need.slice(0, 40), kitRender), 0);
    root.innerHTML = `<div class="hd"><b>キタムラ鑑定</b><span>このページの商品 ${list.length}件 ・ 買い候補 ${buy}件${buyWeak ? `（うち sold で確認 ${buyWeak}件）` : ""}</span><span class="sp"></span>
<button data-k="re">読み直す</button><button data-k="min">${root.classList.contains("min") ? "開く" : "最小化"}</button></div>
<div class="bd"><div class="s">画面に出ている商品だけを読みます（キタムラへの通信はしません）。値札は税込・国内送料0円で計算。表示に複数の価格があるときは一番安い額で計算しています。クーポンの条件は商品ページで確認してください。基準 利益${DATA.cfg.minProfit.toLocaleString()}円以上・利益率${DATA.cfg.minMarginPct}%以上 ・ 為替 ${state.fx}円 ・ 相場データ ${DATA.refs.length}件（${esc(DATA.built)}作成）</div>
${list.length ? `<table><tr><th>判定</th><th>商品（→キタムラ）</th><th>eBay相場（送料別）</th><th>キタムラの価格</th><th>買ってよい上限（税込）</th><th>利益（この価格で買ったら）</th></tr>${list.map(row).join("")}</table>`
    : `<div class="s" style="margin-top:8px">商品を見つけられませんでした。ページを下までスクロールしてから［読み直す］を押してください。それでも出ないときは、この画面のスクショを Claude に送ってください。</div>`}</div>`;
  }
  function kitStart() {
    state.fx = load(LS_FX, DATA.cfg.fx) || DATA.cfg.fx;
    let t = null;
    const later = () => { clearTimeout(t); t = setTimeout(kitRender, 1200); };
    later();
    new MutationObserver((ms) => { if (ms.some((m) => !(m.target.closest && m.target.closest("#akit")))) later(); })
      .observe(document.body, { childList: true, subtree: true });
    document.addEventListener("click", (ev) => {
      const b = ev.target.closest && ev.target.closest("#akit button");
      if (!b) return;
      if (b.dataset.k === "re") kitRender();
      if (b.dataset.k === "min") { document.getElementById("akit").classList.toggle("min"); kitRender(); }
    });
  }

  function start() {
    if (location.hostname === "shop.kitamura.jp") { kitStart(); return; }  // キタムラのネットショップ
    if (location.pathname.startsWith("/follow/list")) {  // フォロー中の一覧: 出品者をまとめて安心ストアに（一覧は後から描かれるので少し待つ）
      let n = 0;
      for (const ms of [800, 2500, 5000]) setTimeout(() => { n = Math.max(n, trustFollowList()); }, ms);
      return;
    }
    const el = document.getElementById("__NEXT_DATA__");
    if (!el) return;
    const json = JSON.parse(el.textContent);
    state.items = parseItems(json);
    state.total = totalFromJson(json);
    { const l = listingFromNextData(json); state.pageSize = l ? l.items.length : 0; }  // 開いているページの件数（続きのページ送りの幅）
    state.done = !state.total || state.total <= state.items.length;
    if (!state.items.length) return;
    if (migrateMemo()) saveMemoFile();  // 直した相場メモを ダウンロード/aurora_price_memo.csv へ（Mac が取り込む）
    autoTrustFollowed();
    evaluate(state.items, state.fx);
    render();
    setTimeout(() => { if (autoTrustFollowed()) { evaluate(state.items, state.fx); render(); } }, 2500);  // ボタンが後から出るページ用
    if (!state.done) loadAll(Math.min(DATA.cfg.autoMaxItems || LOAD_STEP, LOAD_STEP));  // 開いたら自動で上限（config.toml）まで全ページを鑑定
  }

  start();
  }
})(/*__DATA__*/ null);
