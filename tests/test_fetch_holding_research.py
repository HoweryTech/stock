import unittest

from tools.fetch_holding_research import announcement_risk_keywords, financial_review_flags, scaled, security_code_with_exchange, security_id


class FetchHoldingResearchTest(unittest.TestCase):
    def test_security_identifiers(self) -> None:
        self.assertEqual(security_id("600028"), "1.600028")
        self.assertEqual(security_id("000725"), "0.000725")
        self.assertEqual(security_code_with_exchange("601939"), "601939.SH")
        self.assertEqual(security_code_with_exchange("002321"), "002321.SZ")

    def test_scaled_quote_values(self) -> None:
        self.assertEqual(scaled(491), 4.91)
        self.assertIsNone(scaled("-"))

    def test_announcement_risk_keywords(self) -> None:
        self.assertEqual(announcement_risk_keywords("关于收到监管警示函及诉讼进展的公告"), ["警示", "诉讼"])
        self.assertEqual(announcement_risk_keywords("年度权益分派实施公告"), [])

    def test_financial_review_flags(self) -> None:
        flags = financial_review_flags(
            {"pe_ttm": -10.0},
            {"revenue_yoy_pct": -12.0, "parent_net_profit_yoy_pct": -30.0, "roe_weighted_pct": -1.0, "debt_ratio_pct": 80.0},
        )
        self.assertEqual(
            [item["code"] for item in flags],
            ["revenue_decline", "profit_decline", "negative_roe", "high_debt_ratio", "negative_pe"],
        )


if __name__ == "__main__":
    unittest.main()
