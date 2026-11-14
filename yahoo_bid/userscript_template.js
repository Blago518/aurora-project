// ==UserScript==
// @name         ヤフオク入札鑑定
// @namespace    aurora-research
// @version      __VERSION__
// @description  ヤフオクの検索結果・出品者ページを開くと、販売実績・相場データと照合して上限入札額と利益を表示します
// @match        https://auctions.yahoo.co.jp/search/*
// @match        https://auctions.yahoo.co.jp/seller/*
// @match        https://auctions.yahoo.co.jp/category/*
// @run-at       document-idle
// @grant        none
// @noframes
// ==/UserScript==
// このファイルは build_userscript.py で自動生成されます。直接編集せず、data/ と config.toml を更新して作り直してください。
(function () {
  "use strict";
  const DATA = /*__DATA__*/ null;
  const C = DATA.consts;
  const LS_MEMO = "yhkMemoV2", LS_FX = "yhkFxV2", LS_OPEN = "yhkOpenV2";

  // ------------------------------------------------------------------ 文字列処理（bid_analyzer.py と同じ規則）
  const BRAND_SET = new Set(Object.values(C.brandsEn).map((b) => b.toLowerCase()));
  const NOISE = new Set(C.noise), THIRD = new Set(C.thirdParty), COLORS = new Set(C.colors);
  const GENS = new Set(C.generations), PREMIUM = new Set(C.premium);
  const ACCESSORY_RE = new RegExp(C.accessoryRe, "i");
  const FLAG_RES = C.flagRules.map(([name, pat]) => [name, new RegExp(pat, "i")]);
  const KANTEI = DATA.kantei.map((r) => ({ ...r, re: r.patterns.map((p) => { try { return new RegExp(p, "i"); } catch (e) { return null; } }) }));

  function titleToQuery(title, maxTokens = 7) {
    let t = title.normalize("NFKC");
    t = t.replace(/\/[A-Z]{1,2}\d{3}\s*$/, " ");
    t = t.replace(/\d+円[~〜～]?/g, " ");
    t = t.replace(/\b[wW]\s*\/\s*/g, " ");
    t = t.replace(/(\d)\s*(mm|㎜)\s*([fF]\s*\/?\s*\d)/g, "$1mm $3");
    t = t.replace(/[①-⑳]/g, " ");
    t = t.replace(/非\s*-?\s*A[iI](?![-sS])/g, " nonAi ");
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
    q = q.replace(/(\d)\s*mm/g, "$1mm").replace(/\bf\s*\/\s*(\d)/g, "f$1").replace(/[()\[\]+&]/g, " ");
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
  const pick = (set, pool) => [...set].filter((x) => pool.has(x)).sort().join("|");

  function detectFlags(title) {
    return FLAG_RES.filter(([, re]) => re.test(title)).map(([n]) => n);
  }

  function matchRefs(query, refs) {
    const qt = tokensOf(query);
    if (!qt.size) return [];
    const qmm = has(qt, (t) => t.endsWith("mm"));
    const qAcc = ACCESSORY_RE.test(query);
    const scored = [];
    for (const r of refs) {
      const rt = r.tset || (r.tset = new Set(r.tokens));
      if ([...qt].some((t) => THIRD.has(t) && !rt.has(t))) continue;
      const rb = [...rt].filter((t) => BRAND_SET.has(t));
      if (rb.length && !rb.some((t) => qt.has(t))) continue;
      if (qmm !== has(rt, (t) => t.endsWith("mm"))) continue;
      if (r.acc && !qAcc) continue;
      if (pick(qt, PREMIUM) !== pick(rt, PREMIUM)) continue;
      if ((qt.has("nonai") && (rt.has("ai") || rt.has("ai-s"))) || (rt.has("nonai") && (qt.has("ai") || qt.has("ai-s")))) continue;
      if (pick(qt, GENS) !== pick(rt, GENS)) continue;
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
    const qmm = has(qt, (x) => x.endsWith("mm"));
    for (const r of KANTEI) {
      if (!r.re.every((re) => re && re.test(t))) continue;
      const rt = new Set(r.tokens);
      if ([...qt].some((x) => THIRD.has(x) && !rt.has(x))) continue;
      if (qt.has("nonai") && (rt.has("ai") || rt.has("ai-s"))) continue;
      if (qmm !== has(rt, (x) => x.endsWith("mm"))) continue;
      const model = [...rt].filter((x) => /\d/.test(x) && !x.endsWith("mm") && !x.startsWith("f"));
      if (model.length && !model.every((x) => qt.has(x))) continue;
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
    const k = (1 + g.storeTaxPct / 100) * (1 - g.refundRatio);
    const fixed = net - dom - intl - g.packing;
    const maxBid = Math.max(0, roundDown(Math.min((fixed - g.minProfit) / k, (fixed - g.minMarginPct / 100 * revJpy) / k)));
    const be = Math.max(0, roundDown(fixed / k));
    const out = { maxBid, breakeven: be, fees: Math.round(fees * fx), intl, dom, net: Math.round(net), netUsd: usd - g.deductionUsd };
    if (it.price != null) {
      const p = fixed - it.price * k;
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
    if (it.price <= c.maxBid * 0.8) return ["◎", pre + "余裕あり"];
    if (it.price <= c.maxBid) return ["○", pre + "上限に近い"];
    return ["×", pre + "上限超過"];
  }

  // ------------------------------------------------------------------ 相場メモ（このブラウザに保存）
  const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch (e) { return d; } };
  const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  function memoRefs() {
    return Object.entries(load(LS_MEMO, {})).map(([title, usd]) => ({
      title, price: Number(usd), tier: 0, source: "相場メモ", tokens: [...tokensOf(title)], acc: false,
    }));
  }

  function modelKey(title) {
    const toks = titleToQuery(title, 20).split(" ").filter(Boolean);
    const brand = toks.length && BRAND_SET.has(toks[0].toLowerCase()) ? toks[0] : "";
    const rest = brand ? toks.slice(1) : toks;
    const keyToks = rest.filter((t) => /\d/.test(t)).slice(0, 3);
    const label = (brand ? [brand] : []).concat(keyToks.length ? keyToks : rest.slice(0, 2)).join(" ");
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
    return l.items.filter((x) => !x.isFleamarketItem).map((x) => ({
      id: x.auctionId, title: x.title || "", price: x.price ?? null, buyNow: x.buyNowPrice || null,
      bids: x.bidCount ?? 0, end: x.endTime || "", ship: x.isFreeShipping ? 0 : (x.currentPriceShippingFee ?? null),
      watch: x.watchCount ?? 0, store: !!(x.seller && x.seller.isStore),
    }));
  }

  function totalFromJson(json) {
    const m = JSON.stringify(json).match(/"totalResultsAvailable":(\d+)/);
    return m ? Number(m[1]) : null;
  }

  async function fetchPage(start) {
    const u = new URL(location.href);
    u.searchParams.set("b", String(start));
    u.searchParams.set("n", "100");
    const html = await (await fetch(u.toString(), { credentials: "include" })).text();
    const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
    return m ? JSON.parse(m[1]) : null;
  }

  // ------------------------------------------------------------------ 鑑定
  function evaluate(items, fx) {
    const refs = DATA.refs.concat(memoRefs());
    for (const it of items) {
      it.flags = detectFlags(it.title);
      it.query = titleToQuery(it.title);
      let m = matchRefs(it.query + " " + it.title, refs);
      if (!m.length) m = matchKantei(it.title);
      it.refs = m;
      it.usd = m.length ? median(m.map((r) => r.price)) : null;
      if (it.usd != null && it.flags.includes("ジャンク") && DATA.cfg.junkFactor < 1)
        it.usd = Math.round(it.usd * DATA.cfg.junkFactor * 100) / 100;
      it.calc = it.usd != null ? calculate(it, it.usd, fx) : null;
      [it.mark, it.reason] = judge(it);
    }
    return items;
  }
  const median = (a) => { const s = [...a].sort((x, y) => x - y), n = s.length; return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2; };

  // ------------------------------------------------------------------ 表示
  const yen = (v) => (v == null ? "-" : v.toLocaleString("ja-JP") + "円");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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

  const css = `
#yhk{position:fixed;top:8px;right:8px;z-index:2147483647;width:min(860px,calc(100vw - 16px));max-height:calc(100vh - 16px);overflow:auto;
background:#fff;color:#14263a;border-radius:14px;box-shadow:0 10px 40px #0004;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Hiragino Sans",sans-serif}
#yhk.min{width:auto}#yhk.min .bd{display:none}
#yhk .hd{position:sticky;top:0;background:#0b1728;color:#fff;padding:9px 12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#yhk .hd b{font-size:14px}#yhk .hd .sp{flex:1}
#yhk button{border:0;border-radius:8px;padding:6px 10px;background:#e8f2f8;color:#14415a;font-weight:700;cursor:pointer;font-size:12px}
#yhk .hd button{background:#24496a;color:#fff}
#yhk .bd{padding:10px 12px}#yhk table{width:100%;border-collapse:collapse}
#yhk th,#yhk td{padding:5px 6px;border-bottom:1px solid #e6ecf2;text-align:left;vertical-align:top}
#yhk th{font-size:11px;color:#5d7083;font-weight:600}#yhk td.n{text-align:right;white-space:nowrap}
#yhk a{color:#0b5d8c;text-decoration:none}#yhk .m{font-size:15px;font-weight:800}
#yhk .g{color:#1f7a5c}#yhk .o{color:#1b6387}#yhk .x{color:#a4473a}#yhk .s{font-size:11px;color:#6a7c8e}
#yhk details{margin-top:10px}#yhk summary{cursor:pointer;font-weight:700}
#yhk input{width:72px;padding:4px;border:1px solid #c6d3de;border-radius:6px}
#yhk a.t{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-weight:600}
#yhk a.usd{font-size:15px;font-weight:800;text-decoration:underline dotted}
#yhk a.now{display:inline-block;margin-top:2px;font-size:11px;padding:0 6px;border:1px solid #9fc0d4;border-radius:6px}
#yhk td.m{white-space:nowrap;width:58px}#yhk tr.soon td{background:#fff6ec}#yhk .hot{color:#c0392b;font-weight:700}
#yhk .flag{background:#fff3d6;color:#7a5a12;border-radius:6px;padding:0 5px;font-size:11px;margin-left:4px}
.yhk-badge{display:inline-block;margin:2px 0;padding:2px 7px;border-radius:7px;font:700 12px -apple-system,sans-serif;color:#fff}
.yhk-badge.g{background:#1f7a5c}.yhk-badge.o{background:#1b6387}.yhk-badge.x{background:#9aa6b2}
@media (prefers-color-scheme:dark){#yhk tr.soon td{background:#2a2216}#yhk{background:#152231;color:#e2eaf2}#yhk th,#yhk td{border-color:#26394b}#yhk a{color:#7cc4ee}
#yhk button{background:#24384c;color:#d6e6f2}#yhk input{background:#0f1a26;color:#e2eaf2;border-color:#36506a}}`;

  let state = { items: [], total: null, fx: Number(load(LS_FX, DATA.cfg.fx)) || DATA.cfg.fx, loading: "" };

  function render() {
    let root = document.getElementById("yhk");
    if (!root) {
      const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
      root = document.createElement("div"); root.id = "yhk"; document.body.appendChild(root);
      if (!load(LS_OPEN, true)) root.classList.add("min");
    }
    const items = state.items;
    const good = items.filter((i) => i.mark === "◎" || i.mark === "○").sort((a, b) => (a.end < b.end ? -1 : 1));
    const check = items.filter((i) => i.mark === "要確認" && i.calc);
    const bad = items.filter((i) => i.mark === "×");
    const priced = items.filter((i) => i.calc).length;
    const groups = new Map();
    for (const i of items) {
      if (i.calc || i.flags.some((f) => ["付属品のみ?", "アダプター等", "セット品"].includes(f))) continue;
      const [label, key] = modelKey(i.title);
      if (label.split(" ").length < 2) continue;
      const g = groups.get(key) || { label, n: 0, prices: [], ex: i.title };
      g.n++; if (i.price) g.prices.push(i.price); groups.set(key, g);
    }
    const unpriced = [...groups.values()].sort((a, b) => b.n - a.n).slice(0, 40);
    const more = !state.done && state.total && state.total > 100;

    const row = (i) => {
      const c = i.calc, r = i.refs[0] || {}, e = ebay(i.query);
      const cls = i.mark === "◎" ? "g" : i.mark === "○" ? "o" : "x";
      const junk = i.flags.includes("ジャンク") && DATA.cfg.junkFactor < 1;
      const flags = i.flags.filter((f) => !["動作品", "美品"].includes(f)).map((f) => `<span class="flag">${esc(f)}</span>`).join("");
      const tip = `eBay検索語: ${i.query}（価格+送料の高い順）\n根拠: ${r.title || ""} $${r.price} [${r.source || ""}]` +
        (junk ? `\nジャンク評価 ×${Math.round(DATA.cfg.junkFactor * 100)}% → $${i.usd}` : "") + `\n送料$${DATA.cfg.deductionUsd}差引後 Net $${c ? c.netUsd.toFixed(0) : "-"}`;
      const [left, soon] = remain(i.end);
      return `<tr class="${soon ? "soon" : ""}"><td class="m ${cls}">${i.mark}<div class="s ${soon ? "hot" : ""}">${left}</div></td>
<td><a class="t" href="https://auctions.yahoo.co.jp/jp/auction/${i.id}" target="_blank" rel="noopener" title="${esc(i.title)}">${esc(i.title)}</a>${flags}
<div class="s">根拠: ${esc(r.title || "")} [${esc(srcLabel(r))}]</div></td>
<td class="n"><a class="usd" href="${e.sold}" target="_blank" rel="noopener" title="${esc(tip)}">$${i.usd}</a>${junk ? `<div class="s">元$${r.price}</div>` : ""}
<div><a class="now" href="${e.now}" target="_blank" rel="noopener" title="eBay出品中（高い順）">now</a></div></td>
<td class="n">${yen(i.price)}<div class="s">入札${i.bids}・送${i.ship == null ? "?" : yen(i.ship)}</div></td>
<td class="n"><b>${yen(c && c.maxBid)}</b><div class="s">分岐 ${yen(c && c.breakeven)}</div></td>
<td class="n">${yen(c && c.profit)}<div class="s">${c && c.margin != null ? c.margin + "%" : ""}</div></td></tr>`;
    };
    const head = `<tr><th>判定</th><th>商品（タイトル→ヤフオク）</th><th title="金額→eBay販売済み（高い順）">eBay相場</th><th>現在</th><th>上限入札</th><th>利益(現在)</th></tr>`;

    root.innerHTML = `<div class="hd"><b>入札鑑定</b><span>${items.length}${state.total ? "/" + state.total : ""}件 ・ 相場あり ${priced}件 ・ ◎○ ${good.length}件</span>
<span class="sp"></span>${state.loading ? `<span>${esc(state.loading)}</span>` : more ? `<button data-a="all">全ページ鑑定（残り${state.total - items.length}件）</button>` : ""}
<button data-a="copy">◎○をコピー</button><button data-a="min">${root.classList.contains("min") ? "開く" : "最小化"}</button></div>
<div class="bd"><div class="s">為替 1USD=<input data-a="fx" value="${state.fx}">円 ・ 基準 利益${DATA.cfg.minProfit.toLocaleString()}円以上・利益率${DATA.cfg.minMarginPct}%以上 ・ 手数料${DATA.cfg.feePct.toFixed(2)}%+$${DATA.cfg.perOrderUsd} ・ 送料$${DATA.cfg.deductionUsd}差引 ・ ジャンク${Math.round(DATA.cfg.junkFactor * 100)}%評価 ・ 相場データ ${DATA.refs.length}件（${esc(DATA.built)}作成）</div>
<h4 style="margin:10px 0 4px">入札候補（終了が近い順）</h4>
${good.length ? `<table>${head}${good.map(row).join("")}</table>` : `<div class="s">このページには基準を満たす出品がありません。</div>`}
<details><summary>要確認（相場はあるが付属品・セット等の可能性） ${check.length}件</summary><table>${head}${check.map(row).join("")}</table></details>
<details><summary>× 見送り ${bad.length}件</summary><table>${head}${bad.map(row).join("")}</table></details>
<details ${priced < items.length / 3 ? "open" : ""}><summary>相場が未登録の機種（件数順） ${groups.size}種類 ― 想定$を入れて登録すると即反映</summary>
<table><tr><th>機種（→eBay販売済み）</th><th>件数</th><th>ヤフオク現在値</th><th></th><th>想定$（送料込）</th></tr>${unpriced.map((g) => `<tr><td><a href="${ebay(g.label).sold}" target="_blank" rel="noopener" title="eBay販売済み（高い順）">${esc(g.label)}</a> <a class="now" href="${ebay(g.label).now}" target="_blank" rel="noopener">now</a><div class="s">${esc(g.ex.slice(0, 60))}</div></td>
<td class="n">${g.n}</td><td class="n">${yen(g.prices.length ? median(g.prices) : null)}</td>
<td><a href="${ebay(g.label).pr}" target="_blank" rel="noopener" title="Product Research（Best Offer込みの成約）">PR</a></td>
<td><input data-memo="${esc(g.label)}" placeholder="$"><button data-a="memo" data-k="${esc(g.label)}">登録</button></td></tr>`).join("")}</table>
<div style="margin-top:6px"><button data-a="memocopy">登録した相場メモをコピー（${Object.keys(load(LS_MEMO, {})).length}件）</button>
<span class="s">コピーした内容を data/price_memo.csv に貼るか Claude に送ると、全端末の相場データに取り込めます</span></div></details></div>`;
    badges();
  }

  function badges() {
    for (const i of state.items) {
      if (!i.calc) continue;
      const a = document.querySelector(`a[href*="/auction/${i.id}"]:not(#yhk a)`);
      if (!a || a.parentElement.querySelector(".yhk-badge")) continue;
      const b = document.createElement("span");
      b.className = "yhk-badge " + (i.mark === "◎" ? "g" : i.mark === "○" ? "o" : "x");
      b.textContent = `${i.mark} 上限${yen(i.calc.maxBid)}`;
      a.insertAdjacentElement("afterend", b);
    }
  }

  // Node でのテスト用
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { titleToQuery, tokensOf, detectFlags, matchRefs, matchKantei, calculate, judge, evaluate, parseItems, modelKey, roundDown };
    return;
  }

  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest && ev.target.closest("#yhk button");
    if (!btn) return;
    const a = btn.dataset.a, root = document.getElementById("yhk");
    if (a === "min") { root.classList.toggle("min"); save(LS_OPEN, !root.classList.contains("min")); render(); }
    if (a === "all") {
      const seen = new Set(state.items.map((i) => i.id));
      try {
        for (let start = 101; start <= (state.total || 0) && start <= 5001; start += 100) {
          state.loading = `読み込み中… ${start - 1}/${state.total}`; render();
          const json = await fetchPage(start);
          const fresh = parseItems(json || {}).filter((it) => !seen.has(it.id));
          fresh.forEach((it) => seen.add(it.id));
          state.items.push(...evaluate(fresh, state.fx));  // ページごとに鑑定して順次表示
          await new Promise((r) => setTimeout(r, 800));
        }
      } catch (e) {
        alert("読み込みに失敗しました: " + e.message);
      } finally {
        state.loading = ""; state.done = true; render();
      }
    }
    if (a === "memo") {
      const k = btn.dataset.k, inp = root.querySelector(`input[data-memo="${CSS.escape(k)}"]`);
      const v = Number((inp && inp.value || "").replace(/[$,\s]/g, ""));
      if (!v) return;
      const memo = load(LS_MEMO, {}); memo[k] = v; save(LS_MEMO, memo);
      evaluate(state.items, state.fx); render();
    }
    if (a === "memocopy") {
      const memo = load(LS_MEMO, {});
      const csv = "title,sold_price_usd,source\n" + Object.entries(memo).map(([k, v]) => `"${k}",${v},相場メモ`).join("\n");
      await navigator.clipboard.writeText(csv).catch(() => {});
      btn.textContent = "コピーしました";
    }
    if (a === "copy") {
      const good = state.items.filter((i) => i.mark === "◎" || i.mark === "○").sort((x, y) => (x.end < y.end ? -1 : 1));
      const txt = good.map((i) => `${i.mark} ${endStr(i.end)} 上限${yen(i.calc.maxBid)} 現在${yen(i.price)} ${i.title}\nhttps://auctions.yahoo.co.jp/jp/auction/${i.id}\nsold: ${ebay(i.query).sold}\nnow: ${ebay(i.query).now}`).join("\n\n");
      await navigator.clipboard.writeText(txt).catch(() => {});
      btn.textContent = "コピーしました";
    }
  });
  document.addEventListener("change", (ev) => {
    if (ev.target.matches && ev.target.matches('#yhk input[data-a="fx"]')) {
      const v = Number(ev.target.value); if (v > 50) { state.fx = v; save(LS_FX, v); evaluate(state.items, state.fx); render(); }
    }
  });

  // ------------------------------------------------------------------ 起動
  function start() {
    const el = document.getElementById("__NEXT_DATA__");
    if (!el) return;
    const json = JSON.parse(el.textContent);
    state.items = parseItems(json);
    state.total = totalFromJson(json);
    state.done = !state.total || state.total <= 100 || new URL(location.href).searchParams.get("b") > "1";
    if (!state.items.length) return;
    evaluate(state.items, state.fx);
    render();
  }

  start();
})();
