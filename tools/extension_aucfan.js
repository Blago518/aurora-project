// Aurora 仕入れ鑑定 ― オークファンの入札予約（tools.aucfan.com/v2/snipe）で動く部品。build_extension.py が extension/aucfan.js にする。
// 1) ヤフオクのパネルの［予約］→ 予約ページで URL を入れて「開く」まで進め、上限額を大きく表示（入札額の欄が見つかれば入れておく）。
//    最後の［予約する］は人が押す（自動では予約しない）。
// 2) 「入札予約中」の一覧から、予約中の出品と入札設定の額を読み取り、ダウンロード/aurora_snipes.csv に保存（落札率の集計用）。
//    現在価格が入札設定を超えている予約は赤く目立たせる。
// ページの作りは変わりうるので、文字（「入札設定」「開く」など）と ヤフオクの URL を手がかりに読む。読めなければ何もしない。
(function () {
  "use strict";
  const AID_RE = /auctions\.yahoo\.co\.jp\/(?:jp\/)?auction\/([a-z]?\d{6,12})/i;
  const yen = (s) => Number(String(s || "").replace(/[^\d]/g, "")) || null;

  // 「入札予約中」の一覧から予約を読む（ヤフオクへのリンクを持つまとまりごと）
  function parseSnipes(root) {
    const out = new Map();
    for (const a of root.querySelectorAll('a[href*="auctions.yahoo.co.jp"]')) {
      const m = (a.getAttribute("href") || "").match(AID_RE);
      if (!m || out.has(m[1])) continue;
      let box = a;
      for (let k = 0; k < 8 && box.parentElement; k++) {
        box = box.parentElement;
        if (/入札設定/.test(box.textContent || "")) break;
      }
      const t = (box.textContent || "").replace(/\s+/g, " ");
      const bid = t.match(/入札設定\s*([\d,]+)\s*円/);
      if (!bid) continue;
      const cur = t.match(/現在価格[^\d]{0,12}([\d,]+)\s*円/);
      const titleEl = [...box.querySelectorAll("a")].find((x) => (x.textContent || "").trim().length > 8 && !/Yahoo!?オークションで見る/.test(x.textContent));
      out.set(m[1], { auction_id: m[1], max_bid: yen(bid[1]), current: cur ? yen(cur[1]) : null,
        title: titleEl ? titleEl.textContent.trim() : "", box });
    }
    return [...out.values()];
  }

  if (typeof module !== "undefined" && module.exports) { module.exports = { parseSnipes }; return; }

  const send = (msg) => new Promise((ok) => { try { chrome.runtime.sendMessage(msg, (r) => ok(r || {})); } catch (e) { ok({}); } });

  function banner(html, color) {
    let b = document.getElementById("aurora-snipe");
    if (!b) {
      b = document.createElement("div"); b.id = "aurora-snipe";
      b.style.cssText = "position:fixed;top:12px;right:12px;z-index:2147483647;max-width:420px;padding:14px 16px;border-radius:12px;" +
        "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Hiragino Sans',sans-serif;box-shadow:0 8px 30px #0005;color:#fff";
      document.body.appendChild(b);
    }
    b.style.background = color || "#0b1728";
    b.innerHTML = html;
  }

  // 一覧: 予約を保存・警告
  function scanList() {
    if (!/入札予約/.test(document.body.textContent || "")) return;
    const list = parseSnipes(document);
    if (!list.length) return;
    let over = 0;
    for (const s of list) {
      if (s.current && s.max_bid && s.current > s.max_bid) {
        over++;
        s.box.style.outline = "3px solid #c0392b"; s.box.style.outlineOffset = "2px";
        s.box.title = `現在 ${s.current.toLocaleString()}円 が入札設定 ${s.max_bid.toLocaleString()}円 を超えています（このままでは落札できません）`;
      }
    }
    const q = (v) => '"' + String(v ?? "").replace(/"/g, '""') + '"';
    const csv = "auction_id,title,max_bid,current,seen_at\n" +
      list.map((s) => [s.auction_id, s.title, s.max_bid, s.current ?? "", new Date().toISOString()].map(q).join(",")).join("\n");
    send({ type: "saveFile", name: "aurora_snipes.csv", csv });
    if (over) banner(`<b>Aurora</b>: 予約 ${list.length}件のうち <b>${over}件</b> は現在価格が入札設定を超えています（赤枠）。上限額を超えて入札しないなら、そのままで構いません。`, "#7a2318");
  }

  // 予約の手伝い: ヤフオクのパネルの［予約］から来たとき
  async function assist() {
    const r = await send({ type: "aucfanPending" });
    const p = r && r.pending;
    if (!p || Date.now() - p.t > 10 * 60 * 1000) return;
    const urlInput = [...document.querySelectorAll("input")].find((i) => /auction/i.test(i.placeholder || "") || /auction/i.test(i.name || ""));
    if (urlInput && !urlInput.value) {
      urlInput.focus();
      urlInput.value = p.url;
      urlInput.dispatchEvent(new Event("input", { bubbles: true }));
      urlInput.dispatchEvent(new Event("change", { bubbles: true }));
      const open = [...document.querySelectorAll("button, input[type=submit], a")].find((b) => (b.textContent || b.value || "").trim() === "開く");
      banner(`<b>Aurora</b>: ${p.title ? p.title.slice(0, 40) + "… の" : ""}予約ページを開いています…`);
      if (open) setTimeout(() => open.click(), 300);
      return;
    }
    // 予約の設定画面: 上限額を表示し、入札額の欄が1つだけ見つかれば入れておく（［予約する］は人が押す）
    const money = [...document.querySelectorAll("input")].filter((i) => /bid|price|amount|入札|金額/i.test(
      [i.name, i.id, i.placeholder, i.getAttribute("aria-label")].join(" ")) && i.type !== "hidden" && i.type !== "checkbox");
    if (money.length === 1 && !money[0].value) {
      money[0].value = String(p.max);
      money[0].dispatchEvent(new Event("input", { bubbles: true }));
      money[0].dispatchEvent(new Event("change", { bubbles: true }));
    }
    banner(`<b>Aurora の上限額</b><div style="font-size:26px;font-weight:900;margin:4px 0">${Number(p.max).toLocaleString()}円</div>` +
      `<div>${p.tax ? "ストアの出品: この額は税抜の入札額です（支払いは×1.1）。" : ""}入札額に<b>この額</b>を入れて、［予約する］を押してください。` +
      `${money.length === 1 ? "（入札額の欄に入れておきました。確認してください）" : ""}</div>`, "#1f5c3a");
  }

  setTimeout(() => { scanList(); assist(); }, 800);
})();
