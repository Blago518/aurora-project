// ヤフオクの仕入れ証跡をまとめて保存（2026-11-26 オーナー「俺がそのページを開いたら、取得するものをつくったりできないの？」）。
// ヤフオクのページ（マイ・オークションの落札分の一覧 auctions.yahoo.co.jp/my/won など）に取引ナビへのリンクがあれば、まだ証跡の無い物を
// 裏方（background.js）が1件ずつ前のタブで開き、取引ナビの evidence.js が画面を PNG で保存したらそのタブを閉じる。
// 2026-11-26 落札分の一覧で［取引連絡］のある5件だけ保存して止まった。取引が終わった物は［評価］だけで取引ナビのリンクが無い。
// → 同じページの取引ナビのリンク（出品者・落札者の引数付き）を型にして、行の中のリンクから出品者のIDを拾い、オークションIDと入れ替えて作る。
// 開くのはリンクの先（取引ナビ）を見るだけ。取引ナビの中のボタン（連絡・受け取り連絡・評価）は押さない。

// @evlist-start（tools/test_evidence.py がこの間を node で試す）
const NAVI_RE = /^https:\/\/contact\.auctions\.yahoo\.co\.jp\//;
const SELLER_KEYS = ["syid", "sellerid", "seller", "targetid", "target", "userid", "user", "u", "id"];

function aidOf(url) {
  return ((String(url || "").match(/[?&](?:aid|aucid|auctionid)=([a-z]?\d{8,12})(?:&|$|#)/i) || [])[1] || "").toLowerCase();
}

function navLinks(hrefs) {
  const out = [], seen = new Set();
  for (const h of hrefs || []) {
    const s = String(h || "");
    if (!NAVI_RE.test(s)) continue;
    const id = aidOf(s);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, url: s });
  }
  return out;
}

// 行の中のリンク（評価・出品者のページなど）から出品者の Yahoo! ID を探す
function sellerOf(hrefs, mine) {
  for (const h of hrefs || []) {
    let u;
    try { u = new URL(h); } catch (e) { continue; }
    if (NAVI_RE.test(h)) continue;
    for (const [k, v] of u.searchParams) {
      if (SELLER_KEYS.includes(k.toLowerCase()) && v && v !== mine && !/^[a-z]?\d{8,12}$/i.test(v)) return v;
    }
    const m = u.pathname.match(/\/(?:seller|user|rating\/list|jp\/show\/rating)\/([^/?#]+)/);
    if (m && m[1] !== mine && !/^[a-z]?\d{8,12}$/i.test(m[1])) return decodeURIComponent(m[1]);
  }
  return "";
}

// 落札分の一覧の行: [{id, hrefs}] → 開く物 [{id, url, made}]
function planLinks(rows, pageHrefs) {
  const direct = navLinks(pageHrefs);
  const tpl = direct.find((l) => /[?&]syid=/.test(l.url)) || null;
  let mine = "";
  if (tpl) { try { mine = new URL(tpl.url).searchParams.get("bid") || ""; } catch (e) { /* 型が読めない */ } }
  const out = direct.slice(), seen = new Set(direct.map((l) => l.id));
  let noSeller = 0;
  for (const r of rows || []) {
    const id = String(r.id || "").toLowerCase();
    if (!id || seen.has(id)) continue;
    const own = navLinks(r.hrefs);
    if (own.length) { out.push(own[0]); seen.add(id); continue; }
    const sy = tpl ? sellerOf(r.hrefs, mine) : "";
    if (!sy) { noSeller += 1; continue; }
    const u = new URL(tpl.url);
    u.searchParams.set("aid", id);
    u.searchParams.set("syid", sy);
    out.push({ id, url: u.toString(), made: true });
    seen.add(id);
  }
  return { links: out, direct: direct.length, made: out.filter((l) => l.made).length, noSeller, template: !!tpl };
}
// @evlist-end

(function () {
  if (typeof document === "undefined" || typeof chrome === "undefined" || !chrome.runtime) return;
  const sent = new Set();
  const tag = document.createElement("div");
  tag.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#14214a;color:#fff;padding:10px 14px;border-radius:8px;font:13px sans-serif;opacity:.95;max-width:360px;white-space:pre-line";
  const show = (t) => { tag.textContent = t; if (!tag.isConnected) document.body.appendChild(tag); };
  let info = "";
  const rowsOnPage = () => {
    const rows = [];
    const seen = new Set();
    // script の中の文字（__NEXT_DATA__ の JSON など）は行ではないので読まない
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
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
  const scan = () => {
    const rows = rowsOnPage();
    const plan = planLinks(rows, Array.from(document.querySelectorAll("a[href]"), (a) => a.href));
    info = `この画面: 商品 ${rows.length}件（取引連絡のリンク ${plan.direct}件・型から作った ${plan.made}件・出品者が分からず開けない ${plan.noSeller}件）`;
    const links = plan.links.filter((l) => !sent.has(l.id));
    if (!links.length) return;
    links.forEach((l) => sent.add(l.id));
    chrome.runtime.sendMessage({ type: "evidenceBatch", links }, (r) => {
      if (chrome.runtime.lastError || !r) return;
      if (r.queued) show(`仕入れ証跡: ${r.queued}件を順に開いて保存します（保存済み ${r.already}件は飛ばします）\n${info}`);
    });
  };
  chrome.runtime.onMessage.addListener((m) => {
    if (m && m.type === "evidenceProgress") {
      show(m.done ? `仕入れ証跡: 保存 ${m.saved}件・保存できず ${m.failed.length}件` + (m.failed.length ? "\n" + m.failed.join(", ") : "") + `\n${info}\n（下の行はスクロール・次のページで続けて読みます。クリックで閉じる）`
                  : `仕入れ証跡: ${m.n}/${m.total} 件目を保存中…\n${info}`);
      tag.onclick = m.done ? () => tag.remove() : null;
    }
  });
  scan();
  let timer = null;   // 一覧があとから増えるページ（スクロールで読み込む）も拾う
  new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(scan, 1500); }).observe(document.body, { childList: true, subtree: true });
})();
