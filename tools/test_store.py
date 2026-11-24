"""店舗リサーチアプリ（store/）のテスト. python -m unittest discover -s tools で実行."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import appraise as ba
import build_store as bs

CFG = ba.load_config(None)
SAMPLES = Path(__file__).resolve().parent / "samples"


class CoreSync(unittest.TestCase):
    def test_core_js_is_up_to_date(self):
        """store/core.js が拡張機能の判定ロジックのコピーのままか（ずれたら build_store.py を実行）."""
        self.assertEqual(bs.CORE_JS.read_text(encoding="utf-8"), bs.core_js())

    def test_no_data_in_public_files(self):
        """公開する store/ に相場データが入っていないこと."""
        for p in bs.CORE_JS.parent.iterdir():
            if p.suffix in (".js", ".html", ".webmanifest"):
                self.assertNotIn('"refs":[', p.read_text(encoding="utf-8").replace(" ", ""), p.name)


class RecKey(unittest.TestCase):
    def test_mount_separates_lenses(self):
        fd = bs.rec_key("Canon New FD 50mm f/1.4 Lens")
        self.assertEqual(fd[1:], ("canon newfd 50mm f1.4", "lens"))
        self.assertEqual(bs.rec_key("Canon NFD 50mm F1.4")[1], fd[1])
        self.assertNotEqual(bs.rec_key("Canon EF 50mm F1.4 USM")[1], fd[1])

    def test_camera(self):
        self.assertEqual(bs.rec_key("Olympus OM-1 35mm SLR")[1:], ("olympus om-1", "camera"))
        self.assertEqual(bs.rec_key("Nikon FM2 Black Body Exc+++")[0], "Nikon FM2")
        self.assertNotEqual(bs.rec_key("Canon AE-1 Program")[1], bs.rec_key("Canon AE-1")[1])

    def test_descriptive_words_are_not_models(self):
        """2026-11-24 オーナー「all works は機種じゃない」。説明・色の語を機種名にせず、同じ機種を1行にまとめる（実際のおすすめの商品名）."""
        cases = {
            "ALL WORKS [Top MINT] Contax RX SLR 35mm Film Camera Black Body From JAPAN": "Contax RX",
            "Contax Aria ALL WORKS": "Contax Aria",
            "Contax Aria 35mm SLR Film Camera Body Black": "Contax Aria",
            "Pentax MX Black All Works": "Pentax MX",
            "Pentax MX Silver Body Tested": "Pentax MX",
            "Konica Hexar Silver Fully Working": "Konica Hexar",
            "Nikon FE silver": "Nikon FE",
            "Nikon FM Silver": "Nikon FM",
            "Nikon Df Silver": "Nikon Df",
            "Olympus Pen F Half Frame": "Olympus Pen F",
            "Fujifilm X Half Silver": "Fujifilm X Half",
            "Leica IIIF Red Dial + Self Timer": "Leica IIIF Red",
        }
        for t, label in cases.items():
            self.assertEqual(bs.rec_key(t)[0], label, t)
        self.assertIsNone(bs.rec_key("CONTAX ALL WORKS 35mm film camera"))   # 機種が書いていない物は出さない

    def test_not_model_numbers_and_editions(self):
        """状態・通し番号は型番にしない（オーキドの見回り）。記念モデルは元の機種と別の行（2026-11-24 オーナー「aria と aria 70 years は別」）."""
        self.assertEqual(bs.rec_key("[Exc+5] Canon New F-1 AE Finder")[0], "Canon New F-1")
        self.assertEqual(bs.rec_key("Count 001 Sony α7 III Body")[0], "Sony α7 III")
        self.assertEqual(bs.rec_key("Nikon New FM2 silver")[0], "Nikon FM2")
        self.assertEqual(bs.rec_key("Nikon F3 HP Body")[0], "Nikon F3 HP")
        self.assertEqual(bs.rec_key("Olympus OM-4 Ti Black")[0], "Olympus OM-4 Ti")
        aria = bs.rec_key("Contax Aria Body")[1]
        for t in ("CONTAX Aria 70 Years Silver", "Contax Aria 70th Anniversary", "CONTAX Aria 70周年 記念モデル", "Contax Aria 70years Limited", "Contax Aria 70 Year"):
            k = bs.rec_key(t)
            self.assertEqual(k[0], "Contax Aria 70 Years", t)
            self.assertNotEqual(k[1], aria, t)

    def test_body_with_lens_keeps_body_name(self):
        """ボディ＋レンズは「Contax 50mm F1.4」のようなレンズの数字だけの名前にしない。ボディの型番の数字（F2・67）はレンズの数字と取り違えない."""
        self.assertEqual(bs.rec_key("Contax Aria Body + Planar 50mm F1.4")[0], "Contax Aria + 50mm F1.4")
        self.assertEqual(bs.rec_key("Kowa Six + 85mm 2.8 Silver")[0], "Kowa Six + 85mm F2.8")
        self.assertEqual(bs.rec_key("Nikon FA + Ai 50mm 1.8")[0], "Nikon FA + 50mm F1.8")
        self.assertEqual(bs.rec_key("Nikon F2 Ai 50mm F1.4")[0], "Nikon F2 + 50mm F1.4")
        self.assertEqual(bs.rec_key("Pentax 67 Body")[0], "Pentax 67")
        # レンズの商品名は今までどおり（状態の語やフードを名前にしない）
        self.assertEqual(bs.rec_key("[Top MINT w/Hood] NIKON AF-S NIKKOR 200-500mm f/5.6E ED VR F mount JAPAN 3148637")[0], "Nikon AF-S 200-500mm F5.6E")
        self.assertEqual(bs.rec_key("Minolta M Rokkor 40mm f/2 CLE")[0], "Minolta 40mm F2 CLE")

    def test_unknown_brand(self):
        self.assertIsNone(bs.rec_key("Some Camera"))


class RecMakerModel(unittest.TestCase):
    """おすすめのメーカーと型番（2026-11-24 オーナー「om はオリンパスですよね？メーカーも型番もよく調べて精査して」）.
    「Mac_おすすめ」の実際の商品名 559件を全部読み直して見つけた誤り。直したら、その商品名をここに足す."""

    def test_om_is_olympus(self):
        """OM SYSTEM（2021年にオリンパスのカメラ事業を引き継いだ）は Olympus の枠。フィルムの OM-1 とは別の行."""
        for t in ("OM SYSTEM OM-1", "OM SYSTEM OM-5 ボディ", "OMデジタル OM-5", "OM SYSTEM M.Zuiko 12-40mm F2.8 PRO II"):
            label = bs.rec_key(t)[0]
            self.assertEqual(bs.rec_brand(label), "Olympus", (t, label))
        self.assertEqual(bs.rec_key("OM SYSTEM OM-1")[0], "OM SYSTEM OM-1")
        self.assertNotEqual(bs.rec_key("OM SYSTEM OM-1")[1], bs.rec_key("Olympus OM-1 Body")[1])
        self.assertEqual(bs.rec_key("OM Zuiko 50mm F1.8")[0].split()[0], "Olympus")   # フィルムの OM マウントのレンズ
        self.assertEqual(bs.rec_key("OM-D E-M5 Mark II")[0], "Olympus OM-D E-M5 Mark II")
        self.assertEqual(bs.rec_cat("camera", ["OM SYSTEM OM-1"]), "digital")

    def test_condition_tags_are_not_model(self):
        """[Exc+5]・[MINT w/Caps]・w/Box はレンズの名前・種類を変えない（AF のレンズが Film に出ていた）."""
        for t, label, cat in [("[Exc+5] Canon EF 135mm f/2.8 Soft focus Lens Canon EF Mount From JAPAN", "Canon EF 135mm F2.8", "af"),
                              ("[MINT w/Caps] Canon EF 400mm F/5.6 L USM Telephoto Prime AF Lens From JAPAN", "Canon EF 400mm F5.6 L AF", "af"),
                              ("Canon EF 135mm f/2 L USM w/box", "Canon EF 135mm F2 L", "af"),
                              ("[MINT+++] Nikon NIKKOR 135mm f/3.5 Ai from FA,FE2,FM2,FM3A,F4,F5,F6 From JAPAN", "Nikon 135mm F3.5 Ai", "mf"),
                              ("[Top MINT w/Box] Nikon AF-S NIKKOR 80-400mm f/4.5-5.6G ED VR Lens From JAPAN", "Nikon AF-S 80-400mm F4.5-5.6", "af")]:
            k = bs.rec_key(t)
            self.assertEqual((k[0], bs.rec_cat(k[2], [t])), (label, cat), t)

    def test_f_number_without_f(self):
        """「100mm 2.8」の 2.8 は F値。表示に F を付け、f/2.8 と書いた同じレンズと同じ行にする."""
        self.assertEqual(bs.rec_key("Canon EF 100mm 2.8 USM")[0], "Canon EF 100mm F2.8")
        self.assertEqual(bs.rec_key("Canon EF 100mm 2.8 USM")[1], bs.rec_key("Canon EF 100mm f/2.8 USM")[1])
        self.assertEqual(bs.rec_key("Nikon Z 24-120mm 4")[0], "Nikon Z 24-120mm F4")
        self.assertEqual(bs.rec_key("Canon 35mm 2.8 L39 + Finder")[0], "Canon 35mm F2.8 L39")
        k = bs.rec_key("Canon 35mm 2.8 L39 + Finder")
        self.assertEqual(bs.rec_cat(k[2], ["Canon 35mm 2.8 L39 + Finder"]), "mf")

    def test_kit_label(self):
        """ボディ＋レンズは「+」でつなぐ。レンズ固定のカメラ・8mm には付けない."""
        cases = {"Fujifilm X-T2 + 18-55mm 2.8-4": "Fujifilm X-T2 + 18-55mm F2.8-4",
                 "Canon EOS 90D + EF-S 18-135 IS USM 箱付": "Canon EOS 90D + 18-135mm",
                 "Fujifilm X-A5 XC Body + XC15-45mm f/3.5-5.6 OIS PZ 箱付": "Fujifilm X-A5 + 15-45mm F3.5-5.6",
                 "Canon 7 Black + 35mm 2 L39": "Canon 7 + 35mm F2 L39",
                 "Nikon Nikonos V Orange + 35mm 2.5": "Nikon Nikonos V + 35mm F2.5",
                 "leica IIIF Red Dial Self Timer + Summicron 50mm 2 L39": "Leica IIIF Red + 50mm F2",
                 "Mamiya RB67 SD + Sekor C 90mm 3.8": "Mamiya RB67 Pro SD + 90mm F3.8",
                 "Olympus mju Zoom 35-70mm": "Olympus mju Zoom 35-70mm",
                 "Canon 514 XL Super8 Movie Camera 9-45mm f/1.4": "Canon 514 XL Super 8 9-45mm F1.4",
                 "[Top MINT w/Box ] Canon 1014 XL-S Super 8 8mm Film Movie Camera From JAPAN": "Canon 1014 XL-S Super 8"}
        for t, label in cases.items():
            self.assertEqual(bs.rec_key(t)[0], label, t)

    def test_model_names(self):
        cases = {"Minolta a7 Dynax 7 Maxxum 7 Body w/Box": ("Minolta α-7", "film"),
                 "Minolta a-7 Digital Camera": ("Minolta α-7 Digital", "digital"),
                 "Ricoh GR Digital IV": ("Ricoh GR Digital IV", "digital"),
                 "Yashica Matt 124G": ("Yashica Mat-124G", "film"),
                 "Yashica Mat-124 G 革ケース付": ("Yashica Mat-124G", "film"),
                 "Hasselblad 500C/M + A12 II Filmback": ("Hasselblad 500CM", "film"),
                 "Nikon F3 P HP + MD-4": ("Nikon F3P HP", "film"),
                 "ZEISS Contax IIIa Type Color Dial + Sonnar 50mm 2": ("Contax IIIa Color + 50mm F2", "film"),
                 "Olympus Six F.C. 7.5cm F3.5": ("Olympus Six F.C. 7.5cm F3.5", "film"),
                 "Rollei Rolleicode III TLR Xener 75mm": ("Rollei Rolleicord III 75mm", "film"),
                 "[Near MINT] Fujifilm TIARA Zoom 28-56mm Point & Shoot Film Camera from JAPAN": ("Fujifilm TIARA Zoom 28-56mm", "film"),
                 "Leica Summaron 35mm 3.5cm F3.5 Lens L39 LTM Mount": ("Leica 35mm F3.5 L39", "mf"),
                 "Nikon NIKKOR-P 10.5cm 2.5 S Mount": ("Nikon 10.5cm F2.5", "mf"),
                 "Sony Alpha SAL85F28 85mm f/2.8 SAM": ("Sony 85mm F2.8", "af"),
                 "TAMRON SP 70-300 4-5.6 A005 for Canon": ("Tamron 70-300mm F4-5.6", "af"),
                 "Nikon 135 3.5 for Bronica S": ("Nikon 135mm F3.5", "mf"),
                 "Nikon AF-S 24-70mm f/2.8 E ED VR + FTZ": ("Nikon AF-S 24-70mm F2.8", "af"),
                 "Nikon S3 Rangefinder": ("Nikon S3", "film"),
                 "[Exc+5] Olympus XA Rangefinder Film Camera From JAPAN": ("Olympus XA", "film"),
                 "[Top MINT] Olympus μ mju ZOOM WIDE 80 Point&Shoot 35mm Film Camera From JAPAN": ("Olympus mju Zoom Wide 80", "film")}
        for t, (label, cat) in cases.items():
            k = bs.rec_key(t)
            self.assertEqual((k[0], bs.rec_cat(k[2], [t])), (label, cat), t)

    def test_digital_not_film(self):
        for t in ("Canon SX 710 HS", "Canon A2600 Pink", "Canon Power Shot S95 Black", "Fujifilm X-100F Black", "Nikon S570 Pink",
                  "Pentax Q10 + 02 Lens", "RICOH WG-50 Black", "Sony W610 Blue"):
            k = bs.rec_key(t)
            self.assertEqual(bs.rec_cat(k[2], [t]), "digital", (t, k))
        for t in ("Olympus XA2", "Nikon S3 Rangefinder", "Nikon S2"):
            self.assertEqual(bs.rec_cat("camera", [t]), "film", t)

    def test_af_mf(self):
        cases = {"Olympus 12-40mm 2.8": "af", "Olympus 100-400mm 5.6-6.3": "af", "olympus 8mm 3.5 ed": "af", "LUMIX 20-60mm 3.5-5.6": "af",
                 "Canon RF-S 55-210mm 5-7.1": "af", "Fujifilm 18-120mm 4 LM PZ WR": "af", "SIGMA 17-50mm 2.8 for Pentax": "af",
                 "SIGMA 20mm 1.8 for Canon EF": "af", "Pentax SMC DA Fisheye 10-17mm f/3.5-4.5 ED IF": "af",
                 "Hasselblad FE 250mm f/4": "mf", "Olympus G.Zuiko 28mm f/3.5": "mf", "Olympus Zuiko Auto 35-70mm f/3.6": "mf",
                 "Olympus 21mm f/3.5": "mf", "Olympus OM Zuiko 8mm F2.8 Fisheye": "mf"}
        for t, cat in cases.items():
            k = bs.rec_key(t)
            self.assertEqual(bs.rec_cat(k[2], [t]), cat, (t, k))

    def test_accessories_not_recommended(self):
        """カメラでもレンズでもない物（ファインダー・フィルムバック・フラッシュ・双眼鏡・フィルター・モーター・アダプター）は出さない."""
        skip = ["Bronica Filmback SQ 135N", "Mamiya 645 120 Filmback for RB67", "Mamiya Cds Finder", "Rollei Prism Finder for Rolleiflex",
                "Nikon Action Finder DA-1", "Nikon Eye Level DE-2", "Nikon DP-1 Finder", "Nikon DR-3", "Nikon High Magnification Viewfinder DW-21",
                "Hasselblad PME", "Hasselblad Diopter 0", "Hasselblad Quick Focusing Handle", "Horseman Universal Zoom Finder",
                "Tested [Almost MINT] Contax TLA140 Shoe Mount Flash w/ Case For G1 G2 From JAPAN", "konica flash for Hexar",
                "Kowa Genesis 44 Prominar 8.5x44 Field 7.0° Binoculars", "Nikon 7x35 A 7.3", "Pentax Papilio II 6.5x21 元箱付",
                "Kowa TSN-884 PROMINAR + TE-11WZ 25-60x", "Leica SERIE 7 UVa 13009", "Leica 12504 Hood + Filter 13009",
                "Pentax Bayonet Polarizing Lens Filter 67mm for 6x7 67", "Mamiya Hood 48mm for C330", "Pentax Hood for 6x7 Takumar 75mm 4.5",
                "Fujifilm Film Cassette iii x8個", "Intenscreen for Nikon FE, FA, FM2, FE2", "Nikon FTZ II", "Nikon MD-4", "Nikon MD-4 for F3",
                "Nikon MF-6B", "Nikon PB-６", "Nikon TC-20EIII", "Canon Macro Ring LITE MR-14EX", "Minolta Autometer III", "Pentax Spot Meter V",
                "Pentax Dial Data LX", "Voigtlander Trigger Winder For Besser T ・ R2", "Linhof Cam 150mm", "Horseman P T 18cm Cam",
                "Nippon Kogaku Nikkorex 8 Tele 2x", "Nikon Digital SLR Camera Attachment FSA-L1"]
        for t in skip:
            self.assertTrue(bs.rec_skip(t), t)
        keep = ["Nikon S3 Rangefinder", "Topcon 35-S 35mm Rangefinder Film Camera Topcor 44mm f/2", "Hasselblad 500C/M + A12 II Filmback",
                "Nikon F3 P HP + MD-4", "Canon Serenar 85mm f/2 + 85mm Finder", "Leica Elma-R 180mm f/4 3-Cam", "Nikon F2 Photomic A Silver",
                "Nikon F Eye Level silver", "Pentax 6x7 Eye Level Body", "Olympus OM-1 Body", "Mamiya RZ67 + 110mm 2.8 W + 120 Filmback"]
        for t in keep:
            self.assertFalse(bs.rec_skip(t), t)


class Recommendations(unittest.TestCase):
    def test_sample(self):
        recs = bs.recommendations(ba.read_sales(SAMPLES / "sales_history.csv"), CFG, 150.0)
        # Canon NewFD 50mm F1.4（$78〜85）は、どの値段で買っても基準（$99以下は利益4,000円）に届かないので出さない
        # （アプリに「税込 上限なし」と出て、いくらでも買えるように読めた。2026-11-24）
        self.assertEqual([r["label"] for r in recs], ["Nikon FM2"])
        self.assertTrue(all(r["maxBuy"] > 0 for r in recs))
        fm2 = recs[0]
        self.assertEqual((fm2["sales"], fm2["usd"], fm2["kind"]), (3, 255.0, "camera"))
        # 上限額で買えば基準を満たす
        c = bs.store_calc(fm2["usd"], CFG, 150.0, fm2["maxBuy"])
        self.assertGreaterEqual(c["profit_now_jpy"], CFG["judge"]["min_profit_jpy"])
        self.assertGreaterEqual(c["margin_now_pct"], CFG["judge"]["min_margin_pct"])
        # ジャンク販売だけの機種（Pentax 67 For Parts）はおすすめに出さない
        self.assertNotIn("Pentax 67", [r["label"] for r in recs])


    def test_memo_only_model(self):
        """販売履歴の無い機種（Contax Aria）も、相場メモに登録すればおすすめに出る。販売履歴がある機種は相場メモで上書きしない（2026-11-24 オーナー「Ariaいれたら？」）."""
        rows = ba.read_sales(SAMPLES / "sales_history.csv")
        memo = [{"title": "Contax Aria", "sold_price_usd": "300", "source": "相場メモ（Near MINT）", "memo": "拡張機能で直した 2026-11-20"},
                {"title": "Contax Aria", "sold_price_usd": "340", "source": "相場メモ（Near MINT）", "memo": "拡張機能で直した 2026-11-25"},
                {"title": "Nikon FM2", "sold_price_usd": "999", "source": "相場メモ", "memo": "拡張機能で直した 2026-11-25"}]
        for m in memo:
            m["_price"] = float(m["sold_price_usd"])
        recs = {r["label"]: r for r in bs.recommendations(rows + memo, CFG, 150.0)}
        aria = recs["Contax Aria"]
        self.assertEqual((aria["sales"], aria["usd"], aria["kind"], aria["cat"]), (0, 340.0, "camera", "film"))   # いちばん新しい登録
        self.assertTrue(aria["basis"].startswith("相場メモ"))
        self.assertGreater(aria["maxBuy"], 0)
        self.assertEqual(recs["Nikon FM2"]["usd"], 255.0)   # 販売実績のある機種は販売実績のまま


class StoreParity(unittest.TestCase):
    """スマホアプリ（core.js）の上限額・利益が Python と一致すること."""

    def test_same_as_python(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        cases = [(255.0, 18000), (81.5, 1500), (120.0, 30000)]
        py = []
        for u, price in cases:
            c = bs.store_calc(u, CFG, 150.0, price)
            py.append([c["max_bid_jpy"], c["breakeven_bid_jpy"], c["profit_now_jpy"]])
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const D=require({json.dumps(str(dp))});"
                    f"const c=makeCore(D);console.log(JSON.stringify({json.dumps(cases)}.map(([u,p])=>"
                    "{const r=c.calculate({price:p,ship:0,tax:false},u,150);return [r.maxBid,r.breakeven,r.profit];})))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), py)


class MemoAndCondition(unittest.TestCase):
    """手で直した相場（相場メモ）が過去データより優先されること（Python と JS で同じ）と、状態の読み取り."""

    REFS = [("Olympus Lens 35mm f/2", "頒布会リスト(A 美品)", 229.99),
            ("Olympus 35mm F2", "相場メモ", 139.99),
            ("Nikon FM2 Black Near Mint", "自分の販売実績", 265.0)]
    TITLES = ["オリンパス OM ZUIKO 35mm F2 美品", "オリンパス ZUIKO 35mm F2.8", "ニコン Nikon FM2"]

    def test_python_prefers_memo(self):
        sales = [{"title": t, "source": s, "_price": p, "_tokens": ba._tokens(t)} for t, s, p in self.REFS]
        items = [ba.Item(url=f"u{i}", title=t, current_price_jpy=5000, domestic_shipping_jpy=800, prefilled=True)
                 for i, t in enumerate(self.TITLES)]
        ba.analyze(items, sales, CFG, 150.0, True, 0, [])
        self.assertEqual([it.expected_price_usd for it in items], [139.99, None, 265.0])

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        data["refs"] = [{"title": t, "price": p, "tier": ba.source_tier({"source": s}), "source": s,
                         "tokens": sorted(ba._tokens(t)), "acc": False} for t, s, p in self.REFS]
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const D=require({json.dumps(str(dp))});"
                    "const c=makeCore(D);const mp=D.refs.filter(c.isMemo),op=D.refs.filter(r=>!c.isMemo(r));"
                    f"console.log(JSON.stringify({json.dumps(self.TITLES, ensure_ascii=False)}.map(t=>{{"
                    "const m=c.matchPreferMemo(c.titleToQuery(t)+' '+t,mp,op);return m.length?c.median(m.map(r=>r.price)):null;})))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [139.99, None, 265.0])

    def test_min_profit_shown(self):
        """店舗アプリが出す利益の基準は計算の基準と同じ（売値・送料込み$99以下は4,000円。2026-11-21）."""
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    "console.log(JSON.stringify([88,99,150].map(u=>c.calculate({price:3000,ship:0,tax:false},u,150).minProfit)))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        low = int(CFG["judge"].get("low_price_min_profit_jpy", 4000))
        self.assertEqual(json.loads(out), [low, low, int(CFG["judge"]["min_profit_jpy"])])
        html = (bs.ROOT / "store" / "index.html").read_text(encoding="utf-8")
        self.assertIn("c.minProfit.toLocaleString()", html)   # 表示は計算の値をそのまま使う（同じ式を2か所に持たない）

    def test_condition(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        cases = {"Nikon F3 TOP MINT": "TOP MINT", "Canon AE-1 [N MINT]": "Near MINT", "Leica M6 Exc+5": "Exc+5",
                 "Pentax 67 Exc++++": "Exc++++", "Mamiya 7 MINT-": "MINT", "Olympus OM-1 For Parts": "ジャンク",
                 "Nikon FE": ""}
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    f"console.log(JSON.stringify({json.dumps(list(cases), ensure_ascii=False)}.map(t=>c.conditionOf({{title:t}}))"
                    ".concat([c.conditionOf({title:'Olympus 35mm',source:'頒布会リスト(AB 小傷)'}),c.conditionOf({title:'Olympus 35mm',source:'頒布会リスト(Near Mint 小傷あり)'}),c.conditionOf({title:'Nikon FM2 MINT',source:'頒布会リスト(Exc+5)'})])))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), list(cases.values()) + ["AB", "Near MINT", "Exc+5"])

class MemoAutoSave(unittest.TestCase):
    """拡張機能の相場メモ（ダウンロード/aurora_price_memo.csv）を data/price_memo.csv に取り込む."""

    def test_import(self):
        with tempfile.TemporaryDirectory() as d:
            dl, dest = Path(d) / "dl", Path(d) / "price_memo.csv"
            dl.mkdir()
            dest.write_text("title,sold_price_usd,source,memo\nOlympus F2 35mm,180,相場メモ,前\nLeica M6,1500,Product Research,手入力\n",
                            encoding="utf-8-sig")
            (dl / "aurora_price_memo.csv").write_text(
                '\ufefftitle,sold_price_usd,source\n"Olympus F2 35mm",139.99,相場メモ\n"Nikon ""FM2""",230,相場メモ\n', encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (1, 1))
            rows = {r["title"]: r for r in ba.read_sales(dest)}
            self.assertEqual(rows["Olympus F2 35mm"]["_price"], 139.99)
            self.assertEqual(rows['Nikon "FM2"']["source"], "相場メモ")
            self.assertEqual(rows["Leica M6"]["_price"], 1500)  # 相場メモ以外はそのまま
            self.assertEqual(ba.import_extension_memo(dl, dest), (0, 0))  # 2回目は変化なし
            self.assertEqual(ba.import_extension_memo(Path(d) / "none", dest), (0, 0))
            hist = ba.load_memo_history(Path(d) / "price_memo_history.csv")  # 上書きする前の値も残る
            self.assertEqual([e[0] for e in hist["Olympus F2 35mm"]], [180, 139.99])

    def test_history_from_extension(self):
        with tempfile.TemporaryDirectory() as d:
            dl, dest = Path(d) / "dl", Path(d) / "price_memo.csv"
            dl.mkdir()
            (dl / "aurora_price_memo.csv").write_text("title,sold_price_usd,source\nNikon FM2,230,相場メモ(MINT)\n", encoding="utf-8")
            (dl / "aurora_price_memo_history.csv").write_text(
                "title,sold_price_usd,cond,date\nNikon FM2,200,,\nNikon FM2,215,MINT,2026-10-05\nNikon FM2,230,MINT,2026-11-17\n", encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (1, 0))  # 履歴ファイルを相場メモと取り違えない
            ba.import_extension_memo(dl, dest)
            h = ba.load_memo_history(Path(d) / "price_memo_history.csv")["Nikon FM2"]
            self.assertEqual([e[0] for e in h][:3], [200, 215, 230])  # 2回取り込んでも重ならない・日付順（不明が先）
            self.assertTrue(all(e[0] == 230 for e in h[3:]))  # 取り込んだ日の記録（同じ額）

    def test_manifest_has_downloads(self):
        import build_extension as bx
        with tempfile.TemporaryDirectory() as d:
            bx.write_chrome_extension("//", Path(d))
            m = json.loads((Path(d) / "manifest.json").read_text(encoding="utf-8"))
            bg = (Path(d) / "background.js").read_text(encoding="utf-8")
        self.assertIn("downloads", m["permissions"])
        self.assertNotIn("https://api.ebay.com/*", m.get("host_permissions", []))  # キーが無ければ eBay への通信権限は付けない
        self.assertIn("const APP_ID = null;", bg)
        self.assertIn("aurora_price_memo.csv", bg)


class AdaptallFlag(unittest.TestCase):
    """タムロンの交換マウント（アダプトール）だけの出品は付属品。焦点距離があるレンズ本体は除かない（Python と JS で同じ）."""
    CASES = {"20-47 オリンパスOMマウント タムロン アダプトール マウント": True,
             "Tamron Adaptall-2 mount Canon FD": True,
             "TAMRON SP MACRO 90mm F2.8 72B OLYMPUS アダプトール": False,
             "Tamron SP 70-210mm F3.5 Adaptall-2": False}

    def test_python(self):
        for t, adapter in self.CASES.items():
            self.assertEqual("アダプター等" in ba.detect_flags(t), adapter, t)

    def test_js(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    f"console.log(JSON.stringify({json.dumps(list(self.CASES), ensure_ascii=False)}.map(t=>c.detectFlags(t).includes('アダプター等'))))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), list(self.CASES.values()))


class MemoCondition(unittest.TestCase):
    """［直す］［登録］で入れた状態（相場メモ(MINT)）が取り込まれ、表示される."""

    def test_import_keeps_condition(self):
        with tempfile.TemporaryDirectory() as d:
            dl, dest = Path(d) / "dl", Path(d) / "price_memo.csv"
            dl.mkdir()
            (dl / "aurora_price_memo.csv").write_text(
                'title,sold_price_usd,source\n"Olympus F2 35mm",139.99,"相場メモ(Near MINT)"\n"Nikon FM2",230,相場メモ\n', encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (2, 0))
            rows = {r["title"]: r for r in ba.read_sales(dest)}
            self.assertEqual(rows["Olympus F2 35mm"]["source"], "相場メモ(Near MINT)")
            self.assertTrue(ba.is_memo(rows["Olympus F2 35mm"]))  # 状態付きでも相場メモとして優先される
            (dl / "aurora_price_memo.csv").write_text('title,sold_price_usd,source\n"Nikon FM2",230,"相場メモ(MINT)"\n', encoding="utf-8")
            self.assertEqual(ba.import_extension_memo(dl, dest), (0, 1))  # 状態だけ変えても更新

    def test_js_condition(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["refs"], data["kantei"] = [], []
        with tempfile.TemporaryDirectory() as d:
            dp = Path(d) / "d.json"
            dp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            code = (f"const {{makeCore}}=require({json.dumps(str(bs.CORE_JS))});const c=makeCore(require({json.dumps(str(dp))}));"
                    "console.log(JSON.stringify(['相場メモ(Near MINT)','相場メモ(Exc+++)','相場メモ(ジャンク)','相場メモ']"
                    ".map(s=>[c.conditionOf({title:'Olympus F2 35mm',source:s}),c.isMemo({source:s})])))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [["Near MINT", True], ["Exc+++", True], ["ジャンク", True], ["", True]])


class ConditionAdjust(unittest.TestCase):
    """状態の補正（1ランク 8%。相場データが少ないときの値）: 過去データの状態とこの出品の状態の差で販売価格を動かす。Python と JS で同じ."""

    SALES = [("Olympus 35mm F2", "相場メモ(Near MINT)", 139.99),        # Near MINT = 7
             ("[MINT] Nikon FM2 Black", "Chatwork販売実績(キタムラ)", 250.0),  # MINT = 9
             ("Nikon FM2 Black Exc+++", "自分の販売実績", 210.0),             # Exc+3 = 4
             ("Canon AE-1 Program", "自分の販売実績", 120.0)]                 # 状態なし＝補正しない
    TITLES = {"オリンパス OM ZUIKO 35mm F2 美品": 139.99,           # 美品=Near MINT → 差0
              "オリンパス OM ZUIKO 35mm F2 極上": 163.28,           # 極上=MINT → +2ランク（139.99×1.08²）
              "オリンパス OM ZUIKO 35mm F2 動作品": 129.62,         # 動作品=Exc+5 → -1ランク（139.99÷1.08）
              "オリンパス OM ZUIKO 35mm F2": 139.99,               # 表記なし=Near MINT
              "オリンパス OM ZUIKO 35mm F2 MINT": 139.99,          # 英語の格付けは使わない
              "ニコン Nikon FM2 ブラック 未使用に近い": 333.24,       # TOP MINT=10: 自分の実績 Exc+3($210)×1.08⁶（Chatwork より優先）
              "ニコン Nikon FM2 ブラック ジャンク": 210.0,           # ジャンクは状態の補正をせず、割引もしない（junk_price_factor = 1.0）
              "キヤノン Canon AE-1 Program 美品": 120.0}

    def _python(self):
        sales = [{"title": t, "source": s, "_price": pr, "_tokens": ba._tokens(t)} for t, s, pr in self.SALES]
        items = [ba.Item(url=f"u{i}", title=t, current_price_jpy=5000, domestic_shipping_jpy=800, prefilled=True)
                 for i, t in enumerate(self.TITLES)]
        ba.analyze(items, sales, CFG, 150.0, True, 0, [])
        return [it.expected_price_usd for it in items]

    def test_python(self):
        self.assertEqual(self._python(), list(self.TITLES.values()))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        data["refs"] = [{"title": t, "price": pr, "tier": ba.source_tier({"source": s}), "source": s,
                         "tokens": sorted(ba._tokens(t)), "acc": False} for t, s, pr in self.SALES]
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"global.localStorage={{getItem(){{return null}},setItem(){{}}}};const m=require({json.dumps(str(js))});"
                    f"const it={json.dumps(list(self.TITLES), ensure_ascii=False)}.map((t,i)=>({{id:'x'+i,title:t,price:5000,ship:800,bids:0,end:''}}));"
                    "m.evaluate(it,150);console.log(JSON.stringify(it.map(i=>i.usd)))")
            out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), self._python())


class BidCandidates(unittest.TestCase):
    """毎朝の入札リスト（bid_report_*.csv）→ スマホの「入札」タブ."""

    def _write(self, d: Path) -> None:
        import csv as _csv
        rows = [
            {"判定": "◎", "枠": "安心", "タイトル": "Nikon FM2", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a1",
             "現在価格": "10000", "入札数": "2", "終了": "2026-11-17T21:00:00+09:00", "国内送料": "910", "想定販売価格USD": "260.0",
             "上限入札額": "18000", "利益(現在価格)": "9000", "利益率%": "25.1", "注意": "動作品", "説明文": "説明文に光学難・故障の記載なし"},
            {"判定": "×", "仮判定": "○", "タイトル": "Sigma 90mm", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a2",
             "現在価格": "2300", "仮の上限入札額": "18000", "仮の想定USD": "208", "仮のsoldURL": "S", "終了": "2026-11-16T10:00:00+09:00"},
            {"判定": "◎", "タイトル": "フード", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a3", "注意": "付属品のみ?"},
            {"判定": "×", "タイトル": "x", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a4"},
            {"判定": "○", "根拠の強さ": "確認してから", "タイトル": "Canon FD 50mm", "ヤフオクURL": "https://auctions.yahoo.co.jp/jp/auction/a5",
             "終了": "2026-11-16T09:00:00+09:00"},
        ]
        keys = sorted({k for r in rows for k in r})
        with (d / "bid_report_20261115_0703.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = _csv.DictWriter(f, keys)
            w.writeheader()
            w.writerows(rows)

    def test_pick(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(Path(d))
            b = bs.bid_candidates(Path(d))
        self.assertEqual([(i["id"], i["mark"], i["tent"], i["check"]) for i in b["items"]],
                         [("a1", "◎", False, False), ("a5", "○", False, True), ("a2", "○", True, True)])
        a1, _, a2 = b["items"]
        self.assertEqual((a1["maxBid"], a1["ship"], a1["trusted"], a1["flags"]), (18000, 910, True, []))
        self.assertEqual((a2["maxBid"], a2["usd"], a2["sold"]), (18000, 208, "S"))

    def test_old_list_not_sent(self):
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as d:
            self._write(Path(d))
            self.assertIsNone(bs.bid_candidates(Path(d), now=datetime.now() + timedelta(days=3)))
            self.assertIsNone(bs.bid_candidates(Path(d) / "none"))


if __name__ == "__main__":
    unittest.main()


class ShopList(unittest.TestCase):
    """お店タブの店リスト（store/shops.js）の形が正しいこと."""

    def test_shops_js(self):
        text = (bs.CORE_JS.parent / "shops.js").read_text(encoding="utf-8")
        data = json.loads(text.split("var SHOPS = ", 1)[1].rstrip().rstrip(";"))
        self.assertEqual([a["name"] for a in data["areas"]], ["池袋", "新宿", "吉祥寺", "横浜"])
        for a in data["areas"]:
            self.assertTrue(a["shops"])
            for sh in a["shops"]:
                for k in ("name", "q", "addr", "floor", "ll", "hours", "stock", "min", "url"):
                    self.assertIn(k, sh, sh["name"])
                lat, lng = map(float, sh["ll"].split(","))
                self.assertTrue(35.3 < lat < 35.8 and 139.4 < lng < 139.8, sh["name"])


class ConditionStepEstimate(unittest.TestCase):
    """状態1ランクの補正%を、同じ機種で状態の違う売れた記録から推定する（2026-11-18）."""

    def test_estimate(self):
        rows = []
        for i, base in enumerate([100, 200, 300, 150, 250, 400]):
            toks = {f"model{i}", "50mm"}
            rows.append({"title": "[MINT] x", "source": "自分の販売実績", "_price": base * 1.1 ** 2, "_tokens": toks})
            rows.append({"title": "[Near MINT] x", "source": "自分の販売実績", "_price": base, "_tokens": toks})
        pct, n = ba.estimate_condition_step_pct(rows, 8.0)
        self.assertEqual(n, 6)
        self.assertAlmostEqual(pct, 10.0, places=1)

    def test_fallback_and_clamp(self):
        self.assertEqual(ba.estimate_condition_step_pct([], 8.0), (8.0, 0))
        rows = []
        for i in range(5):
            toks = {f"m{i}", "x"}
            rows += [{"title": "[MINT] a", "source": "自分の販売実績", "_price": 400, "_tokens": toks},
                     {"title": "[Near MINT] a", "source": "自分の販売実績", "_price": 100, "_tokens": toks}]
        self.assertEqual(ba.estimate_condition_step_pct(rows, 8.0)[0], 15.0)  # 上限 15%


class OpticalSkip(unittest.TestCase):
    """光学難（カビ・クモリ・バルサム）ははっきり書かれていれば ×。打ち消し（〜なし）は除く. Python と JS で同じ."""

    CASES = {"Nikon Ai 50mm f1.4 カビあり": ["カビ"], "Canon FD 50mm カビ・クモリなし 美品": [],
             "Pentax 55mm クモリ：無": [], "Olympus 50mm 薄クモリ": ["クモリ"], "Minolta 58mm バルサム切れ": ["バルサム"],
             "Nikon F3 傷あり": [], "Leica 50mm 少ないカビ": ["カビ"]}

    def test_python(self):
        self.assertEqual([ba.optical_title_words(t) for t in self.CASES], list(self.CASES.values()))

    def test_judge(self):
        it = ba.Item(url="u", title="Nikon Ai 50mm f1.4 カビあり", current_price_jpy=1000, prefilled=True)
        it.calc = {"max_bid_jpy": 10000, "bid_now": 1000}
        self.assertEqual(ba.judge(it)[0], "×")
        it.title = "Nikon Ai 50mm f1.4 カビ・クモリなし"
        self.assertEqual(ba.judge(it)[0], "◎")


class SetListing(unittest.TestCase):
    """まとめ売り（2026-11-18）: 中身ごとに相場を当てて合計する. Python と JS で同じ."""

    SALES = [("Nikon F3 HP Body", "自分の販売実績", 450.0),
             ("Nikon Ai-S Nikkor 50mm f/1.4", "自分の販売実績", 160.0),
             ("Nikon Ai Nikkor 28mm f/2.8", "自分の販売実績", 120.0)]
    TITLES = ["ニコン Nikon F3 HP + Ai-S 50mm F1.4 + Ai 28mm F2.8 まとめ セット",
              "ニコン レンズ 3本 まとめ セット",                       # 中身が分からない → 要確認のまま
              "Nikon F3 HP ボディ"]                                      # セットではない

    def _python(self):
        sales = [{"title": t, "source": s, "_price": pr, "_tokens": ba._tokens(t)} for t, s, pr in self.SALES]
        items = [ba.Item(url=f"u{i}", title=t, current_price_jpy=5000, domestic_shipping_jpy=800, prefilled=True)
                 for i, t in enumerate(self.TITLES)]
        ba.analyze(items, sales, CFG, 150.0, True, 0, [])
        return items

    def test_python(self):
        items = self._python()
        self.assertEqual(len(items[0].set_parts), 3)
        extra = CFG["shipping"]["deduction_usd"] + CFG["ebay"]["per_order_fee_usd"] + ba.item_cost_jpy(CFG) / 150.0  # 梱包＋オークレボの手数料
        self.assertAlmostEqual(items[0].expected_price_usd, round(450 + 160 + 120 - 2 * extra, 2), places=2)
        self.assertNotEqual(ba.judge(items[0])[0], "要確認")
        self.assertIn("まとめ売り3点", ba.judge(items[0])[1])
        self.assertEqual(items[1].set_parts, [])
        self.assertEqual(ba.judge(items[1])[0], "要確認")
        self.assertEqual(items[2].set_parts, [])

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        data["kantei"] = []
        data["refs"] = [{"title": t, "price": pr, "tier": 0, "source": s, "tokens": sorted(ba._tokens(t)), "acc": False}
                        for t, s, pr in self.SALES]
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"global.localStorage={{getItem(){{return null}},setItem(){{}}}};const m=require({json.dumps(str(js))});"
                    f"const it={json.dumps(self.TITLES, ensure_ascii=False)}.map((t,i)=>({{id:'x'+i,title:t,price:5000,ship:800,bids:0,end:''}}));"
                    "m.evaluate(it,150);console.log(JSON.stringify(it.map(i=>[i.usd,i.mark,(i.setParts||[]).length])))")
            out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
        py = [[it.expected_price_usd, ba.judge(it)[0], len(it.set_parts)] for it in self._python()]
        self.assertEqual(out, py)


class FNumberOrBody(unittest.TestCase):
    """「F2」は F値かボディの型番か、書き方全体から判断する（2026-11-18 ユーザー指摘）. Python と JS で同じ."""

    CASES = {"Nikon F2 フォトミック ボディ": "F2", "Nikon F3 HP": "F3", "ニコン Nikon F2": "F2",
             "Nikon Ai Nikkor 35mm F2": "35mm", "Nikkor 35 F2 レンズ": "", "Canon FD 50mm F1.4": "50mm",
             "Olympus 21mm f2 Zuiko": "21mm", "Contax T2 チタン": "T2", "ニコン ニッコール 105 F2.5": ""}

    def test_python(self):
        self.assertEqual({t: ba._model_word(t) for t in self.CASES}, self.CASES)
        self.assertTrue(ba.is_f_number("F2", "Nikkor 35 F2"))
        self.assertFalse(ba.is_f_number("F2", "Nikon F2 ボディ"))
        self.assertTrue(ba.is_f_number("f/2", "Nikon F2 ボディ"))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"global.localStorage={{getItem(){{return null}},setItem(){{}}}};const m=require({json.dumps(str(js))});"
                    f"console.log(JSON.stringify({json.dumps(list(self.CASES), ensure_ascii=False)}.map((t)=>m.modelWord(t))))")
            out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, list(self.CASES.values()))


class JapaneseTitlePatterns(unittest.TestCase):
    """日本語の商品名のよくある書き方で、型の語とまとめ売りの中身を正しく取れるか（2026-11-18）. Python と JS で同じ."""

    CASES = {
        "ニコン F3 50mm F1.4 28mm F2.8 セット": ("F3", 3),          # 区切り無しの並び。F3 はボディ
        "Canon AE-1 35mm フィルムカメラ + FD 50mm F1.8 セット": ("AE-1", 2),  # 35mm はフィルムの規格
        "レンズ 50mm・28mm・135mm まとめ ニコン": ("50mm", 3),      # 「・」区切り
        "Nikon F3 35mm フィルムカメラ": ("F3", 0),
        "オリンパス ５０ミリ F1.4 + OM-1 セット": ("50mm", 2),      # 全角・ミリ
        "Pentax SP + タクマー 55mm F1.8 セット": ("SP", 2),         # 数字の無いボディの型番
        "Nikon F2 フォトミック ボディ ＋ ニッコール 50mm F2 セット": ("F2", 2),
        "Nikkor 35 F2 レンズ": ("", 0),                               # F2 は F値
        "Nikon FM2 ボディ": ("FM2", 0),
        "Nikon AI 50mm F1.4": ("50mm", 0),                            # AI は型番ではない
        "Contax RTS II": ("RTS", 0),
        "Mamiya RB67 + 127mm F3.8": ("RB67", 2),
        "1円～/Nikon/ニコン/D5100/ボディ/N031": ("D5100", 0),         # ストアの管理番号は使わない
    }

    def test_python(self):
        got = {t: (ba._model_word(t), len(ba.set_part_titles(t))) for t in self.CASES}
        self.assertEqual(got, self.CASES)
        self.assertNotIn("35mm", " ".join(ba.set_part_titles("Canon AE-1 35mm フィルムカメラ + FD 50mm F1.8 セット")))

    def test_js_same(self):
        if not shutil.which("node"):
            self.skipTest("node がありません")
        import build_extension as bx
        data = bx.build_data(CFG, 150.0)
        tpl = (Path(ba.__file__).parent / "extension_template.js").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            js = Path(d) / "u.js"
            js.write_text(tpl.replace("/*__DATA__*/ null", json.dumps(data, ensure_ascii=False)), encoding="utf-8")
            code = (f"global.localStorage={{getItem(){{return null}},setItem(){{}}}};const m=require({json.dumps(str(js))});"
                    f"console.log(JSON.stringify({json.dumps(list(self.CASES), ensure_ascii=False)}.map((t)=>[m.modelWord(t),m.setPartTitles(t)])))")
            out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
        self.assertEqual(out, [[ba._model_word(t), ba.set_part_titles(t)] for t in self.CASES])


class RecsSoldHistory(unittest.TestCase):
    """店舗アプリのおすすめ: 頒布会も販売履歴として数える（2026-11-18）."""

    def test_hanpukai_counts(self):
        rows = [{"title": "Nikon FM2 Black", "source": "頒布会リスト(SPM)", "_price": 300.0, "purchase_jpy": ""},
                {"title": "Nikon FM2 Black", "source": "Chatwork販売実績(店)", "_price": 320.0, "purchase_jpy": "20000"}]
        recs = bs.recommendations(rows, CFG, 150.0)
        fm2 = [r for r in recs if "FM2" in r["label"]]
        self.assertTrue(fm2)
        self.assertEqual(fm2[0]["sales"], 2)            # 頒布会も販売の回数に入る
        self.assertEqual(fm2[0]["basis"], "Chatwork実績")  # 一番強い根拠の名前


class SweetNotes(unittest.TestCase):
    """スマホアプリの「狙い目」タブ: knowledge/講座/ のまとめを、暗号化して届く相場データに入れる（ページには書かない）."""

    def test_reads_notes(self):
        import build_store as bs
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "knowledge" / "講座"
            folder.mkdir(parents=True)
            (folder / "レンズ狙い目スペック.md").write_text("# 狙い目\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
            notes = bs.sweet_notes(Path(d))
        self.assertEqual([n["name"] for n in notes], ["レンズ狙い目スペック"])
        self.assertIn("狙い目", notes[0]["md"])

    def test_not_in_page(self):
        # 講座資料の中身（世代の順など）をアプリのページ（公開）に書いていないこと
        page = (Path(__file__).resolve().parent.parent / "store" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("New Nikkor", page)
        self.assertIn('data-tab="sweet"', page)
