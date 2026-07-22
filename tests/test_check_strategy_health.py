import unittest

from tools.check_strategy_health import check_strategy_health, render_health


class CheckStrategyHealthTest(unittest.TestCase):
    def test_pauses_strategy_when_cooldown_is_triggered(self) -> None:
        analysis = {
            "by_strategy": {
                "trend_strength": {
                    "count": 3,
                    "win_count": 0,
                    "loss_count": 3,
                    "win_rate_pct": 0.0,
                    "avg_trade_return_pct": -2.0,
                    "total_portfolio_return_pct": -0.3,
                }
            }
        }
        cooldown = {
            "conclusion": "cooldown_required",
            "threshold": 3,
            "strategy_losing_streaks": {"trend_strength": 3},
        }

        result = check_strategy_health(analysis, cooldown)
        content = render_health(result)

        self.assertEqual(result["conclusion"], "pause_required")
        self.assertEqual(result["strategies"][0]["status"], "pause_new_entries")
        self.assertTrue(any(item["code"] == "strategy_cooldown_required" for item in result["strategies"][0]["actions"]))
        self.assertIn("trend_strength", content)

    def test_marks_low_performing_strategy_as_needs_review(self) -> None:
        analysis = {
            "by_strategy": {
                "value_quality": {
                    "count": 4,
                    "win_count": 1,
                    "loss_count": 3,
                    "win_rate_pct": 25.0,
                    "avg_trade_return_pct": -0.5,
                    "total_portfolio_return_pct": -0.2,
                }
            }
        }

        result = check_strategy_health(analysis, {"conclusion": "normal", "threshold": 3, "strategy_losing_streaks": {}})

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertEqual(result["strategies"][0]["status"], "needs_review")
        self.assertTrue(any(item["code"] == "low_win_rate" for item in result["strategies"][0]["actions"]))

    def test_small_sample_can_still_be_healthy(self) -> None:
        analysis = {
            "by_strategy": {
                "event_catalyst": {
                    "count": 1,
                    "win_count": 1,
                    "loss_count": 0,
                    "win_rate_pct": 100.0,
                    "avg_trade_return_pct": 3.0,
                    "total_portfolio_return_pct": 0.15,
                }
            }
        }

        result = check_strategy_health(analysis, {"conclusion": "normal", "threshold": 3, "strategy_losing_streaks": {}})

        self.assertEqual(result["conclusion"], "healthy")
        self.assertEqual(result["strategies"][0]["status"], "healthy")
        self.assertTrue(any(item["code"] == "insufficient_review_sample" for item in result["strategies"][0]["actions"]))

    def test_marks_loss_making_discipline_exception_as_needs_review(self) -> None:
        analysis = {
            "by_strategy": {
                "trend_strength": {
                    "count": 3,
                    "win_count": 2,
                    "loss_count": 1,
                    "win_rate_pct": 66.6667,
                    "avg_trade_return_pct": 0.8,
                    "total_portfolio_return_pct": 0.2,
                }
            },
            "discipline": {
                "exceptions": [
                    {
                        "review_id": "TR-EXCEPTION",
                        "strategy": "trend_strength",
                        "trade_return_pct": -2.0,
                        "portfolio_return_pct": -0.1,
                        "exception_reason": "策略暂停期小仓位例外。",
                    }
                ]
            },
        }

        result = check_strategy_health(analysis, {"conclusion": "normal", "threshold": 3, "strategy_losing_streaks": {}})
        content = render_health(result)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertEqual(result["strategies"][0]["status"], "needs_review")
        self.assertEqual(result["strategies"][0]["discipline_exception_loss_count"], 1)
        self.assertTrue(any(item["code"] == "loss_making_discipline_exception" for item in result["strategies"][0]["actions"]))
        self.assertIn("亏损纪律例外交易", content)

    def test_marks_negative_config_version_as_needs_review(self) -> None:
        analysis = {
            "by_strategy": {
                "trend_strength": {
                    "count": 3,
                    "win_count": 2,
                    "loss_count": 1,
                    "win_rate_pct": 66.6667,
                    "avg_trade_return_pct": 0.8,
                    "total_portfolio_return_pct": 0.2,
                }
            },
            "by_config_version": {
                "CONFIG-VERSION-RISK": {
                    "version_id": "CONFIG-VERSION-RISK",
                    "profile_hash": "abcdef1234567890",
                    "profile_hash_short": "abcdef123456",
                    "count": 3,
                    "win_count": 1,
                    "loss_count": 2,
                    "win_rate_pct": 33.3333,
                    "avg_trade_return_pct": -0.6,
                    "total_portfolio_return_pct": -0.2,
                }
            },
        }

        result = check_strategy_health(analysis, {"conclusion": "normal", "threshold": 3, "strategy_losing_streaks": {}})
        content = render_health(result)

        self.assertEqual(result["conclusion"], "needs_review")
        self.assertEqual(result["strategies"][0]["status"], "healthy")
        self.assertEqual(result["needs_review_config_version_count"], 1)
        self.assertEqual(result["config_versions"][0]["status"], "needs_review")
        self.assertTrue(any(item["code"] == "config_version_low_win_rate" for item in result["config_versions"][0]["actions"]))
        self.assertIn("配置版本明细", content)
        self.assertIn("CONFIG-VERSION-RISK", content)

    def test_includes_candidate_observation_without_changing_health_status(self) -> None:
        analysis = {
            "by_strategy": {
                "trend_strength": {
                    "count": 3,
                    "win_count": 2,
                    "loss_count": 1,
                    "win_rate_pct": 66.6667,
                    "avg_trade_return_pct": 0.8,
                    "total_portfolio_return_pct": 0.2,
                }
            }
        }
        candidate_performance = {
            "generated_at": "2026-07-22T10:00:00",
            "horizons": [5, 10],
            "items": [
                {
                    "code": "600000",
                    "strategies": "trend_strength|value_quality",
                    "primary_strategy": "trend_strength",
                    "horizons": {
                        "5": {"status": "complete", "return_pct": 6.0},
                        "10": {"status": "complete", "return_pct": -2.0},
                    },
                },
                {
                    "code": "000001",
                    "strategies": "trend_strength",
                    "horizons": {
                        "5": {"status": "complete", "return_pct": 2.0},
                        "10": {"status": "insufficient_future_bars", "return_pct": None},
                    },
                },
            ],
        }

        result = check_strategy_health(
            analysis,
            {"conclusion": "normal", "threshold": 3, "strategy_losing_streaks": {}},
            candidate_performance,
        )
        content = render_health(result)

        self.assertEqual(result["conclusion"], "healthy")
        observation = result["candidate_observation"]
        self.assertTrue(observation["available"])
        self.assertEqual(observation["candidate_count"], 2)
        trend = observation["by_strategy"]["trend_strength"]
        self.assertEqual(trend["candidate_count"], 2)
        self.assertEqual(trend["horizons"]["5"]["completed_count"], 2)
        self.assertEqual(trend["horizons"]["5"]["average_return_pct"], 4.0)
        self.assertEqual(trend["horizons"]["10"]["completed_count"], 1)
        self.assertIn("候选观察表现", content)
        self.assertIn("不参与真实交易健康状态判定", content)


if __name__ == "__main__":
    unittest.main()
