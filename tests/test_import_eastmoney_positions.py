import csv
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.import_eastmoney_positions import import_positions, normalize_holding_rows, parse_position_text
from tools.risk_check import load_yaml


ROOT = Path(__file__).resolve().parents[1]


class ImportEastmoneyPositionsTest(unittest.TestCase):
    def test_normalizes_common_eastmoney_headers(self) -> None:
        rows = [
            {
                "证券代码": "600000",
                "证券名称": "浦发银行",
                "股票余额": "1000",
                "可用余额": "800",
                "成本价": "10.00",
                "当前价": "10.50",
                "市值": "10500",
                "盈亏比例": "5.0%",
            }
        ]

        holdings = normalize_holding_rows(rows, total_assets=50000, cash=0)

        self.assertEqual(holdings[0]["code"], "600000")
        self.assertEqual(holdings[0]["exchange"], "SSE")
        self.assertEqual(holdings[0]["shares"], 1000)
        self.assertEqual(holdings[0]["available_shares"], 800)
        self.assertEqual(holdings[0]["position_pct"], 21.0)
        self.assertEqual(holdings[0]["return_pct"], 5.0)

    def test_imports_positions_to_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_path = Path(tmp_dir) / "eastmoney.csv"
            output_dir = Path(tmp_dir) / "positions"
            metadata_output = Path(tmp_dir) / "metadata.json"
            with input_path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=["证券代码", "证券名称", "股票余额", "成本价", "当前价", "市值"])
                writer.writeheader()
                writer.writerow({"证券代码": "300750", "证券名称": "宁德时代", "股票余额": "100", "成本价": "200", "当前价": "220", "市值": "22000"})

            metadata = import_positions(
                Namespace(
                    input=str(input_path),
                    from_clipboard=False,
                    template=str(ROOT / "templates/position.example.yaml"),
                    output_dir=str(output_dir),
                    metadata_output=str(metadata_output),
                    overwrite=False,
                    id_prefix="POS-EASTMONEY-TEST",
                    total_assets=100000.0,
                    cash=0.0,
                    default_stop_loss_pct=8.0,
                    note="测试导入。",
                    json=False,
                )
            )

            position = load_yaml(Path(metadata["written"][0]))

        self.assertEqual(metadata["position_count"], 1)
        self.assertEqual(position["stock"]["code"], "300750")
        self.assertEqual(position["stock"]["exchange"], "SZSE")
        self.assertEqual(position["entry"]["position_pct_of_total_assets"], 22.0)
        self.assertEqual(position["risk"]["stop_loss_price"], 184.0)
        self.assertEqual(position["tracking"]["current_return_pct"], 10.0)
        self.assertEqual(position["broker_import_snapshot"]["source"], "eastmoney_export")

    def test_parses_copied_tsv_table(self) -> None:
        text = "证券代码\t证券名称\t股票余额\t成本价\t当前价\t市值\n600000\t浦发银行\t1000\t10.00\t10.50\t10500\n"

        rows = parse_position_text(text)
        holdings = normalize_holding_rows(rows, total_assets=50000, cash=0)

        self.assertEqual(holdings[0]["code"], "600000")
        self.assertEqual(holdings[0]["name"], "浦发银行")
        self.assertEqual(holdings[0]["position_pct"], 21.0)

    def test_parses_plain_copied_table_line_without_header(self) -> None:
        text = "浦发银行 600000 1000 10.00 10.50 10500\n"

        rows = parse_position_text(text)
        holdings = normalize_holding_rows(rows, total_assets=50000, cash=0)

        self.assertEqual(holdings[0]["code"], "600000")
        self.assertEqual(holdings[0]["name"], "浦发银行")
        self.assertEqual(holdings[0]["shares"], 1000)

    def test_parses_space_aligned_eastmoney_holdings_table(self) -> None:
        text = """证券代码      证券名称     持仓数量     可用数量     成本价       最新价       盈亏比 √       盈亏         最新市值        币种    交易市场
601939    建设银行        200     200     9.772     10.180      4.175      81.56      2036.00   人民币   上海A股
000709    河钢股份        500     500     2.090      1.960     -6.220      -65.00      980.00   人民币   深圳A股
"""

        rows = parse_position_text(text)
        holdings = normalize_holding_rows(rows, total_assets=20000, cash=0)

        self.assertEqual(len(holdings), 2)
        self.assertEqual(holdings[0]["code"], "601939")
        self.assertEqual(holdings[0]["shares"], 200)
        self.assertEqual(holdings[0]["available_shares"], 200)
        self.assertEqual(holdings[0]["entry_price"], 9.772)
        self.assertEqual(holdings[0]["current_price"], 10.18)
        self.assertEqual(holdings[0]["return_pct"], 4.175)
        self.assertEqual(holdings[0]["profit_loss"], 81.56)
        self.assertEqual(holdings[0]["market_value"], 2036.0)


if __name__ == "__main__":
    unittest.main()
