// Aurora 仕入れ鑑定 ― eBay 出品中の相場（公式 Browse API）を調べる裏方（service worker）
// build_extension.py が eBay の App ID・Cert ID（キーチェーンから）を下の2行に埋め込んで extension/background.js にする。
// 通信先は api.ebay.com だけ。eBay のページのスクレイピングはしない。
const APP_ID = __APP_ID__;
const CERT_ID = __CERT_ID__;
const CACHE_MS = 24 * 60 * 60 * 1000; // 同じ機種は1日1回だけ調べる
// 本体以外（キャップ・フード・ケース・部品取りなど）は除く。検索語に含まれる語は除かない
const EXCLUDE_WORDS = ["cap", "caps", "hood", "case", "filter", "adapter", "adaptor", "parts", "repair", "junk",
  "manual", "strap", "box only", "teleconverter", "extender", "mutar", "for parts"];

let token = null, tokenExp = 0;

async function appToken() {
  if (token && Date.now() < tokenExp - 60000) return token;
  const r = await fetch("https://api.ebay.com/identity/v1/oauth2/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Authorization: "Basic " + btoa(APP_ID + ":" + CERT_ID) },
    body: "grant_type=client_credentials&scope=" + encodeURIComponent("https://api.ebay.com/oauth/api_scope"),
  });
  if (!r.ok) throw new Error("eBayの認証エラー " + r.status);
  const d = await r.json();
  token = d.access_token; tokenExp = Date.now() + (d.expires_in || 7200) * 1000;
  return token;
}

const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

async function browse(q, must = [], not = []) {
  const key = "browse3:" + [q, ...must, ...not.map((x) => "-" + x)].join(" ").toLowerCase();  // 集計方法を変えたら番号を上げて読み直す
  const word = (w) => new RegExp("(^|[^A-Za-z])" + w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "(?![A-Za-z])", "i");
  const mustRe = must.map(word), notRe = not.map(word);  // 型（Contax の MMJ など）: その型の出品だけ数え、ほかの型は除く
  const cached = (await chrome.storage.local.get(key))[key];
  if (cached && Date.now() - cached.t < CACHE_MS) return cached;
  const params = new URLSearchParams({ q, limit: "100", filter: "buyingOptions:{FIXED_PRICE|AUCTION},conditions:{USED}" });
  const r = await fetch("https://api.ebay.com/buy/browse/v1/item_summary/search?" + params, {
    headers: { Authorization: "Bearer " + (await appToken()), "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
      "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS" },
  });
  if (!r.ok) throw new Error("eBay検索エラー " + r.status);
  const d = await r.json();
  const ql = q.toLowerCase();
  const words = EXCLUDE_WORDS.filter((w) => !ql.includes(w));
  const re = new RegExp("\\b(" + words.map((w) => w.replace(/ /g, "\\s+")).join("|") + ")\\b", "i");
  // 検索語の焦点距離（28mm・80-200mm）と F値（F2.8）が、タイトルにも入っている出品だけ使う（別のレンズが混ざらないように）
  const specs = [];
  // 焦点距離: 「28mm」「28 mm」、Zeiss 式の「2.8/28」も可。F値: 「F2.8」「f/2.8」「1:2.8」「2.8/28」「135mm 2.8」も可
  for (const m of q.matchAll(/(\d+(?:-\d+)?)\s*mm/gi)) {
    const n = m[1].replace("-", "\\s*-\\s*");
    specs.push(new RegExp("(^|[^\\d.])" + n + "\\s*mm|\\/\\s*" + n + "(?![\\d.])", "i"));
  }
  for (const m of q.matchAll(/\bf\/?\s*(\d+(?:\.\d+)?)/gi))
    specs.push(new RegExp("(^|[^\\d.])" + m[1].replace(".", "\\.") + "(?![\\d])", "i"));
  let totals = [], jp = 0;
  for (const it of d.itemSummaries || []) {
    if (!it.price || it.price.currency !== "USD" || re.test(it.title || "")) continue;
    if (!specs.every((r) => r.test(it.title || ""))) continue;
    if (!mustRe.every((r) => r.test(it.title || "")) || notRe.some((r) => r.test(it.title || ""))) continue;
    const ships = (it.shippingOptions || []).map((s) => Number(s.shippingCost && s.shippingCost.value)).filter((v) => !isNaN(v));
    totals.push(Number(it.price.value) + (ships.length ? Math.min(...ships) : 0));
    if ((it.itemLocation || {}).country === "JP") jp++;
  }
  if (totals.length >= 3) { const m = median(totals); totals = totals.filter((v) => v >= m * 0.3); } // 極端に安い物（部品など）を外す
  const out = { t: Date.now(), n: totals.length, hits: d.total || 0, jp,
    min: totals.length ? Math.min(...totals) : null, med: totals.length ? Math.round(median(totals) * 100) / 100 : null };
  await chrome.storage.local.set({ [key]: out });
  return out;
}

// 相場メモを ダウンロード/aurora_price_memo.csv に上書き保存（Mac の build_store.py などが data/price_memo.csv に取り込む）
function saveMemo(csv) {
  const url = "data:text/csv;charset=utf-8," + encodeURIComponent("\ufeff" + csv);
  return chrome.downloads.download({ url, filename: "aurora_price_memo.csv", conflictAction: "overwrite", saveAs: false });
}

chrome.runtime.onMessage.addListener((msg, _sender, send) => {
  if (msg && msg.type === "saveFile" && /^aurora_[a-z_]+\.csv$/.test(msg.name || "")) {  // 安心ストアの一覧など
    const url = "data:text/csv;charset=utf-8," + encodeURIComponent("\ufeff" + msg.csv);
    chrome.downloads.download({ url, filename: msg.name, conflictAction: "overwrite", saveAs: false })
      .then(() => send({ ok: true }), (e) => send({ error: String((e && e.message) || e) }));
    return true;
  }
  if (msg && msg.type === "saveMemo") {
    saveMemo(msg.csv).then(() => send({ ok: true }), (e) => send({ error: String((e && e.message) || e) }));
    return true;
  }
  if (msg && msg.type === "browse") {
    browse(msg.q, msg.must || [], msg.not || []).then(send, (e) => send({ error: String((e && e.message) || e) }));
    return true; // 非同期で返す
  }
});
