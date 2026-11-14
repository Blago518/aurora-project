// 相場データの自動更新（Mac の tools/store_publish.py が暗号化して配信したものを読む）。
// 設定コード AURORA1:… に配信 URL と合言葉が入っている。合言葉はこの端末の中だけに保存し、外へは送らない。
// 暗号: PBKDF2-SHA256（iter 回）で鍵を作り、AES-256-GCM で復号 → gzip を展開 → JSON。
"use strict";

function parseSetupCode(code) {
  const m = String(code || "").trim().match(/^AURORA1:([A-Za-z0-9_-]+)$/);
  if (!m) return null;
  let s = m[1].replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  try {
    const bytes = Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
    const j = JSON.parse(new TextDecoder().decode(bytes));
    return j && /^https:\/\//.test(j.u) && j.p ? { url: j.u, pass: j.p } : null;
  } catch (e) {
    return null;
  }
}

async function decryptStore(text, pass) {
  const j = JSON.parse(text);
  const b = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const km = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt: b(j.salt), iterations: j.iter, hash: "SHA-256" },
    km, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b(j.iv) }, key, b(j.ct));
  const stream = new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

async function fetchStore(cfg) {
  const res = await fetch(cfg.url + (cfg.url.includes("?") ? "&" : "?") + "t=" + Date.now(), { cache: "no-store" });
  if (!res.ok) throw new Error("配信先を読めません（" + res.status + "）");
  return decryptStore(await res.text(), cfg.pass);
}

if (typeof module !== "undefined" && module.exports) module.exports = { parseSetupCode, decryptStore };
