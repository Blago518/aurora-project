// ヤフオクの仕入れ証跡（2026-11-23 オーナー「税理士から仕入れ証跡を残すように言われ、ヤフオクの出品ページをスクショしている。手間なので自動化」）。
// 取引ナビを開いたら、その画面（商品名・金額・終了日時・オークションID・出品者が写る上の部分）を自動で PNG に保存する。
// 名前は今までのショートカット「ヤフオク仕入証跡」と同じ形: 終了日_出品者_金額_オークションID.png（例 20260901_カメラ店A_6000_m1000000001.png）。
// 保存先: ダウンロード/ヤフオク仕入証跡/（Mac の update_all.py が 税理 › 8.〜 › ヤフオク に移す）。同じオークションIDは1回だけ。
// 開くのはオーナー（拡張機能はページを開かない・読みに行かない）。

// @evidence-start（tools/test_evidence.py がこの間を node で試す）
function evidenceInfo(text, now) {
  const t = String(text || "").replace(/ /g, " ");
  const id = (t.match(/オークションID\s*[:：]\s*([a-z]?\d{8,12})/i) || [])[1];
  if (!id) return null;
  const seller = ((t.match(/出品者\s*[:：]\s*([^\n（(]+)/) || [])[1] || "").trim().replace(/\s+/g, " ");
  const price = ((t.match(/(?:商品金額（税込）|落札価格|商品金額)\s*[:：]\s*([\d,]+)\s*円/) || [])[1] || "").replace(/,/g, "");
  const m = t.match(/終了日時\s*[:：]\s*(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日/);
  let ymd = "";
  if (m) {
    const n = now || new Date();
    let y = m[1] ? +m[1] : n.getFullYear();
    if (!m[1] && +m[2] > n.getMonth() + 1) y -= 1;   // 年の書いていない「12月30日」を1月に開いたら去年
    ymd = `${y}${String(m[2]).padStart(2, "0")}${String(m[3]).padStart(2, "0")}`;
  }
  const safe = (s) => s.replace(/[\\/:*?"<>|\n\r\t]/g, "").trim();
  const name = [ymd, safe(seller), price, id].filter(Boolean).join("_");
  return { id, seller, price, ymd, name };
}

// まとめて取引の画面（/trade/bundle。2026-11-26 本物の取引ナビのリンクはこちらに飛ぶ）: オークションID・出品者は出ないが、
// 商品名・落札価格・終了日時・合計金額は出るので、この画面を証跡にする。名前は 終了日_まとめて取引_合計金額_ID_ID.png（中の商品のID全部）
function bundleInfo(text, url, hrefs, now) {
  const t = String(text || "").replace(/\u00a0/g, " ");
  if (!/まとめて取引/.test(t)) return null;
  const ids = [];
  for (const h of hrefs || []) {
    const m = String(h).match(/\/jp\/auction\/([a-z]?\d{8,12})/i);
    if (m && !ids.includes(m[1].toLowerCase())) ids.push(m[1].toLowerCase());
  }
  const own = ((String(url || "").match(/[?&]aid=([a-z]?\d{8,12})/i) || [])[1] || "").toLowerCase();
  if (own && !ids.includes(own)) ids.push(own);
  if (!ids.length) return null;
  const total = ((t.match(/合計金額\s*[:：]\s*([\d,]+)\s*円/) || [])[1] || "").replace(/,/g, "");
  const m = t.match(/終了日時\s*[:：]\s*(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日/);
  if (!m || !total) return null;
  const n = now || new Date();
  let y = m[1] ? +m[1] : n.getFullYear();
  if (!m[1] && +m[2] > n.getMonth() + 1) y -= 1;
  const ymd = `${y}${String(m[2]).padStart(2, "0")}${String(m[3]).padStart(2, "0")}`;
  return { id: ids[0], ids, price: total, ymd, name: [ymd, "まとめて取引", total].concat(ids).join("_") };
}

// 注文の状況の画面（buy.auctions.yahoo.co.jp/order/status。2026-11-26 落札分の一覧の34件は［取引連絡］がここに飛んでいた）。
// 書き方が取引ナビと違うので、見つかった物だけで名前を作る。オークションIDは開いた側（順番待ち）が知っている ID を使う
function orderInfo(text, id, now) {
  if (!id) return null;
  const t = String(text || "").replace(/\u00a0/g, " ");
  const pick = (re) => ((t.match(re) || [])[1] || "");
  const seller = pick(/(?:出品者|ストア名|販売者|ストア)\s*[:：]?\s*\n?\s*([^\n（(]{1,40})/).trim().replace(/\s+/g, " ");
  const price = pick(/(?:落札価格|商品金額（税込）|商品金額|商品代金|小計|合計金額|お支払い金額|支払い金額|お支払金額)\s*[:：]?\s*\n?\s*([\d,]+)\s*円/).replace(/,/g, "");
  const m = t.match(/(?:終了日時|落札日時|落札日|注文日時|注文日|購入日時|購入日)\s*[:：]?\s*\n?\s*(?:(\d{4})[年/])?\s*(\d{1,2})[月/]\s*(\d{1,2})/) || t.match(/(\d{4})[年/](\d{1,2})[月/](\d{1,2})/);
  let ymd = "";
  if (m) {
    const n = now || new Date();
    let y = m[1] ? +m[1] : n.getFullYear();
    if (!m[1] && +m[2] > n.getMonth() + 1) y -= 1;
    ymd = `${y}${String(m[2]).padStart(2, "0")}${String(m[3]).padStart(2, "0")}`;
  }
  const safe = (s) => s.replace(/[\\/:*?"<>|\n\r\t]/g, "").trim();
  return { id: String(id).toLowerCase(), ymd, price, seller, name: [ymd, safe(seller) || "注文", price, String(id).toLowerCase()].filter(Boolean).join("_"),
           found: ["日付:" + (ymd || "なし"), "出品者:" + (seller ? "あり" : "なし"), "金額:" + (price || "なし")].join(" ") };
}
// @evidence-end

(function () {
  if (typeof document === "undefined" || typeof chrome === "undefined" || !chrome.runtime) return;
  let tries = 0, expect = "";
  const isOrder = location.hostname === "buy.auctions.yahoo.co.jp";
  if (isOrder) {   // 開いた側（まとめて保存の順番待ち）が知っているオークションID。自分で開いたときは URL・画面の文字から
    chrome.runtime.sendMessage({ type: "evidenceExpect" }, (r) => { expect = (!chrome.runtime.lastError && r && r.id) || ""; });
  }
  const go = () => {
    const body = document.body ? document.body.innerText : "";
    let info = evidenceInfo(body) || bundleInfo(body, location.href, Array.from(document.querySelectorAll("a[href]"), (a) => a.href));
    if (!info && isOrder) {
      const id = expect || ((location.href.match(/[?&][a-z_]*(?:aid|auction)[a-z_]*=([a-z]?\d{8,12})/i) || [])[1]) || ((body.match(/(?:オークションID|商品ID)\s*[:：]?\s*([a-z]\d{8,12})/i) || [])[1]) || "";
      const o = orderInfo(body, id);
      // 中身が出るのを少し待ってから（最大5秒）、見つかった物だけで保存する（オークションIDさえあれば写す）
      if (o && (o.ymd && o.price || tries >= 10)) info = o;
    }
    if (!info || !info.ymd && !info.found || !info.price && !info.found) {   // 中身が出るまで待つ（最大10秒）。出なければ、まとめて保存の順番待ちに次へ進むよう知らせる
      if (++tries < 20) setTimeout(go, 500); else chrome.runtime.sendMessage({ type: "evidenceNone", note: (location.pathname + " 「" + document.title + "」 " + body.replace(/\s+/g, " ").slice(0, 80)) }, () => void chrome.runtime.lastError);
      return;
    }
    window.scrollTo(0, 0);
    setTimeout(() => {
      chrome.runtime.sendMessage({ type: "evidenceShot", id: info.id, ids: info.ids || [info.id], name: info.name, found: info.found ? info.found + " ／画面: " + body.replace(/\s+/g, " ").slice(0, 120) : "" }, (r) => {
        if (chrome.runtime.lastError || !r) return;
        const tag = document.createElement("div");
        tag.textContent = r.saved ? "✓ 仕入れ証跡を保存しました" : r.already ? "✓ 仕入れ証跡は保存済み" : "仕入れ証跡を保存できませんでした: " + (r.error || "");
        tag.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#14214a;color:#fff;padding:8px 12px;border-radius:8px;font:13px sans-serif;opacity:.92";
        document.body.appendChild(tag);
        setTimeout(() => tag.remove(), 4000);
      });
    }, 600);   // 写す前に、上まで戻った画面が落ち着くのを待つ（保存のしるしは写した後に出す）
  };
  go();
})();
