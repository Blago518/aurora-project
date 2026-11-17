"""ebay_api.py の返品・キャンセルの判定（eBay には接続しない）."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebay_api as ea


class ReturnsTest(unittest.TestCase):
    def test_return_delivered_back(self):
        r = ea.return_row({"returnId": 1, "orderId": "11-1", "state": "CLOSED",
                           "sellerTotalRefund": {"actualRefundAmount": {"value": 100.0, "currency": "USD"}}},
                          {"returnShipmentInfo": {"shipmentTracking": {"deliveryStatus": "DELIVERED"}}})
        self.assertEqual(r["verdict"], "返送あり・到着")
        self.assertEqual((r["refund"], r["refund_currency"]), (100.0, "USD"))

    def test_refund_without_return_shipment(self):
        r = ea.return_row({"returnId": 2, "orderId": "11-2", "state": "CLOSED",
                           "sellerTotalRefund": {"actualRefundAmount": {"value": 50, "currency": "AUD"}}}, {})
        self.assertEqual(r["verdict"], "返金のみ（返送の記録なし）")

    def test_open_return(self):
        self.assertEqual(ea.return_row({"returnId": 3, "state": "RETURN_REQUESTED"})["verdict"], "手続き中")

    def test_join_adds_sku_and_plain_refunds(self):
        orders = [{"order_id": "11-1", "sku": "X1", "title": "lens", "date": "2026-01-09", "payment_status": "FULLY_REFUNDED"},
                  {"order_id": "11-3", "sku": "X2", "title": "lens2", "date": "2026-01-02", "payment_status": "FULLY_REFUNDED"},
                  {"order_id": "11-4", "sku": "X3", "title": "lens3", "date": "2026-01-15", "payment_status": "PARTIALLY_REFUNDED"},
                  {"order_id": "11-5", "sku": "X4", "title": "ok", "date": "2026-01-01", "payment_status": "PAID"}]
        rows = ea.join_returns([ea.return_row({"returnId": 1, "orderId": "11-1", "state": "CLOSED"}),
                                ea.cancel_row({"cancelId": 9, "legacyOrderId": "11-9", "cancelState": "CLOSED"})], orders)
        by = {r["order_id"]: r for r in rows}
        self.assertEqual(by["11-1"]["sku"], "X1")
        self.assertIn("全額返金", by["11-3"]["verdict"])
        self.assertIn("一部返金", by["11-4"]["verdict"])
        self.assertNotIn("11-5", by)
        self.assertIn("在庫のまま", by["11-9"]["verdict"])

    def test_search_splits_windows_and_dedupes(self):
        calls = []

        def fake(path, params=None):
            calls.append(params)
            return {"members": [{"returnId": 1}, {"returnId": len(calls) + 1}], "paginationOutput": {"totalPages": 1}}
        orig, ea.post_order_get = ea.post_order_get, fake
        try:
            got = ea._post_order_search("return", "members", "returnId", 200)
        finally:
            ea.post_order_get = orig
        self.assertEqual(len(calls), 3)  # 200日 → 90日ずつ3回
        self.assertEqual(sorted(m["returnId"] for m in got), [1, 2, 3, 4])

    def test_scrub_removes_personal_fields(self):
        d = ea._scrub({"buyerLoginName": "x", "summary": {"sellerLoginName": "y", "state": "CLOSED",
                                                           "shipTo": {"address": {"city": "z"}}}})
        self.assertEqual(d, {"summary": {"state": "CLOSED", "shipTo": {}}})


if __name__ == "__main__":
    unittest.main()
