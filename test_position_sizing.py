import unittest

from position_sizing import calculate_position_size


class PositionSizingTests(unittest.TestCase):
    def test_blocks_stop_above_entry(self):
        result = calculate_position_size(100, 101, capital=100000, risk_pct=1, lot_size=50, max_lots=5)
        self.assertTrue(result["blocked"])
        self.assertEqual(result["lots"], 0)

    def test_caps_lots_at_max(self):
        result = calculate_position_size(100, 99, capital=500000, risk_pct=1, lot_size=50, max_lots=3)
        self.assertFalse(result["blocked"])
        self.assertEqual(result["lots"], 3)


if __name__ == "__main__":
    unittest.main()

