// ヤフオクの仕入れ証跡をまとめて保存（2026-11-26 オーナー「俺がそのページを開いたら、取得するものをつくったりできないの？」）。
// ヤフオクのページ（マイ・オークションの落札分の一覧など）に取引ナビへのリンクがあれば、まだ証跡の無い物を
// 裏方（background.js）が1件ずつ前のタブで開き、取引ナビの evidence.js が画面を PNG で保存したらそのタブを閉じる。
// 開くのはリンクの先（取引ナビ）を見るだけ。取引ナビの中のボタン（連絡・受け取り連絡・評価）は押さない。

// @evlist-start（tools/test_evidence.py がこの間を node で試す）
function navLinks(hrefs) {
  const out = [], seen = new Set();
  for (const h of hrefs || []) {
    const s = String(h || "");
    if (!/^https:\/\/contact\.auctions\.yahoo\.co\.jp\//.test(s)) continue;
    const id = ((s.match(/[?&]aid=([a-z]?\d{8,12})(?:&|$|#)/i) || [])[1] || "").toLowerCase();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push({ id, url: s });
  }
  return out;
}
// @evlist-end

(function () {
  if (typeof document === "undefined" || typeof chrome === "undefined" || !chrome.runtime) return;
  let sent = new Set();
  const tag = document.createElement("div");
  tag.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#14214a;color:#fff;padding:10px 14px;border-radius:8px;font:13px sans-serif;opacity:.95;max-width:320px;white-space:pre-line";
  const show = (t) => { tag.textContent = t; if (!tag.isConnected) document.body.appendChild(tag); };
  const scan = () => {
    const links = navLinks(Array.from(document.querySelectorAll("a[href]"), (a) => a.href)).filter((l) => !sent.has(l.id));
    if (!links.length) return;
    links.forEach((l) => sent.add(l.id));
    chrome.runtime.sendMessage({ type: "evidenceBatch", links }, (r) => {
      if (chrome.runtime.lastError || !r) return;
      if (r.queued) show(`仕入れ証跡: ${r.queued}件を順に開いて保存します（保存済み ${r.already}件は飛ばします）`);
    });
  };
  chrome.runtime.onMessage.addListener((m) => {
    if (m && m.type === "evidenceProgress") {
      show(m.done ? `仕入れ証跡: 保存 ${m.saved}件・保存できず ${m.failed.length}件` + (m.failed.length ? "\n" + m.failed.join(", ") : "") + "\n（閉じるまで残します。クリックで閉じる）"
                  : `仕入れ証跡: ${m.n}/${m.total} 件目を保存中…`);
      tag.onclick = m.done ? () => tag.remove() : null;
    }
  });
  scan();
  setTimeout(scan, 2000);   // 一覧があとから出るページ用
})();
