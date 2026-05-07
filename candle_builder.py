# candle_builder.py

from datetime import datetime, timedelta
import pandas as pd

class CandleBuilder:
    def __init__(self, interval_minutes=5):
        self.interval     = interval_minutes
        self.candles      = []          # all candles (historical + live)
        self.current      = None
        self.last_bucket  = None
        self.pdh          = None        # yesterday's high
        self.pdl          = None        # yesterday's low
        self.pdc          = None        # yesterday's close
        self._last_volume = 0           # tracks last seen cumulative volume

    def get_bucket(self, timestamp):
        return timestamp.replace(
            minute=(timestamp.minute // self.interval) * self.interval,
            second=0,
            microsecond=0
        )

    def preload_historical(self, kite, instrument_token):
        """Fetch 2 days of data, extract PDH/PDL/PDC, keep only today's candles"""
        self.candles = []
        self.current = None
        self.last_bucket = None
        self._last_volume = 0

        to   = datetime.now()
        from_ = to - timedelta(days=2)

        data = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_,
            to_date=to,
            interval=f"{self.interval}minute"
        )

        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])

        today     = datetime.now().date()
        yesterday = today - timedelta(days=1)

        # Extract yesterday's key levels
        df_yesterday = df[df['date'].dt.date == yesterday]
        if not df_yesterday.empty:
            self.pdh = df_yesterday['high'].max()
            self.pdl = df_yesterday['low'].min()
            self.pdc = df_yesterday['close'].iloc[-1]
            print(f"[PDH] {self.pdh} | [PDL] {self.pdl} | [PDC] {self.pdc}")

        # Keep only today's candles
        df_today = df[df['date'].dt.date == today]
        for _, row in df_today.iterrows():
            self.candles.append({
                'time':   row['date'],
                'open':   row['open'],
                'high':   row['high'],
                'low':    row['low'],
                'close':  row['close'],
                'volume': row['volume']
            })

        if not df_today.empty:
            self._last_volume = int(df_today['volume'].fillna(0).sum())

        print(f"[PRELOADED] {len(self.candles)} candles from today")

    def process_tick(self, ltp, volume, timestamp=None):
        """
        volume here is Kite's `volume_traded` — a cumulative daily counter.
        We compute a per-tick delta by diffing against the last seen value
        so each candle accumulates only the volume traded within its window.
        """
        if timestamp is None:
            timestamp = datetime.now()

        bucket = self.get_bucket(timestamp)

        # Compute volume delta from cumulative feed
        raw_delta = volume - self._last_volume
        if raw_delta < 0:
            # Cumulative counter went backwards — most likely a WebSocket reconnect
            # or Kite resetting the feed mid-session. We silently drop this tick's
            # volume contribution (treating it as zero) to avoid negative deltas
            # corrupting the candle, and log a warning so reconnects are visible.
            print(f"[WARN] Volume counter dropped {self._last_volume} → {volume}. "
                  f"Possible reconnect — tick volume skipped.")
            vol_delta = 0
        else:
            vol_delta = raw_delta
        self._last_volume = volume

        if self.current is None or bucket != self.last_bucket:
            if self.current:
                self.candles.append(self.current.copy())
                print(f"[CANDLE CLOSED] {self.current}")

            # Reset cumulative volume tracker at candle boundary
            # (delta for this first tick is already computed above)
            self.current = {
                'time':   bucket,
                'open':   ltp,
                'high':   ltp,
                'low':    ltp,
                'close':  ltp,
                'volume': vol_delta
            }
            self.last_bucket = bucket
        else:
            self.current['high']   = max(self.current['high'], ltp)
            self.current['low']    = min(self.current['low'], ltp)
            self.current['close']  = ltp
            self.current['volume'] += vol_delta

    def get_todays_candles(self):
        """All closed candles from today — for indicators"""
        return self.candles

    def get_recent_candles(self, count=6):
        """Last N closed candles — for trigger detection"""
        return self.candles[-count:]

    def get_key_levels(self):
        """Yesterday's levels as clean dict for Gemini"""
        return {
            "PDH": self.pdh,
            "PDL": self.pdl,
            "PDC": self.pdc
        }

    def candle_count(self):
        return len(self.candles)
