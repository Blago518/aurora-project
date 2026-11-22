"""おすすめの4分類 MF・AF・Film・Digital（2026-11-22 オーナー）. 売れた商品名から決める."""
import unittest

import build_store as bs


class RecCat(unittest.TestCase):
    LENS = {
        "mf": ["Nikon Ai-S Nikkor 50mm f/1.2", "Canon New FD 50mm f1.4", "Pentax SMC Takumar 55mm F1.8",
               "Olympus OM G.Zuiko 50mm f1.4", "Contax Carl Zeiss Planar T* 85mm F1.4 AEG", "Tamron SP 90mm f/2.5 52BB",
               "Minolta MD Rokkor 50mm", "Leica Summicron M 50mm F2", "Nikon Nikkor-N.C Auto 35mm f1.4 Ai Converted"],
        "af": ["Nikon AF Nikkor 50mm f/1.4D", "Nikon AF-S 70-200mm F2.8G ED VR", "Canon EF 135mm F2 L USM",
               "Pentax smc DA 40mm F2.8 Limited", "Pentax FA 43mm F1.9 Limited", "Olympus M.Zuiko 12-40mm F2.8 PRO",
               "Sony FE 24-70mm F4 ZA OSS", "Sigma 18-300mm DC Macro OS HSM", "Nikon Ai AF Nikkor 35mm f/2D", "Canon EF 50mm F1.8 II",
               "Contax Planar 45mm F2 G", "Contax Biogon 28mm F2.8 for G", "Contax 645 Planar 80mm F2", "Hasselblad HC 80mm F2.8",
               "Mamiya 645 AF 80mm F2.8", "Sony Vario-Sonnar 24-70mm f/2.8"],
    }
    CAMERA = {
        "film": ["Nikon F3 HP", "Nikon F100", "Canon EOS 5 film", "Canon AE-1 Program", "Contax T2", "Pentax K1000",
                 "Leica M6", "Leica M3", "Olympus PEN F", "Minolta α-7", "Hasselblad 500CM", "Nikon FM2", "Olympus XA2", "Mamiya RB67",
                 "Contax G2", "Contax G1 Body", "Polaroid SX70", "Minolta α7 film", "Minolta α807si", "Kyocera Samurai Z2",
                 "Canonet QL17 G-III", "Pentax 645N", "Nikon F100", "Nikon Zoom 500 AF"],
        "digital": ["Nikon D750 24.3MP", "Canon EOS 5D Mark II", "Ricoh GR III", "Olympus OM-D E-M5 Mark II", "Pentax K-1",
                    "Fujifilm X-T3", "Leica M10", "Leica D-LUX 4", "Canon PowerShot SX720 HS", "Sony α7 II", "Nikon Z9", "Fujifilm X100V",
                    "Sony Cyber Shot DSC-W350 Silver", "Nikon 1 J5 Body Black", "Nikon Z 6II", "Nikon Z fc", "OM SYSTEM OM-5 Mark II",
                    "Canon PowerShot G7 X Mark II", "Canon G7 X", "Sony A7II", "SONY ILCE-7M2", "Panasonic Lumix G9"],
    }

    def test_lens(self):
        for cat, ts in self.LENS.items():
            for t in ts:
                self.assertEqual(bs.rec_cat("lens", [t]), cat, t)

    def test_camera(self):
        for cat, ts in self.CAMERA.items():
            for t in ts:
                self.assertEqual(bs.rec_cat("camera", [t]), cat, t)

    def test_majority(self):
        self.assertEqual(bs.rec_cat("lens", ["Nikon 50mm F1.4", "Nikon AF 50mm F1.4D", "Nikon AF Nikkor 50mm 1.4D"]), "af")


    def test_js_same(self):
        """店舗アプリの古いデータ用の分類（index.html の recCat）が Python と同じ."""
        import json
        import shutil
        import subprocess
        from pathlib import Path
        if not shutil.which("node"):
            self.skipTest("node がありません")
        html = (Path(__file__).resolve().parent.parent / "store" / "index.html").read_text(encoding="utf-8")
        import re
        src = "\n".join(re.search(r"^const %s = .*$" % n, html, re.M).group(0)
                         for n in ("AF_STRONG_RE", "DIGITAL_CAM_RE", "LENS_LIKE_RE"))
        cases = [("lens", t) for ts in self.LENS.values() for t in ts] + [("camera", t) for ts in self.CAMERA.values() for t in ts]
        js = src + """
const cases = %s;
console.log(JSON.stringify(cases.map(([k, t]) => {
  const lens = k === "lens" || LENS_LIKE_RE.test(t);
  return lens ? (AF_STRONG_RE.test(t) && !/\\bMF\\b|manual\\s*focus/i.test(t) ? "af" : "mf") : (DIGITAL_CAM_RE.test(t) ? "digital" : "film");
})));""" % json.dumps(cases, ensure_ascii=False)
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [bs.rec_cat(k, [t]) for k, t in cases])

    def test_lens_in_camera(self):
        """「カメラ」に入ったレンズ（200-500mm F5.6E）はレンズの枠へ（2026-11-22 オーナー指摘）."""
        self.assertEqual(bs.rec_key("Nikon AF-S 200-500mm F5.6E ED VR")[2], "lens")
        self.assertIn(bs.rec_cat("camera", ["Nikon AF-S 200-500mm F5.6E ED VR"]), ("af", "mf"))
        self.assertEqual(bs.rec_cat("camera", ["Nikon R10 SUPER 8mm"]), "film")


    # 必ずその枠に出るはずの機種（eBay の商品名のような、レンズの仕様つきの長い形も）。2026-11-22 オーナー指摘「フィルムカメラで何故レンズが出る？」
    MUST = {
        "film": ["Contax T2 Sonnar 38mm F2.8 Titanium", "Contax G2 Body", "Nikon F3 HP Body", "Nikon FM2 + Ai 50mm F1.4",
                 "Canon AE-1 Program w/ New FD 50mm F1.8", "Ricoh GR1v 28mm F2.8", "Leica M6 TTL 0.72", "Minolta α-7 + 50mm F1.4",
                 "Olympus Trip 35", "Pentax 67II Body", "Hasselblad 500CM 80mm F2.8", "Polaroid SX70", "Nikon R10 SUPER 8mm",
                 "Olympus mju Zoom 35-70mm", "Contax TVS 28-56mm", "Konica Hexar AF 35mm F2", "Rolleiflex 2.8F Planar 80mm F2.8",
                 "Mamiya 7 II 80mm F4",   # ボディ＋レンズで売れることが多い名前はカメラ（レンズだけは「N 80mm for Mamiya 7」の形）
                 "Hasselblad 500CM 80mm F2.8", "Leica M6 TTL Summicron 50mm F2", "Nikon F3 HP w/ Ai-S 50mm F1.4", "Contax G2 Planar 45mm F2 kit",
                 # 「w/」を書かずにボディとレンズ名が並ぶ形（2026-11-22 オーキド指摘）
                 "Nikon FM2 Nikkor 50mm F1.4", "Olympus OM-2N Zuiko 50mm F1.8", "Bronica SQ-A Zenzanon 80mm F2.8",
                 "Mamiya C330 Sekor 80mm F2.8", "Mamiya M645 1000S Sekor C 80mm F2.8", "Pentax 67 II SMC 105mm F2.4",
                 "Minolta CLE M-Rokkor 40mm F2"],
        "digital": ["Fujifilm X100V 23mm F2", "Nikon D750 24.3MP Body", "Sony α7 III Body", "Ricoh GR III 18.3mm F2.8",
                    "Canon EOS 5D Mark II 24-105mm F4", "Canon PowerShot G7 X Mark II", "Leica M10 Body", "Nikon Z 6II Body",
                    "Canon IXY 310", "Olympus OM-D E-M5 Mark II", "Nikon Df 50mm F1.8G", "Ricoh GR IIIx"],
        "mf": ["Nikon Ai-S 50mm F1.2", "Canon New FD 50mm F1.4", "Pentax 67 105mm F2.4", "Contax Planar 85mm F1.4 AEG",
               "Leica Summilux-M 35mm F1.4", "Pentax SMC Takumar 55mm F1.8", "Olympus Pen Zuiko 38mm f/2.8",
               "Mamiya RB67 Sekor C 90mm F3.8", "Mamiya RZ67 Sekor Z 110mm F2.8 W", "Leica L39 Elmar 50mm F3.5",
               "Tamron SP 52B 90mm F2.5 Macro", "Olympus OM Zuiko 50mm F1.2", "Mamiya N 80mm F4 for Mamiya 7", "Bronica Zenzanon PS 80mm F2.8", "Mamiya Sekor C 80mm F2.8 for M645",
               "Hasselblad Planar 80mm F2.8 CF"],
        "af": ["Nikon 6.7-13mm F3.5-5.6", "Nikon 1 NIKKOR VR 6.7-13mm f/3.5-5.6", "Nikon 1 NIKKOR 10-30mm F3.5-5.6",  # 2026-11-22 Film に出ていた
               "Nikon AF-S 200-500mm F5.6E ED VR", "Nikon AF-S DX 18-140mm", "Canon EF 70-200mm F2.8L IS II",
               "Sony FE 24-70mm F2.8 GM", "Contax Planar 45mm F2 G", "Nikon Z 24-70mm F4 S", "Sony SEL1635Z",
               "Tamron A09 28-75mm F2.8 Di", "Olympus M.Zuiko Digital ED 12-40mm F2.8"],
    }

    def test_rollei_brand(self):
        """「Zeiss Rolleiflex 2.8F Planar」のメーカーは Rollei（Carl Zeiss にしない。2026-11-22）."""
        label = bs.rec_key("Rolleiflex 2.8F Planar 80mm F2.8")[0]
        self.assertTrue(label.startswith("Rollei ") and "2.8F" in label, label)

    def test_must_python(self):
        """Mac の通り道（rec_key で種類 → rec_cat）で、必ずその枠に出る."""
        for cat, ts in self.MUST.items():
            for t in ts:
                k = bs.rec_key(t)
                self.assertIsNotNone(k, t)
                self.assertEqual(bs.rec_cat(k[2], [t]), cat, (t, k))

    def test_must_app_old_data(self):
        """スマホの古いデータ（Mac の分類が無い・カメラ/レンズの種類が間違っていることもある）でも、アプリが同じ枠に出す."""
        import json
        import re
        import shutil
        import subprocess
        from pathlib import Path
        if not shutil.which("node"):
            self.skipTest("node がありません")
        html = (Path(__file__).resolve().parent.parent / "store" / "index.html").read_text(encoding="utf-8")
        a = html.index("const AF_STRONG_RE")
        b = html.index("const recCat")
        b = html.index("\n};", b) + 3
        src = re.sub(r"^const (MF_RE|brandName) .*$", "", html[a:b], flags=re.M)
        cases = []
        for cat, ts in self.MUST.items():
            label = None
            for t in ts:
                label, _, kind = bs.rec_key(t)
                for k in (("camera", "lens") if re.search(r"\\d+(-\\d+)?mm", label) else ("camera",)):   # 焦点距離のある物は、種類が間違ったデータでも
                    cases.append([cat, {"label": label, "kind": k}])
        js = src + "\nconst DIGITAL_RE = /$^/;\nconst cases = %s;\nconsole.log(JSON.stringify(cases.map(([c, r]) => recCat(r))));" % json.dumps(cases, ensure_ascii=False)
        out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
        for (cat, r), got in zip(cases, out):
            self.assertEqual(got, cat, r)


    def test_js_patterns_synced(self):
        """アプリの正規表現は Mac（build_store）と同じ文字列."""
        import re
        from pathlib import Path
        html = (Path(__file__).resolve().parent.parent / "store" / "index.html").read_text(encoding="utf-8")
        for name in ("AF_STRONG_RE", "DIGITAL_CAM_RE", "LENS_LIKE_RE", "KIT_WORDS_RE", "FIXED_BODY_RE", "LENS_FAMILY_RE"):
            js = re.search(r"^const %s = /(.*)/i;$" % name, html, re.M).group(1).replace("\\/", "/")
            self.assertEqual(js, getattr(bs, name).pattern, name)


    def test_upload_recs(self):
        """おすすめ全部を古物台帳「Mac_おすすめ」に送る形（枠・表示名・元の商品名）."""
        import json
        import tempfile
        from pathlib import Path
        import ledger_sync as ls
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "store_data.json"
            p.write_text(json.dumps({"recs": [{"cat": "film", "brand": "Contax", "label": "Contax T2", "kind": "camera", "sales": 3,
                                                "usd": 400, "examples": ["Contax T2 Sonnar 38mm F2.8"]}]}), encoding="utf-8")
            sent = []
            from unittest import mock
            with mock.patch.object(ls.keychain, "get", return_value="x"):
                self.assertEqual(ls.upload_recs(p, post=lambda text: sent.append(text) or "OK"), 1)
        self.assertIn("Film,Contax,Contax T2,カメラ,3,400,Contax T2 Sonnar 38mm F2.8", sent[0])


if __name__ == "__main__":
    unittest.main()
