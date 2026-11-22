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


if __name__ == "__main__":
    unittest.main()
