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
// @evidence-end

(function () {
  if (typeof document === "undefined" || typeof chrome === "undefined" || !chrome.runtime) return;
  let tries = 0;
  const go = () => {
    const info = evidenceInfo(document.body ? document.body.innerText : "");
    if (!info || !info.ymd || !info.price) { if (++tries < 20) setTimeout(go, 500); return; }   // 中身が出るまで待つ（最大10秒）
    window.scrollTo(0, 0);
    setTimeout(() => {
      chrome.runtime.sendMessage({ type: "evidenceShot", id: info.id, name: info.name }, (r) => {
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
