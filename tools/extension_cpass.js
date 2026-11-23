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
// 表の行をまとめる: 右端の「実行する」列が別の表に描かれる作り（固定列）でも、同じ行の番号とリンクを1つにする。
// 行の鍵は data-row-key（無ければ表の中の何行目か）
function cpassGroup(rows) {
  // rows: [{key, text, hasCert}] → [{text, hasCert}]
  const m = new Map();
  for (const r of rows) {
    const g = m.get(r.key) || { text: "", hasCert: false };
    g.text += " " + r.text; g.hasCert = g.hasCert || r.hasCert;
    m.set(r.key, g);
  }
  return [...m.values()];
}
// 終わりの知らせ（消えない。2026-11-23 オーナー「見逃した」「ダウンロード先で確認しろ」）: 今回押した数と、Chrome のダウンロードの記録にある輸出許可書の数
function cpassSummary(clicked, saved) {
  const s = saved == null ? "" : `・ダウンロード済み 合計${saved}件`;
  return (clicked ? `✓ 輸出許可書 最後のページまで済み（今回 ${clicked}件${s}）` : `✓ 輸出許可書は全部保存済み（今回 0件${s}）`) + "　× 閉じる";
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
    return t;
  };
  const savedCount = () => new Promise((ok) => {   // Chrome のダウンロードの記録で数える（裏方 background.js）
    setTimeout(() => ok(null), 4000);   // 裏方が答えなくても、終わりの知らせは必ず出す
    try { chrome.runtime.sendMessage({ type: "cpassSaved" }, (r) => { void chrome.runtime.lastError; ok(r && typeof r.count === "number" ? r.count : null); }); } catch (e) { ok(null); }
  });
  let busy = false, lastSig = "", total = 0;
  const collect = () => {
    const raw = [];
    for (const tb of document.querySelectorAll("tbody")) {
      [...tb.querySelectorAll(":scope > tr")].forEach((tr, i) => raw.push({ tr, key: tr.getAttribute("data-row-key") || "i" + i, text: tr.innerText, hasCert: leafWith(tr, CERT).length > 0 }));
    }
    const groups = new Map();
    for (const r of raw) { const g = groups.get(r.key) || { trs: [], text: "", hasCert: false }; g.trs.push(r.tr); g.text += " " + r.text; g.hasCert = g.hasCert || r.hasCert; groups.set(r.key, g); }
    return [...groups.values()].filter((g) => cpassRowId(g.text));
  };
  const run = async () => {
    if (busy || !/各注文番号の料金明細/.test(document.body.innerText)) return;
    const rowsNow = collect();
    if (!rowsNow.length) return;   // 表がまだ出ていない
    const sig = rowsNow.map((g) => cpassRowId(g.text)).join(",");
    if (sig === lastSig) return;   // 同じ表はもう見た（ページ送り・検索で表が変われば、また見る）
    busy = true; lastSig = sig;
    try { await work(rowsNow); } finally { busy = false; }
  };
  const work = async (rowsNow) => {
    const st = await chrome.storage.local.get(["cpassDone", "cpassDetailDay"]);
    const done = st.cpassDone || [];
    // 輸出取引の詳細（xlsx）: 1日1回
    const today = new Date().toISOString().slice(0, 10);
    if (st.cpassDetailDay !== today) {
      const b = leafWith(document, "輸出取引の詳細")[0];
      if (b) { b.click(); await chrome.storage.local.set({ cpassDetailDay: today }); await new Promise((r) => setTimeout(r, 2500)); }
    }
    const rows = rowsNow;
    const todo = cpassPending(rows, done);
    let n = 0;
    for (const id of todo) {
      const r = rows.find((x) => cpassRowId(x.text) === id);
      const link = r && r.trs.map((tr) => leafWith(tr, CERT)[0]).find(Boolean);
      if (!link) continue;
      note(`輸出許可書を保存中… ${++n}/${todo.length}`);
      link.click();
      done.push(id);
      await chrome.storage.local.set({ cpassDone: done.slice(-2000) });
      await new Promise((res) => setTimeout(res, 2000));   // 1件ずつ、間を空けて
    }
    // 次のページがあれば自動で進む（料金明細は何ページかに分かれる。2026-11-23 1ページ目の7件だけで止まった）
    const next = [...document.querySelectorAll(".ant-pagination-next, [aria-label='next page'], [title='Next Page'], [title='次のページ'], .btn-next")]
      .find((e) => !/disabled/.test(e.className) && !e.disabled && e.getAttribute("aria-disabled") !== "true");
    total += n;
    if (next) {
      note(`✓ このページの ${n}件を保存。次のページへ…`);
      (next.querySelector("button") || next).click();
      return;
    }
    await new Promise((res) => setTimeout(res, 3000));   // 最後の1件が保存されるのを待ってから数える
    const t = note(cpassSummary(total, await savedCount()));   // 閉じるまで消さない（5秒で消えて見逃した）
    t.style.cursor = "pointer"; t.onclick = () => t.remove();
    total = 0;
  };
  // 一覧は後から描かれ、ページの中で画面が切り替わる（料金明細へ移る・ページ送り）ので、ずっと見張る（間を空けて）
  let timer = 0;
  const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(run, 800); });
  obs.observe(document.documentElement, { childList: true, subtree: true });
  run();
})();
