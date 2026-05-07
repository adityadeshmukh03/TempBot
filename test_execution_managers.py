import unittest

from sl_manager import StopLossManager
from target_manager import TargetManager
from trailing_manager import TrailingStopManager


class ExecutionManagerTests(unittest.TestCase):
    def test_stop_loss_triggers_below_level(self):
        manager = StopLossManager()
        manager.register("NIFTYCE", 95, 50)
        self.assertTrue(manager.should_exit("NIFTYCE", 94.5))
        self.assertFalse(manager.should_exit("NIFTYCE", 96))

    def test_target_manager_partial_then_final(self):
        manager = TargetManager()
        manager.register("NIFTYCE", 110, 120, 100, 0.5)
        self.assertEqual(manager.exit_quantity("NIFTYCE", 111), (50, "TARGET_1"))
        self.assertEqual(manager.exit_quantity("NIFTYCE", 121), (50, "TARGET_2"))

    def test_trailing_stop_only_moves_up(self):
        manager = TrailingStopManager(enabled=True, trail_after_r=1, trail_by_r=0.5)
        manager.register("NIFTYCE", 100, 90)
        self.assertEqual(manager.update("NIFTYCE", 105), 90)
        self.assertEqual(manager.update("NIFTYCE", 112), 107.0)
        self.assertEqual(manager.update("NIFTYCE", 106), 107.0)


if __name__ == "__main__":
    unittest.main()
