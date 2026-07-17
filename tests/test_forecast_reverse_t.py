import unittest

from tools.forecast_reverse_t import build_samples, feature_vector, forecast, probability_thresholds, quantile


class ForecastReverseTTest(unittest.TestCase):
    def bars(self, count=40):
        return [
            {
                "timestamp": f"2026-07-14 {9 + (35 + i * 5) // 60:02d}:{(35 + i * 5) % 60:02d}",
                "open": 10 + i * 0.01, "close": 10 + i * 0.01,
                "high": 10.05 + i * 0.01, "low": 9.95 + i * 0.01,
                "volume": 1000 + i,
            }
            for i in range(count)
        ]

    def test_quantile(self):
        self.assertEqual(quantile([1, 2, 3], 0.5), 2)

    def test_feature_vector_contains_technical_features(self):
        features = feature_vector(self.bars(), 30)
        self.assertEqual(len(features), 9)

    def test_samples_have_forward_targets(self):
        samples = build_samples(self.bars(), horizon_bars=6)
        self.assertGreater(len(samples), 0)
        self.assertIn("max_up_pct", samples[0])
        self.assertIn("max_down_pct", samples[0])
        self.assertIn("pullback_pct", samples[0])

    def test_roundtrip_probability_is_conditional_on_reaching_zone(self):
        bars = self.bars(180)
        costs = {"commission_rate": 0.0003, "minimum_commission": 5.0, "stamp_duty_rate": 0.0005, "transfer_fee_rate": 0.00001, "minimum_net_profit": 5.0}
        result = forecast("000725", "京东方A", bars, 200, costs, neighbors=20)
        if result["status"] not in {"fee_blocked", "insufficient"}:
            self.assertGreaterEqual(result["roundtrip_probability_pct"], result["joint_roundtrip_probability_pct"])

    def test_fee_blocked_forecast_still_returns_observation_zone(self):
        bars = self.bars(180)
        costs = {"commission_rate": 0.0003, "minimum_commission": 5.0, "stamp_duty_rate": 0.0005, "transfer_fee_rate": 0.00001, "minimum_net_profit": 999.0}
        result = forecast("000725", "京东方A", bars, 200, costs, neighbors=20)
        self.assertEqual(result["status"], "fee_blocked")
        self.assertIn("predicted_sell_zone", result)
        self.assertIsNone(result["predicted_buyback_max_price"])

    def test_probability_thresholds_tighten_when_samples_are_thin(self):
        thin = probability_thresholds(150, 20)
        normal = probability_thresholds(300, 60)
        rich = probability_thresholds(600, 90)

        self.assertGreater(thin["minimum_reach_probability_pct"], normal["minimum_reach_probability_pct"])
        self.assertLess(rich["minimum_reach_probability_pct"], normal["minimum_reach_probability_pct"])


if __name__ == "__main__":
    unittest.main()
