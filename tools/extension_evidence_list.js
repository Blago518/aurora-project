// ヤフオクの仕入れ証跡をまとめて保存（2026-11-26 オーナー「俺がそのページを開いたら、取得するものをつくったりできないの？」）。
// 落札分の一覧（auctions.yahoo.co.jp/my/won だけ）に取引ナビへのリンクがあれば、まだ証跡の無い物を
// 裏方（background.js）が1件ずつ前のタブで開き、取引ナビの evidence.js が画面を PNG で保存したらそのタブを閉じる。
// 2026-11-26 落札分の一覧で［取引連絡］のある5件だけ保存して止まった。取引が終わった物は［評価］だけで取引ナビのリンクが無い。
// → 同じページの取引ナビのリンク（出品者・落札者の引数付き）を型にして、行の中のリンクから出品者のIDを拾い、オークションIDと入れ替えて作る。
// 開くのはリンクの先（取引ナビ）を見るだけ。取引ナビの中のボタン（連絡・受け取り連絡・評価）は押さない。

// @evlist-start（tools/test_evidence.py がこの間を node で試す）
// 2026-11-26 本物の取引ナビのリンクは /trade/bundle?aid=◯&seller_auc_user_id=◯&buyer_auc_user_id=◯（まとめて取引の画面。
// オークションID・出品者が出ないので証跡にならない）。同じ引数で /trade/top を開く（1件ずつの取引ナビ）。
const NAVI_RE = /^https:\/\/contact\.auctions\.yahoo\.co\.jp\//;
const SELLER_KEYS = ["seller_auc_user_id", "syid", "sellerid", "seller", "targetid", "target", "userid", "user", "u", "id"];

function aidOf(url) {
  return ((String(url || "").match(/[?&](?:aid|aucid|auctionid)=([a-z]?\d{8,12})(?:&|$|#)/i) || [])[1] || "").toLowerCase();
}

// まとめて取引（bundle）の URL → 1件ずつの取引ナビ（top）の URL
function topUrl(url) {
  return String(url).replace(/^(https:\/\/contact\.auctions\.yahoo\.co\.jp)\/trade\/bundle(?=[?#]|$)/, "$1/trade/top");
}

function navLinks(hrefs) {
  const out = [], seen = new Set();
  // 1件ずつの取引ナビ（top）のリンクを、まとめて取引（bundle）より先に使う
  const list = (hrefs || []).map(String).sort((a, b) => /\/bundle/.test(a) - /\/bundle/.test(b));
  for (const s of list) {
    if (!NAVI_RE.test(s)) continue;
    const id = aidOf(s);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, url: topUrl(s) });
  }
  return out;
}

// 型の URL で出品者を表す引数の名前（seller_auc_user_id・syid）と、自分（落札者）の値
function tplKeys(url) {
  let u;
  try { u = new URL(url); } catch (e) { return null; }
  const keys = Array.from(u.searchParams.keys());
  const sk = keys.find((k) => /seller|^syid$/i.test(k));
  const bk = keys.find((k) => /buyer|^bid$/i.test(k));
  return sk ? { sk, mine: bk ? u.searchParams.get(bk) : "" } : null;
}

// 行の中のリンク（評価・出品者のページなど）から出品者の ID を探す
function sellerOf(hrefs, mine, sk) {
  const keys = [String(sk || "").toLowerCase()].concat(SELLER_KEYS);
  for (const h of hrefs || []) {
    let u;
    try { u = new URL(h); } catch (e) { continue; }
    if (NAVI_RE.test(h) && !/[?&]aid=/i.test(h)) continue;
    for (const [k, v] of u.searchParams) {
      if (keys.includes(k.toLowerCase()) && v && v !== mine && !/^[a-z]?\d{8,12}$/i.test(v)) return v;
    }
    const m = u.pathname.match(/\/(?:seller|user|rating\/list|jp\/show\/rating)\/([^/?#]+)/);
    if (m && m[1] !== mine && !/^[a-z]?\d{8,12}$/i.test(m[1])) return decodeURIComponent(m[1]);
  }
  return "";
}

// ページの HTML（script の中の JSON も含む）から取引ナビの URL を拾う（画面に出ていない行・まだ読み込まれていない行の分）
function naviUrlsIn(html) {
  const t = String(html || "").replace(/\\u0026/gi, "&").replace(/&amp;/g, "&").replace(/\\\//g, "/");
  return Array.from(new Set(t.match(/https:\/\/contact\.auctions\.yahoo\.co\.jp\/[^"'<>\s\\]+/g) || []));
}

// 次のページへのリンク（「次へ」「次のページ」・rel=next）
function nextPageUrl(anchors, here) {
  for (const a of anchors || []) {
    const text = String(a.text || "").replace(/\s+/g, "");
    if ((a.rel === "next" || /^(次へ|次のページ|次の\d+件|次|>|›|»)$/.test(text)) && a.href && a.href !== here && /auctions\.yahoo\.co\.jp/.test(a.href)) return a.href;
  }
  return "";
}

// 落札分の一覧の行: [{id, hrefs}] → 開く物 [{id, url, made}]
function planLinks(rows, pageHrefs) {
  const direct = navLinks(pageHrefs);
  let tpl = null, keys = null;
  for (const l of direct) { keys = tplKeys(l.url); if (keys) { tpl = l; break; } }
  const out = direct.slice(), seen = new Set(direct.map((l) => l.id));
  let noSeller = 0;
  const noSellerIds = [];
  for (const r of rows || []) {
    const id = String(r.id || "").toLowerCase();
    if (!id || seen.has(id)) continue;
    const own = navLinks(r.hrefs);
    if (own.length) { out.push(own[0]); seen.add(id); continue; }
    const sy = tpl ? sellerOf(r.hrefs, keys.mine, keys.sk) : "";
    if (!sy) { noSeller += 1; noSellerIds.push(id); continue; }
    const u = new URL(tpl.url);
    u.searchParams.set("aid", id);
    u.searchParams.set(keys.sk, sy);
    out.push({ id, url: u.toString(), made: true });
    seen.add(id);
  }
  return { links: out, direct: direct.length, made: out.filter((l) => l.made).length, noSeller, noSellerIds, template: !!tpl };
}
// @evlist-end

(function () {
  if (typeof document === "undefined" || typeof chrome === "undefined" || !chrome.runtime) return;
  if (!/^\/my\/won/.test(location.pathname)) return;   // 落札分の一覧のときだけ動く（検索・ストアの画面は動かさない）
  const sent = new Set();
  const tag = document.createElement("div");
  tag.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#14214a;color:#fff;padding:10px 14px;border-radius:8px;font:13px sans-serif;opacity:.95;max-width:360px;white-space:pre-line";
  const show = (t) => { tag.textContent = t; if (!tag.isConnected) document.body.appendChild(tag); };
  let info = "", pages = 1;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 行（「商品ID：◯」ごと）を読む。root はこのページか、裏で読んだ次のページ
  const rowsIn = (root) => {
    const rows = [], seen = new Set();
    // script の中の文字（__NEXT_DATA__ の JSON など）は行ではないので読まない
    const walker = root.createTreeWalker(root.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement && /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(n.parentElement.tagName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    while (walker.nextNode()) {
      const m = walker.currentNode.nodeValue.match(/商品ID\s*[:：]\s*([a-z]?\d{8,12})/i);
      if (!m) continue;
      const id = m[1].toLowerCase();
      if (seen.has(id)) continue;
      seen.add(id);
      // その商品IDだけを含むいちばん外側の枠を、その行とみなす
      let el = walker.currentNode.parentElement;
      while (el.parentElement && (el.parentElement.textContent.match(/商品ID\s*[:：]/g) || []).length <= 1) el = el.parentElement;
      rows.push({ id, hrefs: Array.from(el.querySelectorAll("a[href]"), (a) => a.href) });
    }
    return rows;
  };
  const totals = { rows: 0, direct: 0, made: 0, noSeller: 0 };
  const queue = (root, html) => {
    const rows = rowsIn(root);
    const hrefs = Array.from(root.querySelectorAll("a[href]"), (a) => a.href).concat(naviUrlsIn(html));
    const plan = planLinks(rows, hrefs);
    totals.rows += rows.length; totals.direct += plan.direct; totals.made += plan.made; totals.noSeller += plan.noSeller;
    info = `読んだ一覧 ${pages}ページ: 商品 ${totals.rows}件（取引連絡のリンク ${totals.direct}件・型から作った ${totals.made}件・出品者が分からず開けない ${totals.noSeller}件）`;
    const links = plan.links.filter((l) => !sent.has(l.id));
    const noSeller = plan.noSellerIds.filter((id) => !sent.has(id));
    if (!links.length && !noSeller.length) return;
    links.forEach((l) => sent.add(l.id));
    noSeller.forEach((id) => sent.add(id));
    chrome.runtime.sendMessage({ type: "evidenceBatch", links, noSeller }, (r) => {
      if (chrome.runtime.lastError || !r) return;
      if (r.queued) show(`仕入れ証跡: ${r.queued}件を順に開いて保存します（保存済み ${r.already}件は飛ばします）\n${info}`);
    });
  };
  chrome.runtime.onMessage.addListener((m) => {
    if (m && m.type === "evidenceProgress") {
      show(m.done ? `仕入れ証跡: 保存 ${m.saved}件・保存できず ${m.failed.length}件` + (m.failed.length ? "\n" + m.failed.join(", ") : "") + `\n${info}\n（クリックで閉じる）`
                  : `仕入れ証跡: ${m.n}/${m.total} 件目を保存中…\n${info}`);
      tag.onclick = m.done ? () => tag.remove() : null;
    }
  });
  const run = async () => {
    await sleep(1500);   // 一覧があとから出るページ用
    // このページ: 下まで読み込ませる（スクロールで増える一覧）→ 元の位置に戻す。オーナーがスクロールしなくてよい
    const y0 = window.scrollY;
    let h = -1;
    for (let i = 0; i < 15 && document.body.scrollHeight !== h; i++) {
      h = document.body.scrollHeight;
      window.scrollTo(0, h);
      await sleep(800);
    }
    window.scrollTo(0, y0);
    queue(document, document.documentElement.outerHTML);
    // 次のページ: 裏で読む（開かない。最大30ページ・1.5秒おき）
    let url = nextPageUrl(Array.from(document.querySelectorAll("a[href]"), (a) => ({ href: a.href, text: a.textContent, rel: a.rel })), location.href);
    const done = new Set([location.href]);
    while (url && !done.has(url) && pages < 30) {
      done.add(url);
      await sleep(1500);
      let html = "";
      try { html = await (await fetch(url, { credentials: "include" })).text(); } catch (e) { break; }
      const doc = new DOMParser().parseFromString(html, "text/html");
      const base = doc.createElement("base"); base.href = url; doc.head.appendChild(base);
      pages += 1;
      queue(doc, html);
      url = nextPageUrl(Array.from(doc.querySelectorAll("a[href]"), (a) => ({ href: a.href, text: a.textContent, rel: a.rel })), url);
    }
  };
  run();
})();
