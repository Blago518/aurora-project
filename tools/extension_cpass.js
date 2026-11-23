// SpeedPAK セラーポータル（CPaSS）の「各注文番号の料金明細」: 開いたら輸出許可書を自動で取る（2026-11-23 オーナー「自動化してほしい」）。
// 1) 表の各行の「証明書をダウンロードする」（DHL の輸出許可書の PDF。名前は DHL の運送状番号10桁）を、まだ押していない行だけ順に押す
// 2) 「輸出取引の詳細」（xlsx。FedEx・DHL の運送状番号が入っている）を1日1回押す → Mac の export_permit.py が FedEx の依頼用 Excel を作る
// 押した行は OrangeConnex 追跡番号（EX…/EM…）で覚え、二度押さない。ページを開くのはオーナー（ログインが要るため）。

// @cpass-start（tools/test_cpass.py がこの間を node で試す）
function cpassRowId(text) {
  const m = String(text || "").match(/\b(E[XM]\d{10,16}[A-Z]{2}\d{6,10}[A-Z0-9]{2,4})\b/);
  return m ? m[1] : null;
}
function cpassPending(rows, done) {
  // rows: [{text, hasCert}] → 押す行の追跡番号（証明書のリンクがあり、まだ押していない物）
  const seen = new Set(done || []);
  const out = [];
  for (const r of rows) {
    const id = cpassRowId(r.text);
    if (id && r.hasCert && !seen.has(id)) { out.push(id); seen.add(id); }
  }
  return out;
}
// @cpass-end

(function () {
  if (typeof document === "undefined" || typeof chrome === "undefined" || !chrome.storage) return;
  const CERT = "証明書をダウンロードする";
  const leafWith = (root, label) => [...root.querySelectorAll("a, button, span, div")]
    .filter((e) => e.textContent.trim() === label && ![...e.children].some((c) => c.textContent.trim() === label));
  const note = (msg) => {
    let t = document.getElementById("aurora-cpass-note");
    if (!t) { t = document.createElement("div"); t.id = "aurora-cpass-note"; t.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;background:#14214a;color:#fff;padding:8px 12px;border-radius:8px;font:13px sans-serif;opacity:.92"; document.body.appendChild(t); }
    t.textContent = msg;
  };
  let started = false;
  const run = async () => {
    if (started || !/各注文番号の料金明細/.test(document.body.innerText)) return;
    const rowEls = [...document.querySelectorAll("tr")].filter((tr) => cpassRowId(tr.innerText));
    if (!rowEls.length) return;   // 表がまだ出ていない
    started = true;
    const st = await chrome.storage.local.get(["cpassDone", "cpassDetailDay"]);
    const done = st.cpassDone || [];
    // 輸出取引の詳細（xlsx）: 1日1回
    const today = new Date().toISOString().slice(0, 10);
    if (st.cpassDetailDay !== today) {
      const b = leafWith(document, "輸出取引の詳細")[0];
      if (b) { b.click(); await chrome.storage.local.set({ cpassDetailDay: today }); await new Promise((r) => setTimeout(r, 2500)); }
    }
    const rows = rowEls.map((tr) => ({ tr, text: tr.innerText, hasCert: leafWith(tr, CERT).length > 0 }));
    const todo = cpassPending(rows, done);
    let n = 0;
    for (const id of todo) {
      const r = rows.find((x) => cpassRowId(x.text) === id);
      const link = r && leafWith(r.tr, CERT)[0];
      if (!link) continue;
      note(`輸出許可書を保存中… ${++n}/${todo.length}`);
      link.click();
      done.push(id);
      await chrome.storage.local.set({ cpassDone: done.slice(-2000) });
      await new Promise((res) => setTimeout(res, 2000));   // 1件ずつ、間を空けて
    }
    note(todo.length ? `✓ 輸出許可書 ${n}件を保存しました（このページの分）` : "✓ このページの輸出許可書は保存済み");
    setTimeout(() => { const t = document.getElementById("aurora-cpass-note"); if (t) t.remove(); }, 5000);
  };
  // 一覧は後から描かれるので、出るまで見張る（最大30秒）
  const obs = new MutationObserver(() => { run(); });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  setTimeout(() => obs.disconnect(), 30000);
  run();
})();
