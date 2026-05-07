import unittest
from datetime import datetime

from state_engine import TradingEngineState
from events import EventBus, TickEvent


class StateAndEventTests(unittest.TestCase):
    def test_state_snapshot_tracks_instrument(self):
        state = TradingEngineState()
        state.set_instrument({"instrument_token": 123, "tradingsymbol": "NIFTYCE", "direction": "CE"})
        snap = state.snapshot()
        self.assertEqual(snap.token, 123)
        self.assertEqual(snap.symbol, "NIFTYCE")

    def test_event_bus_dispatches_subscribed_event(self):
        bus = EventBus()
        seen = []
        bus.subscribe(TickEvent, lambda event: seen.append(event.payload["ltp"]))
        bus.publish(TickEvent(datetime.now(), {"ltp": 100}))
        self.assertEqual(seen, [100])


if __name__ == "__main__":
    unittest.main()
