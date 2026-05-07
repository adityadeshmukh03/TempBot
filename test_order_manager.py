import unittest

from order_manager import OrderManager


class FakeKite:
    VARIETY_REGULAR = "regular"

    def __init__(self, history):
        self.history = history
        self.cancelled = False

    def place_order(self, **kwargs):
        return "OID123"

    def order_history(self, order_id):
        return self.history

    def cancel_order(self, **kwargs):
        self.cancelled = True


class OrderManagerTests(unittest.TestCase):
    def test_live_entry_waits_for_complete_fill(self):
        kite = FakeKite([{"status": "COMPLETE", "filled_quantity": 50, "average_price": 101.5}])
        manager = OrderManager(kite, live_mode=True)
        result = manager.place_entry("NIFTYCE", "NFO", 50)
        self.assertTrue(result.ok)
        self.assertEqual(result.average_price, 101.5)

    def test_live_entry_rejects_unfilled_order(self):
        kite = FakeKite([{"status": "REJECTED", "filled_quantity": 0, "status_message": "bad price"}])
        manager = OrderManager(kite, live_mode=True)
        result = manager.place_entry("NIFTYCE", "NFO", 50)
        self.assertFalse(result.ok)
        self.assertIn("REJECTED", result.error)

    def test_sl_order_accepts_trigger_pending(self):
        kite = FakeKite([{"status": "TRIGGER PENDING", "filled_quantity": 0}])
        manager = OrderManager(kite, live_mode=True)
        result = manager.place_stop_loss("NIFTYCE", "NFO", 50, 95)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "TRIGGER PENDING")


if __name__ == "__main__":
    unittest.main()
