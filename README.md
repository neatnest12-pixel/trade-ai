# Navora Quant AI — V1

Navora Quant AI is a research-only, probabilistic EURUSD platform for completed-candle analysis,
next-candle forecasts, backtests and paper trades. It does not place live orders and makes no
profit or accuracy guarantees. The default feed is deterministic synthetic mock data and is
prominently labelled as such.

## Architecture

- `backend/app/market_data`: validated CSV, mock and read-only MT5 adapters; UTC normalization and gap detection.
- `backend/app/analysis`: raw indicators, deterministic structure, price-action, liquidity/SMC approximations, features and regimes.
- `backend/app/strategies`: nine independent standardized strategy agents.
- `backend/app/ml`: interpretable classifier/regressor registry, chronological splits, calibration and walk-forward evaluation.
- `backend/app/services`: prediction consensus/no-trade logic, event-based backtesting, risk and performance metrics.
- `backend/app/db`: SQLAlchemy candles, immutable prediction records, evaluations, backtests and paper trades.
- `frontend`: Next.js dark terminal with TradingView Lightweight Charts and an honest empty-state analytics page.

## Reproducible environments

Core tests and lint use Python 3.12 without the optional MT5 package:

```powershell
py -3.12 -m venv .coreenv
.\.coreenv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:MARKET_DATA_SOURCE="mock"; $env:PYTHONPATH="backend"; python -m pytest backend/tests -q
$env:PYTHONPATH="backend"; ruff check backend
```

The Windows MT5 connector uses the separate Python 3.12 environment:

```powershell
py -3.12 -m venv .mt5env2
.\.mt5env2\Scripts\Activate.ps1
python -m pip install -r requirements-mt5.txt
$env:MT5_READ_ONLY="true"; $env:TRADING_EXECUTION_ENABLED="false"
```

Start the API with an explicitly selected adapter:

```powershell
$env:PYTHONPATH="backend"; $env:MARKET_DATA_SOURCE="mock"; uvicorn app.main:app --reload
```

## Local startup

Python 3.12+ and Node.js 22+ are recommended.

```powershell
Copy-Item .env.example .env
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`; API documentation is at `http://localhost:8000/docs`.

Docker alternative: `docker compose up --build` from the repository root.

## Environment

Copy `.env.example`. `DATABASE_URL` should use PostgreSQL in shared deployments. `MARKET_DATA_SOURCE`
defaults to `mock`; MT5 credentials are optional and must remain outside source control. The MT5
adapter is read-only and intentionally contains no order API.

## Verification

```powershell
cd backend
pytest -q
ruff check .
mypy app
cd ..\frontend
npm run typecheck
npm run build
```

## Known V1 limitations

- The default heuristic probability is not claimed to be calibrated; calibration requires a sufficiently large journal.
- Higher-timeframe alignment has an explicit feature hook but production synchronization/resampling is not yet wired.
- The journal persists predictions immutably, but scheduled post-candle evaluation should be added as a worker.
- Backtesting supports conservative bar fills and costs; session calendars, swaps and venue-specific tick models remain future work.
- PostgreSQL-ready models are present; the included migration placeholder should be replaced by reviewed Alembic revisions.
- Mock data is synthetic and results from it have no claim on live-market performance.

## Validation hardening

See `AUDIT_REPORT.md` and `VALIDATION_REPORT.md`. Production deployments should set
`AUTO_CREATE_SCHEMA=false` and run `python -m alembic upgrade head` from `backend`. For an existing
unversioned V1 database, take a backup, run `python -m alembic stamp 0001`, then upgrade. The current
machine has no legitimate MT5/CSV dataset, so empirical model, baseline, calibration and expectancy
results are deliberately withheld.

## Recommended next stage

Ingest and version a clean broker-exported EURUSD dataset, add the scheduled journal evaluator, build
real walk-forward model artifacts and calibration reports, then validate all fill/risk assumptions
against the intended venue—still in paper mode.
