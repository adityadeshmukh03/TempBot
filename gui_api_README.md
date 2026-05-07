# Trading Bot GUI/API

A simple local web app for controlling and watching the trading bot.

## Start

From the project root:

```powershell
python -m pip install -r requirements-gui.txt
python run_gui.py
```

Then open:

```text
http://127.0.0.1:8000
```

## Safety

- The big Start button launches paper mode by default.
- Live mode requires typing `LIVE` into the confirmation box.
- The app never shows secret key values.
- State is read from `bot_state.sqlite`, root log files, and CSV files.

## API

- `GET /api/health`
- `GET /api/status`
- `GET /api/config`
- `GET /api/logs?name=runtime&lines=120`
- `POST /api/bot/start`
- `POST /api/bot/stop`
- `GET /api/bot/process`
