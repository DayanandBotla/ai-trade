# NIFTY PRO SCALPER
Professional NIFTY 50 Options Trading System

## Quick Start (Local)
```bash
pip install httpx
python app.py
# Open: http://localhost:8000
```

## Deploy to Railway.app (FREE)
1. Go to https://railway.app → Sign up free
2. New Project → Deploy from GitHub
3. Upload these files to a GitHub repo
4. Railway auto-detects and deploys
5. Get your public URL (e.g. https://nifty-pro.up.railway.app)
6. Open URL from any device — phone, laptop, anywhere

## Deploy to Render.com (FREE)
1. Go to https://render.com → Sign up free
2. New → Web Service → Connect GitHub repo
3. Build: pip install httpx
4. Start: python app.py
5. Get public URL

## Files
- app.py          → Main trading engine
- ui.html         → Dashboard frontend
- requirements.txt → Only needs httpx
- railway.toml    → Railway config
- Procfile        → Render/Heroku config

## Strategy — 9 Filter Sure Shot System
1. 15min EMA 21/55     → Master trend direction
2. ADX > 25            → Trending market only (VETO if choppy)
3. 5min Supertrend     → Momentum confirmation
4. 5min VWAP + Bands   → Institutional price levels
5. Bollinger Bands     → Squeeze breakout detection
6. 3min MACD 12/26/9   → Entry trigger
7. 3min RSI 14         → Zone filter
8. Volume > 1.5x avg   → Institutional participation (VETO if low)
9. PCR + OI + Delta    → Options market confirmation

Entry Rule: Min 6/9 agree + ADX>25 + Volume>1.5x + Confidence>=80%
Exit: Supertrend flip / Trailing SL / Target / RSI extreme / 2:15 PM

## NIFTY 50 Specs
- Lot size: 65 shares
- Expiry: Every Tuesday
- Holiday: Shifts to previous trading day
- 1 trade per day maximum
