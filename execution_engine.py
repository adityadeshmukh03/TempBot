from __future__ import annotations


class ExecutionEngine:
    def __init__(self, order_manager, order_sync, sl_manager, target_manager, trailing_manager,
                 state_store, position_tracker, config, logger=None):
        self.order_manager = order_manager
        self.order_sync = order_sync
        self.sl_manager = sl_manager
        self.target_manager = target_manager
        self.trailing_manager = trailing_manager
        self.state_store = state_store
        self.pt = position_tracker
        self.config = config
        self.logger = logger

    def register_restored_position(self, restored_position: dict):
        if not restored_position:
            return False
        self.pt.restore_position(restored_position)
        symbol = restored_position.get("symbol")
        if not symbol:
            return True
        units = restored_position.get("units", 0)
        self.sl_manager.register(symbol, restored_position.get("stop_loss", 0), units)
        self.target_manager.register(
            symbol,
            restored_position.get("target_1", 0),
            restored_position.get("target_2", 0),
            units,
            getattr(self.config, "PARTIAL_EXIT_PCT", 0.5),
        )
        self.trailing_manager.register(
            symbol,
            restored_position.get("entry_price", 0),
            restored_position.get("stop_loss", 0),
        )
        return True

