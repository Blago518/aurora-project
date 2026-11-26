// Aurora 仕入れ鑑定 ― eBay 出品中の相場（公式 Browse API）を調べる裏方（service worker）
// build_extension.py が eBay の App ID・Cert ID（キーチェーンから）を下の2行に埋め込んで extension/background.js にする。
// 通信先は api.ebay.com だけ。eBay のページのスクレイピングはしない。
const APP_ID = __APP_ID__;
const CERT_ID = __CERT_ID__;
// 2026-11-16 ユーザー決定: 送料無料の出品は出品額 − $30 を商品代（送料別）とみなす。送料別の出品は出品額そのまま。
// 送料が分からない出品は保守的に送料無料とみなす。相場データと同じく送料（deduction_usd）を足した送料込みの額で返す
const FREE_SHIP_USD = __FREE_SHIP_USD__;
const DEDUCTION_USD = __DEDUCTION_USD__;
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
  const key = "browse5:" + [q, ...must, ...not.map((x) => "-" + x)].join(" ").toLowerCase();  // 集計方法を変えたら番号を上げて読み直す
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
    specs.push(new RegExp("(^|[^\\d.])" + m[1].replace(".", "\\.") + "(?![\\d]|\\.\\d)", "i"));  // F2 で F2.8 を数えない
  let totals = [], jp = 0;
  for (const it of d.itemSummaries || []) {
    if (!it.price || it.price.currency !== "USD" || re.test(it.title || "")) continue;
    if (!specs.every((r) => r.test(it.title || ""))) continue;
    if (!mustRe.every((r) => r.test(it.title || "")) || notRe.some((r) => r.test(it.title || ""))) continue;
    const ships = (it.shippingOptions || []).map((s) => Number(s.shippingCost && s.shippingCost.value)).filter((v) => !isNaN(v));
    const value = Number(it.price.value);
    const net = ships.length && Math.min(...ships) > 0 ? value : value - FREE_SHIP_USD;
    totals.push(Math.round((net + DEDUCTION_USD) * 100) / 100);
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

// ［全て開く］: 今のタブのすぐ右に、渡された順（ヤフオク → sold → now）で開く。ヤフオクと eBay の URL だけ
function openTabs(urls, tab, max) {
  // ［全て開く］は5件まで、［◎○をまとめて開く］は10件まで
  const lim = Math.min(Math.max(Number(max) || 5, 1), 10);
  const ok = (urls || []).filter((u) => /^https:\/\/(auctions\.yahoo\.co\.jp|(www\.)?ebay\.com)\//.test(String(u))).slice(0, lim);
  const index = tab && tab.index != null ? tab.index + 1 : undefined;
  const opened = [];
  // 全部を裏で開いてから、最初（ヤフオク）を前に出す（開いた直後にタブが切り替わって残りが開かないのを防ぐ）
  return ok.reduce((p, url, n) => p.then(() => chrome.tabs.create(Object.assign(
    { url, active: false }, index != null ? { index: index + n } : {}, tab && tab.windowId != null ? { windowId: tab.windowId } : {}))
    .then((t) => opened.push(t))), Promise.resolve())
    .then(() => (opened[0] ? chrome.tabs.update(opened[0].id, { active: true }) : null))
    .then(() => opened.length);
}

// 拡張機能が自動で保存する aurora_*.csv（Mac が取り込む用）は、ダウンロードの吹き出しと一覧に出さない（2026-11-19 ユーザー指摘）。
// ファイルは上書きで1つだけ。保存の間だけ吹き出しを止め、終わったら一覧から消す（ファイルは残る）
function quietDownload(opts) {
  const ui = (on) => { try { if (chrome.downloads.setUiOptions) return chrome.downloads.setUiOptions({ enabled: on }).catch(() => {}); } catch (e) {} return Promise.resolve(); };
  return ui(false).then(() => chrome.downloads.download(opts)).then((id) => new Promise((ok) => {
    const done = () => { chrome.downloads.onChanged.removeListener(watch); clearTimeout(t); ui(true); chrome.downloads.erase({ id }).catch(() => {}); ok(id); };
    const watch = (d) => { if (d.id === id && d.state && d.state.current !== "in_progress") done(); };
    const t = setTimeout(done, 10000);
    chrome.downloads.onChanged.addListener(watch);
  }), (e) => { ui(true); throw e; });
}

// 仕入れ証跡をまとめて保存する順番待ち（1件ずつ前のタブで開く。写すのは見えている画面だけなので前に出す）
const evQueue = [];
let evBusy = false, evWait = null, evStat = { saved: 0, failed: [], total: 0, n: 0, from: null };
function evDone(tabId, ok) {
  if (evWait && evWait.tabId === tabId) { const w = evWait; evWait = null; w.resolve(ok); }
}
function evTell(extra) {
  if (evStat.from != null) chrome.tabs.sendMessage(evStat.from, Object.assign({ type: "evidenceProgress" }, evStat, extra), () => void chrome.runtime.lastError);
}
async function evRun() {
  if (evBusy) { evStat.total = evStat.n + evQueue.length; return; }
  evBusy = true;
  evStat = { saved: 0, failed: [], total: evQueue.length, n: 0, from: evQueue.length ? evQueue[0].from : null };
  while (evQueue.length) {
    const item = evQueue.shift();
    evStat.n += 1;
    evTell({ done: false });
    let tab = null, ok = false;
    try {
      tab = await chrome.tabs.create({ url: item.url, active: true });
      ok = await new Promise((resolve) => {
        evWait = { tabId: tab.id, resolve };
        setTimeout(() => evDone(tab.id, false), 25000);   // 25秒で保存されなければ、できなかったとして次へ
      });
    } catch (e) { ok = false; }
    if (ok) evStat.saved += 1; else evStat.failed.push(item.id);
    if (tab && ok) { try { await chrome.tabs.remove(tab.id); } catch (e) { /* 閉じられていても続ける */ } }   // 保存できなかったタブは残す（理由を見られるように）
    await new Promise((r) => setTimeout(r, 1500));
  }
  if (evStat.from != null) { try { await chrome.tabs.update(evStat.from, { active: true }); } catch (e) { /* 一覧のタブが閉じられていても */ } }
  evTell({ done: true });
  evBusy = false;
}

chrome.runtime.onMessage.addListener((msg, _sender, send) => {
  // 相場データ: フォルダの data.json を毎回読む（Mac が作り直した最新。拡張機能の ↻ は不要）
  if (msg && msg.type === "getData") {
    fetch(chrome.runtime.getURL("data.json") + "?t=" + Date.now(), { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null)).then((d) => send(d), () => send(null));
    return true;
  }
  // 輸出許可書（DHL の運送状番号10桁.pdf）が Chrome のダウンロードの記録に何件あるか（extension_cpass.js の終わりの知らせ）
  if (msg && msg.type === "cpassSaved") {
    chrome.downloads.search({ filenameRegex: "[\\\\/]\\d{10}( \\(\\d+\\))?\\.pdf$", state: "complete", exists: true, limit: 0 })
      .then((ds) => send({ count: new Set(ds.map((d) => (d.filename.match(/(\d{10})( \(\d+\))?\.pdf$/) || [])[1])).size }), () => send({}));
    return true;
  }
  if (msg && msg.type === "openTabs") {
    openTabs(msg.urls, _sender && _sender.tab, msg.max).then((n) => send({ ok: true, opened: n }), (e) => send({ error: String((e && e.message) || e) }));
    return true;
  }
  // オークファンの入札予約: ヤフオクのパネルの［予約］→ 予約ページを開き、URL と上限額を渡す（予約するのは人）
  if (msg && msg.type === "aucfanReserve") {
    chrome.storage.local.set({ aucfanPending: Object.assign({}, msg.item, { t: Date.now() }) })
      .then(() => chrome.tabs.create({ url: "https://tools.aucfan.com/v2/snipe/item?trflg=1" })).then(() => send({ ok: true }));
    return true;
  }
  if (msg && msg.type === "aucfanPending") {
    chrome.storage.local.get("aucfanPending").then((r) => send({ pending: r.aucfanPending || null }));
    return true;
  }
  if (msg && msg.type === "saveFile" && /^aurora_[a-z_]+\.csv$/.test(msg.name || "")) {  // 安心ストアの一覧など
    const url = "data:text/csv;charset=utf-8," + encodeURIComponent("\ufeff" + msg.csv);
    quietDownload({ url, filename: msg.name, conflictAction: "overwrite", saveAs: false })
      .then(() => send({ ok: true }), (e) => send({ error: String((e && e.message) || e) }));
    return true;
  }
  // 仕入れ証跡をまとめて: ヤフオクのページにあった取引ナビのリンクを、まだ証跡の無い物だけ1件ずつ開く（extension_evidence_list.js）
  if (msg && msg.type === "evidenceBatch" && Array.isArray(msg.links) && _sender && _sender.tab) {
    const links = msg.links.filter((l) => l && /^[a-z]?\d{8,12}$/i.test(l.id || "") && /^https:\/\/contact\.auctions\.yahoo\.co\.jp\//.test(l.url || ""));
    chrome.storage.local.get(links.map((l) => "evidence_" + l.id)).then((r) => {
      const todo = links.filter((l) => !r["evidence_" + l.id] && !evQueue.some((q) => q.id === l.id));
      todo.forEach((l) => evQueue.push(Object.assign({ from: _sender.tab.id }, l)));
      send({ queued: todo.length, already: links.length - todo.length });
      evRun();
    });
    return true;
  }
  // ヤフオクの仕入れ証跡: 取引ナビの見えている画面を PNG で ダウンロード/ヤフオク仕入証跡/ に保存（同じオークションIDは1回だけ。extension_evidence.js）
  if (msg && msg.type === "evidenceShot" && /^[a-z]?\d{8,12}$/i.test(msg.id || "") && _sender && _sender.tab) {
    const key = "evidence_" + msg.id;
    chrome.storage.local.get(key).then((r) => {
      if (r[key]) { evDone(_sender.tab.id, true); return send({ already: true }); }
      return chrome.tabs.captureVisibleTab(_sender.tab.windowId, { format: "png" })
        .then((url) => quietDownload({ url, filename: "ヤフオク仕入証跡/" + String(msg.name).slice(0, 120) + ".png", conflictAction: "uniquify", saveAs: false })
          // 日本語の名前を受け付けない環境だけ、英数字の名前（終了日_金額_オークションID）で保存する
          .catch(() => quietDownload({ url, filename: "yahoo_evidence/" + String(msg.name).replace(/[^\w.-]+/g, "_").replace(/_+/g, "_") + ".png", conflictAction: "uniquify", saveAs: false })))
        .then(() => chrome.storage.local.set({ [key]: Date.now() }))
        .then(() => { evDone(_sender.tab.id, true); send({ saved: true }); });
    }).catch((e) => { evDone(_sender.tab.id, false); send({ error: String((e && e.message) || e) }); });
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
