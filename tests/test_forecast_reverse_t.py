import unittest

from tools.forecast_reverse_t import build_samples, feature_vector, quantile


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
        self.assertIn("pullback_pct", samples[0])


if __name__ == "__main__":
    unittest.main()
