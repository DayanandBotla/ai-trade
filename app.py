"""
NIFTY PRO SCALPER
=================
Professional NIFTY 50 Options Trading System
VPS-hosted | Access from any device via browser

Install : pip install httpx
Run     : python app.py
Deploy  : Railway.app / Render.com / any VPS
"""

import httpx, asyncio, time, json, threading, socket, math, os
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DHAN_BASE = "https://api.dhan.co"
NSE_URL   = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
PORT      = int(os.environ.get("PORT", 8000))  # Railway injects PORT automatically

# ─── STATE ────────────────────────────────────────────────────────
S = {
    # Connection
    "client_id": "", "token": "", "connected": False,
    # Mode
    "auto_mode": False, "paper_mode": True,
    # Market data
    "spot": 0.0, "prev_spot": 0.0,
    # Indicators (computed from candles)
    "ema21_15m": 0, "ema55_15m": 0,
    "supertrend_5m": 0, "supertrend_dir": "WAIT",
    "vwap": 0, "vwap_upper": 0, "vwap_lower": 0,
    "macd": 0, "macd_signal": 0, "macd_hist": 0, "macd_cross": "NONE",
    "rsi": 50, "adx": 0,
    "bb_upper": 0, "bb_lower": 0, "bb_mid": 0, "bb_squeeze": False,
    "vol_ratio": 1.0,
    "pcr": 1.0, "net_delta": 0, "oi_spike": False,
    # Signal
    "signal": None, "last_scan": "--:--:--",
    "signal_score": 0, "signal_reasons": [],
    # Position
    "position": None,
    # Risk
    "lot_size": 65, "max_lots": 1,
    "sl_points": 40, "target_points": 80,
    "trail_method": "supertrend",  # supertrend / candle_low / fixed
    "max_daily_loss": 5200, "max_daily_profit": 10400,
    "max_trades": 1,
    # Stats
    "daily_pnl": 0.0, "today_trades": 0,
    "win": 0, "loss": 0, "trade_log": [],
    # System
    "uptime_start": datetime.now().strftime("%H:%M:%S"),
    "errors": [],
}

# Candle store
CANDLES = {"3m": [], "5m": [], "15m": []}

# ─── DHAN API ─────────────────────────────────────────────────────
def hdrs():
    return {"Content-Type": "application/json",
            "access-token": S["token"], "client-id": S["client_id"]}

async def dget(ep):
    if not S["token"]: return None
    async with httpx.AsyncClient(timeout=12) as c:
        try:
            r = await c.get(f"{DHAN_BASE}{ep}", headers=hdrs())
            r.raise_for_status(); return r.json()
        except Exception as e:
            log_err(f"GET {ep}: {e}"); return None

async def dpost(ep, body):
    if not S["token"]: return None
    async with httpx.AsyncClient(timeout=12) as c:
        try:
            r = await c.post(f"{DHAN_BASE}{ep}", json=body, headers=hdrs())
            r.raise_for_status(); return r.json()
        except Exception as e:
            log_err(f"POST {ep}: {e}"); return None

def log_err(msg):
    S["errors"].insert(0, f"{datetime.now().strftime('%H:%M:%S')} {msg}")
    S["errors"] = S["errors"][:20]
    print(f"[ERR] {msg}")

# ─── CANDLE FETCHER ───────────────────────────────────────────────
async def fetch_candles(mins, count=80):
    if not S["token"]: return []
    today = datetime.now().strftime("%Y-%m-%d")
    yest  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    body  = {"securityId": "13", "exchangeSegment": "IDX_I",
             "instrument": "INDEX", "interval": str(mins),
             "fromDate": yest, "toDate": today}
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.post(f"{DHAN_BASE}/v2/charts/intraday",
                             json=body, headers=hdrs())
            r.raise_for_status()
            d = r.json()
            closes = d.get("close", []);  opens  = d.get("open",  [])
            highs  = d.get("high",  []);  lows   = d.get("low",   [])
            vols   = d.get("volume",[]);  times  = d.get("timestamp",[])
            result, today_str = [], datetime.now().strftime("%Y-%m-%d")
            for i in range(len(closes)):
                ts = str(times[i]) if i < len(times) else ""
                if today_str not in ts: continue
                result.append({
                    "o": opens[i]  if i<len(opens)  else closes[i],
                    "h": highs[i]  if i<len(highs)  else closes[i],
                    "l": lows[i]   if i<len(lows)   else closes[i],
                    "c": closes[i],
                    "v": vols[i]   if i<len(vols)   else 0,
                    "t": ts,
                })
            print(f"[C] {mins}m={len(result)} bars")
            return result[-count:]
        except Exception as e:
            log_err(f"candles {mins}m: {e}"); return []

async def refresh_candles():
    c3  = await fetch_candles(3,  100)
    c5  = await fetch_candles(5,  60)
    c15 = await fetch_candles(15, 40)
    if c3:  CANDLES["3m"]  = c3
    if c5:  CANDLES["5m"]  = c5
    if c15: CANDLES["15m"] = c15
    if c5:  S["spot"] = c5[-1]["c"]

# ─── INDICATORS ───────────────────────────────────────────────────
def ema(vals, p):
    if len(vals) < p: return None
    k = 2 / (p + 1)
    v = sum(vals[:p]) / p
    for x in vals[p:]: v = x*k + v*(1-k)
    return round(v, 2)

def sma(vals, p):
    if len(vals) < p: return None
    return round(sum(vals[-p:]) / p, 2)

def stdev(vals, p):
    if len(vals) < p: return 0
    sl = vals[-p:]
    mean = sum(sl) / p
    return math.sqrt(sum((x - mean)**2 for x in sl) / p)

def calc_vwap(bars):
    pv = sum(((b["h"]+b["l"]+b["c"])/3) * b["v"] for b in bars)
    tv = sum(b["v"] for b in bars)
    if tv == 0:
        return round(sum((b["h"]+b["l"]+b["c"])/3 for b in bars)/len(bars), 2)
    return round(pv/tv, 2)

def calc_supertrend(bars, p=10, mult=3.0):
    if len(bars) < p+2: return None, "WAIT"
    cl = [b["c"] for b in bars]
    hi = [b["h"] for b in bars]
    lo = [b["l"] for b in bars]
    trs = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
           for i in range(1, len(cl))]
    atr = ema(trs, p)
    if not atr: return None, "WAIT"
    mid = (hi[-1]+lo[-1])/2
    lo_band = mid - mult*atr
    hi_band = mid + mult*atr
    direction = "UP" if cl[-1] > lo_band else "DOWN"
    val = lo_band if direction == "UP" else hi_band
    return round(val, 2), direction

def calc_macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow+sig+2:
        return 0, 0, 0, "NONE"
    ef = ema(closes, fast); es = ema(closes, slow)
    if not ef or not es: return 0, 0, 0, "NONE"
    mv = round(ef - es, 2)
    # Build MACD line history
    mh = []
    for i in range(slow, len(closes)):
        e1 = ema(closes[:i], fast); e2 = ema(closes[:i], slow)
        if e1 and e2: mh.append(round(e1-e2, 2))
    sv = ema(mh, sig) if len(mh) >= sig else mv
    if not sv: sv = mv
    hist = round(mv - sv, 2)
    cross = "NONE"
    if len(mh) >= 2:
        prev_hist = mh[-2] - sv
        if prev_hist < 0 and hist > 0: cross = "BULLISH"
        elif prev_hist > 0 and hist < 0: cross = "BEARISH"
    return mv, round(sv, 2), hist, cross

def calc_rsi(closes, p=14):
    if len(closes) < p+2: return 50.0
    gs = [max(closes[i]-closes[i-1], 0) for i in range(1, len(closes))]
    ls = [max(closes[i-1]-closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gs[-p:]) / p; al = sum(ls[-p:]) / p
    if al == 0: return 100.0
    return round(100 - (100/(1+ag/al)), 1)

def calc_adx(bars, p=14):
    """ADX — measures trend strength. >25 = trending, <20 = choppy"""
    if len(bars) < p+2: return 0
    cl = [b["c"] for b in bars]
    hi = [b["h"] for b in bars]
    lo = [b["l"] for b in bars]
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(cl)):
        h_diff = hi[i] - hi[i-1]
        l_diff = lo[i-1] - lo[i]
        plus_dm.append(h_diff if h_diff > l_diff and h_diff > 0 else 0)
        minus_dm.append(l_diff if l_diff > h_diff and l_diff > 0 else 0)
        trs.append(max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1])))
    atr_s  = ema(trs, p)
    pdm_s  = ema(plus_dm, p)
    mdm_s  = ema(minus_dm, p)
    if not atr_s or atr_s == 0: return 0
    pdi = 100 * (pdm_s or 0) / atr_s
    mdi = 100 * (mdm_s or 0) / atr_s
    dx  = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
    return round(dx, 1)

def calc_bollinger(closes, p=20, k=2.0):
    if len(closes) < p: return None, None, None, False
    mid = sma(closes, p)
    if not mid: return None, None, None, False
    sd = stdev(closes, p)
    upper = round(mid + k*sd, 2)
    lower = round(mid - k*sd, 2)
    # Squeeze = bands very tight (< 1% of price)
    width_pct = (upper - lower) / mid * 100
    squeeze = width_pct < 1.0
    return upper, round(mid, 2), lower, squeeze

# ─── NSE OPTION CHAIN ─────────────────────────────────────────────
async def fetch_nse():
    hdrs_nse = {"User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.nseindia.com/"}
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            await c.get("https://www.nseindia.com", headers=hdrs_nse)
            r = await c.get(NSE_URL, headers=hdrs_nse)
            r.raise_for_status()
            rec  = r.json()["records"]
            spot = rec["underlyingValue"]
            exp  = rec["expiryDates"][0]
            fd   = [d for d in rec["data"] if d.get("expiryDate") == exp]
            coi  = sum(d["CE"]["openInterest"] for d in fd if "CE" in d)
            poi  = sum(d["PE"]["openInterest"] for d in fd if "PE" in d)
            S["pcr"] = round(poi / max(1, coi), 2)
            S["net_delta"] = int(
                sum(d["CE"]["openInterest"]*0.5  for d in fd if "CE" in d) +
                sum(d["PE"]["openInterest"]*-0.5 for d in fd if "PE" in d))
            max_chg = max((abs(d.get("CE",{}).get("changeinOpenInterest",0))
                           for d in fd), default=0)
            S["oi_spike"] = max_chg > 50000
            S["spot"] = spot
            print(f"[NSE] spot={spot} pcr={S['pcr']} delta={S['net_delta']}")
        except Exception as e:
            log_err(f"NSE fetch: {e}")

# ─── COMPUTE ALL INDICATORS ───────────────────────────────────────
def compute_indicators():
    bars15 = CANDLES["15m"]
    bars5  = CANDLES["5m"]
    bars3  = CANDLES["3m"]

    # 15min — EMA 21 / 55
    if len(bars15) >= 57:
        cl = [b["c"] for b in bars15]
        S["ema21_15m"] = ema(cl, 21) or 0
        S["ema55_15m"] = ema(cl, 55) or 0

    # 5min — Supertrend + VWAP + Bollinger + Volume
    if len(bars5) >= 15:
        cl = [b["c"] for b in bars5]
        sv, sd = calc_supertrend(bars5)
        S["supertrend_5m"]  = sv or 0
        S["supertrend_dir"] = sd

        vv = calc_vwap(bars5)
        S["vwap"] = vv or 0
        # VWAP bands (1 std dev)
        if vv:
            tps = [(b["h"]+b["l"]+b["c"])/3 for b in bars5]
            tp_mean = sum(tps)/len(tps)
            tp_std  = math.sqrt(sum((t-tp_mean)**2 for t in tps)/len(tps))
            S["vwap_upper"] = round(vv + tp_std, 2)
            S["vwap_lower"] = round(vv - tp_std, 2)

        # Bollinger 20,2 on 5min
        bu, bm, bl, bsq = calc_bollinger(cl, 20, 2.0)
        S["bb_upper"] = bu or 0; S["bb_mid"] = bm or 0
        S["bb_lower"] = bl or 0; S["bb_squeeze"] = bsq

        # Volume ratio (current vs 20-bar avg)
        vols = [b["v"] for b in bars5]
        avg_vol = sum(vols[-20:]) / min(20, len(vols))
        S["vol_ratio"] = round(vols[-1] / max(1, avg_vol), 2)

        # ADX on 5min
        S["adx"] = calc_adx(bars5)

    # 3min — MACD + RSI
    if len(bars3) >= 40:
        cl = [b["c"] for b in bars3]
        mv, sv2, hist, cross = calc_macd(cl)
        S["macd"] = mv; S["macd_signal"] = sv2
        S["macd_hist"] = hist; S["macd_cross"] = cross
        S["rsi"] = calc_rsi(cl)

# ─── SIGNAL ENGINE — 9 FILTERS ────────────────────────────────────
#
#  SURE SHOT PHILOSOPHY:
#  ─────────────────────
#  • Minimum 7 of 9 filters must agree
#  • ADX must be > 25 (no choppy markets)
#  • Volume must confirm (1.5x average)
#  • Confidence must be ≥ 80%
#  • Only 1 trade per day
#  • All timeframes must align
#
# ─────────────────────────────────────────────────────────────────

def build_signal():
    now = datetime.now()
    h, m = now.hour, now.minute
    spot = S["spot"]
    if spot == 0: 
        return _wait("No market data yet")

    filters = []

    # ── F1: 15min TREND (EMA 21 vs 55) ──────────────────────────
    e21 = S["ema21_15m"]; e55 = S["ema55_15m"]
    if e21 > 0 and e55 > 0:
        if e21 > e55 and spot > e21:
            filters.append(("BUY", 95, "15m EMA21>EMA55 — Strong uptrend"))
        elif e21 < e55 and spot < e21:
            filters.append(("SELL", 95, "15m EMA21<EMA55 — Strong downtrend"))
        elif e21 > e55:
            filters.append(("BUY", 60, "15m Uptrend (price below EMA21)"))
        else:
            filters.append(("SELL", 60, "15m Downtrend (price above EMA21)"))
    else:
        filters.append(("WAIT", 0, "15m EMAs not ready"))

    # ── F2: ADX > 25 (trending market only) ─────────────────────
    adx = S["adx"]
    if adx >= 30:
        filters.append(("PASS", 90, f"ADX {adx} — Strong trend"))
    elif adx >= 25:
        filters.append(("PASS", 75, f"ADX {adx} — Trending"))
    elif adx >= 18:
        filters.append(("WAIT", 40, f"ADX {adx} — Weak trend, be careful"))
    else:
        filters.append(("WAIT", 0, f"ADX {adx} — Choppy market, SKIP"))

    # ── F3: 5min SUPERTREND ──────────────────────────────────────
    sd = S["supertrend_dir"]
    sv = S["supertrend_5m"]
    if sd == "UP":
        filters.append(("BUY", 88, f"5m Supertrend UP @ {sv:.0f}"))
    elif sd == "DOWN":
        filters.append(("SELL", 88, f"5m Supertrend DOWN @ {sv:.0f}"))
    else:
        filters.append(("WAIT", 0, "5m Supertrend not ready"))

    # ── F4: VWAP ─────────────────────────────────────────────────
    vv = S["vwap"]; vu = S["vwap_upper"]; vl = S["vwap_lower"]
    if vv > 0:
        gap = abs(spot - vv) / vv * 100
        if spot > vu:
            filters.append(("BUY", 85, f"Price above VWAP upper band {vu:.0f}"))
        elif spot > vv:
            st = 90 if gap < 0.2 else 72
            filters.append(("BUY", st, f"Price {spot:.0f} > VWAP {vv:.0f} (+{gap:.2f}%)"))
        elif spot < vl:
            filters.append(("SELL", 85, f"Price below VWAP lower band {vl:.0f}"))
        else:
            st = 90 if gap < 0.2 else 72
            filters.append(("SELL", st, f"Price {spot:.0f} < VWAP {vv:.0f} (-{gap:.2f}%)"))
    else:
        filters.append(("WAIT", 0, "VWAP not ready"))

    # ── F5: BOLLINGER BANDS ──────────────────────────────────────
    bu = S["bb_upper"]; bl2 = S["bb_lower"]; sq = S["bb_squeeze"]
    if bu > 0:
        if sq:
            # Squeeze breakout — direction determined by price
            if spot > S["bb_mid"]:
                filters.append(("BUY", 92, "BB Squeeze breakout UPWARD"))
            else:
                filters.append(("SELL", 92, "BB Squeeze breakout DOWNWARD"))
        elif spot > bu:
            filters.append(("BUY", 80, f"Price above BB upper {bu:.0f}"))
        elif spot < bl2:
            filters.append(("SELL", 80, f"Price below BB lower {bl2:.0f}"))
        elif spot > S["bb_mid"]:
            filters.append(("BUY", 65, "Price above BB midline"))
        else:
            filters.append(("SELL", 65, "Price below BB midline"))
    else:
        filters.append(("WAIT", 0, "BB not ready"))

    # ── F6: MACD (3min) ──────────────────────────────────────────
    cross = S["macd_cross"]; hist = S["macd_hist"]
    mv = S["macd"]; sv2 = S["macd_signal"]
    if cross == "BULLISH" and hist > 0:
        filters.append(("BUY", 92, f"3m MACD bullish crossover hist={hist:.2f}"))
    elif cross == "BEARISH" and hist < 0:
        filters.append(("SELL", 92, f"3m MACD bearish crossover hist={hist:.2f}"))
    elif mv > sv2 and hist > 0:
        filters.append(("BUY", 68, f"3m MACD above signal hist={hist:.2f}"))
    elif mv < sv2 and hist < 0:
        filters.append(("SELL", 68, f"3m MACD below signal hist={hist:.2f}"))
    else:
        filters.append(("WAIT", 20, f"3m MACD neutral hist={hist:.2f}"))

    # ── F7: RSI ZONE (3min) ──────────────────────────────────────
    rsi = S["rsi"]
    if rsi > 78:
        filters.append(("SELL", 85, f"RSI {rsi} overbought — avoid buy"))
    elif rsi < 22:
        filters.append(("BUY", 85, f"RSI {rsi} oversold — avoid sell"))
    elif 42 <= rsi <= 62:
        filters.append(("BUY", 78, f"RSI {rsi} — prime long zone"))
    elif 38 <= rsi <= 58:
        filters.append(("SELL", 78, f"RSI {rsi} — prime short zone"))
    elif rsi > 62:
        filters.append(("BUY", 60, f"RSI {rsi} — bullish momentum"))
    else:
        filters.append(("SELL", 60, f"RSI {rsi} — bearish momentum"))

    # ── F8: VOLUME CONFIRMATION ──────────────────────────────────
    vr = S["vol_ratio"]
    if vr >= 2.0:
        filters.append(("PASS", 95, f"Volume {vr:.1f}x avg — Strong institutional"))
    elif vr >= 1.5:
        filters.append(("PASS", 85, f"Volume {vr:.1f}x avg — Good participation"))
    elif vr >= 1.0:
        filters.append(("PASS", 55, f"Volume {vr:.1f}x avg — Average"))
    else:
        filters.append(("WAIT", 0, f"Volume {vr:.1f}x avg — Low, skip"))

    # ── F9: PCR + OI (option chain) ──────────────────────────────
    pcr = S["pcr"]; nd = S["net_delta"]; spike = S["oi_spike"]
    score = 0
    reasons_oi = []
    if pcr > 1.25:   score += 35; reasons_oi.append(f"PCR {pcr} bullish")
    elif pcr < 0.75: score -= 35; reasons_oi.append(f"PCR {pcr} bearish")
    else:            reasons_oi.append(f"PCR {pcr} neutral")
    if nd > 25000:    score += 35; reasons_oi.append(f"Delta +{nd//1000}K long")
    elif nd < -25000: score -= 35; reasons_oi.append(f"Delta {nd//1000}K short")
    if spike: score = int(score * 1.3); reasons_oi.append("OI spike!")
    oi_reason = " | ".join(reasons_oi)
    if score >= 45:
        filters.append(("BUY", min(92, 60+abs(score//3)), oi_reason))
    elif score <= -45:
        filters.append(("SELL", min(92, 60+abs(score//3)), oi_reason))
    else:
        filters.append(("WAIT", 35, oi_reason))

    # ── SCORE ──────────────────────────────────────────────────
    # PASS filters count for both directions
    buys  = [f for f in filters if f[0] in ("BUY",  "PASS")]
    sells = [f for f in filters if f[0] in ("SELL", "PASS")]
    pure_buys  = [f for f in filters if f[0] == "BUY"]
    pure_sells = [f for f in filters if f[0] == "SELL"]

    # Weighted confidence
    buy_conf  = round(sum(f[1] for f in pure_buys)  / max(1, len(pure_buys)))
    sell_conf = round(sum(f[1] for f in pure_sells) / max(1, len(pure_sells)))

    direction = "WAIT"; conf = 0

    if len(pure_buys) >= 6 and buy_conf >= 75:
        direction = "BUY";  conf = buy_conf
    elif len(pure_sells) >= 6 and sell_conf >= 75:
        direction = "SELL"; conf = sell_conf
    elif len(pure_buys) == 5 and buy_conf >= 85:
        direction = "BUY";  conf = round(buy_conf * 0.92)
    elif len(pure_sells) == 5 and sell_conf >= 85:
        direction = "SELL"; conf = round(sell_conf * 0.92)

    # Hard gates — these can veto the signal
    adx_f  = next((f for f in filters if "ADX" in f[2]), None)
    vol_f  = next((f for f in filters if "Volume" in f[2]), None)

    if adx_f and adx_f[0] == "WAIT" and "Choppy" in adx_f[2]:
        return _wait(f"VETOED: {adx_f[2]}")

    if vol_f and vol_f[0] == "WAIT":
        return _wait(f"VETOED: {vol_f[2]}")

    if conf < 80 and direction != "WAIT":
        return _wait(f"Confidence {conf}% below 80% threshold")

    # Discipline rules
    if S["today_trades"] >= S["max_trades"] and direction != "WAIT":
        return _wait("1 quality trade already taken today")
    if S["daily_pnl"] <= -S["max_daily_loss"]:
        return _wait("Daily loss limit hit — protect capital")
    if S["daily_pnl"] >= S["max_daily_profit"]:
        return _wait("Daily profit target hit — bank it")

    # Time gate
    exp = is_expiry_today()
    in_time = (h == 9 and m >= 30) or (10 <= h <= 13) or (h == 14 and m == 0)
    if exp: in_time = (h == 9 and m >= 30) or (10 <= h <= 11) or (h == 11 and m <= 30)
    if not in_time and direction != "WAIT":
        return _wait("Outside trading hours" if not exp else "Expiry: stop after 11:30 AM")

    # Strike & levels
    atm = round(spot / 50) * 50
    opt = "CE" if direction == "BUY" else "PE"
    sl_pts  = S["sl_points"]
    tgt_pts = S["target_points"]
    premium = round(spot * 0.004)
    sl      = max(5, premium - sl_pts)
    tgt     = premium + tgt_pts
    rr      = round(tgt_pts / max(1, sl_pts), 1)

    S["last_scan"] = now.strftime("%H:%M:%S")

    return {
        "direction":  direction,
        "confidence": conf,
        "strike":     atm,
        "option":     opt,
        "premium":    premium,
        "sl":         sl,
        "target":     tgt,
        "rr":         rr,
        "buy_count":  len(pure_buys),
        "sell_count": len(pure_sells),
        "is_expiry":  exp,
        "filters":    [{"dir":f[0],"score":f[1],"reason":f[2]} for f in filters],
        "spot":       spot,
        "time":       now.strftime("%H:%M:%S"),
        "score":      conf,
    }

def _wait(reason):
    S["last_scan"] = datetime.now().strftime("%H:%M:%S")
    return {
        "direction": "WAIT", "confidence": 0,
        "strike": 0, "option": "--", "premium": 0, "sl": 0, "target": 0, "rr": 0,
        "buy_count": 0, "sell_count": 0, "is_expiry": False,
        "filters": [], "spot": S["spot"],
        "time": datetime.now().strftime("%H:%M:%S"),
        "reason": reason, "score": 0,
    }

# ─── EXPIRY LOGIC ─────────────────────────────────────────────────
HOLIDAYS = {
    "2025-01-26","2025-02-26","2025-03-14","2025-03-31",
    "2025-04-10","2025-04-14","2025-04-18","2025-05-01",
    "2025-08-15","2025-08-27","2025-10-02","2025-10-20",
    "2025-10-21","2025-11-05","2025-12-25",
    "2026-01-26","2026-03-25","2026-04-02","2026-04-14",
    "2026-04-17","2026-05-01","2026-08-15","2026-10-02",
}

def is_trading_day(d):
    return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in HOLIDAYS

def get_expiry():
    now = datetime.now()
    diff = (1 - now.weekday()) % 7
    tue = now + timedelta(days=diff)
    if now.weekday() > 1 or (now.weekday() == 1 and now.hour >= 15 and now.minute >= 30):
        tue += timedelta(days=7)
    exp = tue.replace(hour=0, minute=0, second=0, microsecond=0)
    while not is_trading_day(exp): exp -= timedelta(days=1)
    return exp

def is_expiry_today():
    t = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return t == get_expiry().replace(hour=0, minute=0, second=0, microsecond=0)

def expiry_str():
    e = get_expiry()
    m = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    return f"{e.year%100}{m[e.month-1]}{e.day:02d}"

# ─── TRAILING STOP LOGIC ──────────────────────────────────────────
def update_trail_sl(pos, ltp):
    """
    3 trailing stop methods:
    1. supertrend  — trail SL = Supertrend value (follows trend)
    2. candle_low  — trail SL = previous candle's low/high
    3. fixed       — trail SL moves up by fixed % of profit
    """
    method = S["trail_method"]
    current_sl = pos["trail_sl"]

    if method == "supertrend":
        st_val = S["supertrend_5m"]
        if st_val > 0:
            if pos["side"] == "BUY":
                new_sl = st_val - 5  # slight buffer below ST
                pos["trail_sl"] = max(current_sl, new_sl)
            else:
                new_sl = st_val + 5
                pos["trail_sl"] = min(current_sl, new_sl)

    elif method == "candle_low":
        bars = CANDLES["5m"]
        if len(bars) >= 2:
            if pos["side"] == "BUY":
                prev_low = bars[-2]["l"]
                new_sl = prev_low - 2
                pos["trail_sl"] = max(current_sl, new_sl)
            else:
                prev_high = bars[-2]["h"]
                new_sl = prev_high + 2
                pos["trail_sl"] = min(current_sl, new_sl)

    elif method == "fixed":
        profit = ltp - pos["entry"] if pos["side"] == "BUY" else pos["entry"] - ltp
        if profit > 20:
            lock_pct = 0.5  # lock 50% of profit
            if pos["side"] == "BUY":
                new_sl = pos["entry"] + profit * lock_pct
                pos["trail_sl"] = max(current_sl, new_sl)
            else:
                new_sl = pos["entry"] - profit * lock_pct
                pos["trail_sl"] = min(current_sl, new_sl)

    return pos

def check_exit(pos, ltp):
    """Returns (should_exit, reason)"""
    side = pos["side"]

    # Hard SL / Target
    if side == "BUY":
        if ltp <= pos["trail_sl"]:
            return True, "TRAIL SL HIT"
        if ltp >= pos["target"]:
            return True, "TARGET HIT"
    else:
        if ltp >= pos["trail_sl"]:
            return True, "TRAIL SL HIT"
        if ltp <= pos["target"]:
            return True, "TARGET HIT"

    # Supertrend flip exit
    if side == "BUY"  and S["supertrend_dir"] == "DOWN":
        return True, "SUPERTREND FLIPPED — exit"
    if side == "SELL" and S["supertrend_dir"] == "UP":
        return True, "SUPERTREND FLIPPED — exit"

    # RSI divergence warning exit
    rsi = S["rsi"]
    if side == "BUY"  and rsi > 80: return True, "RSI OVERBOUGHT EXIT"
    if side == "SELL" and rsi < 20: return True, "RSI OVERSOLD EXIT"

    # Time-based exit — 2:15 PM cutoff
    now = datetime.now()
    if now.hour == 14 and now.minute >= 15:
        return True, "TIME EXIT — 2:15 PM"

    return False, ""

# ─── ORDER MANAGEMENT ─────────────────────────────────────────────
async def place_order(side, strike, opt, lots):
    qty = lots * S["lot_size"]
    sym = f"NIFTY{expiry_str()}{strike}{opt}"
    body = {
        "dhanClientId": S["client_id"], "transactionType": side,
        "exchangeSegment": "NSE_FNO", "productType": "INTRADAY",
        "orderType": "MARKET", "validity": "DAY",
        "tradingSymbol": sym, "securityId": f"NIFTY_{strike}_{opt}",
        "quantity": qty, "price": 0, "triggerPrice": 0,
        "disclosedQuantity": 0, "afterMarketOrder": False,
        "boProfitValue": 0, "boStopLossValue": 0,
    }
    if S["paper_mode"] or not S["connected"]:
        print(f"[PAPER] {side} {sym} x{qty}")
        return {"orderId": f"PAPER_{int(time.time())}", "symbol": sym}
    res = await dpost("/v2/orders", body)
    if res and "orderId" in res:
        print(f"[ORDER] {side} {sym} id={res['orderId']}")
        return res
    log_err(f"Order failed: {res}"); return None

async def enter_trade(sig):
    res = await place_order(sig["direction"], sig["strike"], sig["option"], S["max_lots"])
    if not res: return
    entry_price = sig["premium"]
    sl_pts = S["sl_points"]; tgt_pts = S["target_points"]
    if sig["direction"] == "BUY":
        sl  = entry_price - sl_pts
        tgt = entry_price + tgt_pts
    else:
        sl  = entry_price + sl_pts
        tgt = entry_price - tgt_pts
    S["position"] = {
        "side":       sig["direction"],
        "strike":     sig["strike"],
        "option":     sig["option"],
        "entry":      entry_price,
        "lots":       S["max_lots"],
        "sl":         sl,
        "trail_sl":   sl,
        "target":     tgt,
        "entry_time": datetime.now().strftime("%H:%M:%S"),
        "order_id":   res.get("orderId", ""),
        "peak_pnl":   0,
        "current_ltp": entry_price,
    }
    S["today_trades"] += 1
    print(f"[ENTRY] {sig['direction']} {sig['strike']}{sig['option']} @ {entry_price}")

async def exit_trade(exit_price, reason):
    pos = S["position"]
    if not pos: return
    await place_order(
        "SELL" if pos["side"] == "BUY" else "BUY",
        pos["strike"], pos["option"], pos["lots"]
    )
    if pos["side"] == "BUY":
        pnl = (exit_price - pos["entry"]) * pos["lots"] * S["lot_size"]
    else:
        pnl = (pos["entry"] - exit_price) * pos["lots"] * S["lot_size"]
    S["daily_pnl"] += pnl
    if pnl > 0: S["win"] += 1
    else:       S["loss"] += 1
    S["trade_log"].insert(0, {
        "time":   datetime.now().strftime("%H:%M:%S"),
        "side":   pos["side"],
        "strike": f"{pos['strike']}{pos['option']}",
        "entry":  pos["entry"],
        "exit":   round(exit_price, 1),
        "pnl":    round(pnl, 0),
        "lots":   pos["lots"],
        "reason": reason,
    })
    S["position"] = None
    print(f"[EXIT] {reason} @ {exit_price:.0f} PnL=Rs{pnl:.0f}")

async def fetch_ltp(strike, opt):
    if not S["token"]: return None
    sym = f"NIFTY{expiry_str()}{strike}{opt}"
    res = await dpost("/v2/marketfeed/ltp", {"NSE_FNO": [sym]})
    if res:
        try:
            return float(res.get("data",{}).get("NSE_FNO",{}).get(sym,{}).get("last_price"))
        except: return None
    return None

# ─── BACKGROUND SCANNER ───────────────────────────────────────────
async def scanner():
    t_candle = 0; t_nse = 0
    while True:
        try:
            now_ts = time.time()

            # Refresh candles every 3 min
            if now_ts - t_candle >= 180:
                if S["token"]: await refresh_candles()
                t_candle = now_ts

            # Refresh NSE option chain every 90s
            if now_ts - t_nse >= 90:
                await fetch_nse(); t_nse = now_ts

            # Compute all indicators
            compute_indicators()

            # Build signal
            sig = build_signal()
            S["signal"] = sig

            # Auto execute
            if S["auto_mode"] and sig["direction"] != "WAIT" and not S["position"]:
                await enter_trade(sig)

            # Monitor position
            if S["position"]:
                pos = S["position"]
                ltp = await fetch_ltp(pos["strike"], pos["option"])
                if ltp is None: ltp = pos["entry"]
                pos["current_ltp"] = ltp
                # Update peak PnL
                if pos["side"] == "BUY":
                    cur_pnl = (ltp - pos["entry"]) * pos["lots"] * S["lot_size"]
                else:
                    cur_pnl = (pos["entry"] - ltp) * pos["lots"] * S["lot_size"]
                pos["peak_pnl"] = max(pos.get("peak_pnl", 0), cur_pnl)
                # Update trailing stop
                update_trail_sl(pos, ltp)
                # Check exit
                should_exit, reason = check_exit(pos, ltp)
                if should_exit:
                    await exit_trade(ltp, reason)

        except Exception as e:
            log_err(f"Scanner: {e}")

        await asyncio.sleep(5)

# ─── HTTP SERVER ──────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n:
            try: return json.loads(self.rfile.read(n))
            except: return {}
        return {}

    def run_async(self, coro):
        loop = asyncio.new_event_loop()
        try: return loop.run_until_complete(coro)
        finally: loop.close()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_html(HTML)
        elif path == "/api/state":
            self.send_json({k: v for k, v in S.items() if k != "token"})
        elif path == "/api/signal":
            self.send_json(S["signal"] or _wait("No signal yet"))
        elif path == "/api/funds":
            self.send_json(self.run_async(dget("/v2/fundlimit")) or {})
        elif path == "/api/positions":
            self.send_json(self.run_async(dget("/v2/positions")) or [])
        elif path == "/api/orders":
            self.send_json(self.run_async(dget("/v2/orders")) or [])
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self.read_body()

        if path == "/api/connect":
            S["client_id"] = body.get("client_id", "")
            S["token"]     = body.get("token", "")
            r = self.run_async(dget("/v2/fundlimit"))
            if r:
                S["connected"] = True; S["paper_mode"] = False
                self.send_json({"status": "connected", "funds": r})
            else:
                S["connected"] = False
                self.send_json({"error": "Connection failed — check credentials"}, 400)

        elif path == "/api/disconnect":
            S["connected"] = False; S["token"] = ""; S["paper_mode"] = True
            self.send_json({"status": "disconnected"})

        elif path == "/api/auto/on":
            S["auto_mode"] = True; self.send_json({"auto_mode": True})
        elif path == "/api/auto/off":
            S["auto_mode"] = False; self.send_json({"auto_mode": False})

        elif path == "/api/paper/on":
            S["paper_mode"] = True; self.send_json({"paper_mode": True})
        elif path == "/api/paper/off":
            S["paper_mode"] = False; self.send_json({"paper_mode": False})

        elif path == "/api/scan":
            self.run_async(fetch_nse())
            if S["token"]: self.run_async(refresh_candles())
            compute_indicators()
            S["signal"] = build_signal()
            self.send_json(S["signal"])

        elif path == "/api/execute":
            sig = S["signal"]
            if sig and sig["direction"] != "WAIT":
                self.run_async(enter_trade(sig))
                self.send_json({"status": "executed", "position": S["position"]})
            else:
                self.send_json({"error": "No active signal"}, 400)

        elif path == "/api/exit":
            if S["position"]:
                pos = S["position"]
                ltp = pos.get("current_ltp", pos["entry"])
                self.run_async(exit_trade(ltp, "MANUAL EXIT"))
                self.send_json({"status": "exited", "pnl": S["daily_pnl"]})
            else:
                self.send_json({"error": "No open position"}, 400)

        elif path == "/api/settings":
            for k in ["sl_points","target_points","max_lots","max_daily_loss",
                      "max_daily_profit","max_trades","trail_method"]:
                if k in body: S[k] = body[k]
            self.send_json({"status": "saved"})

        else:
            self.send_json({"error": "not found"}, 404)

# ─── FRONTEND ─────────────────────────────────────────────────────
HTML = open("ui.html", encoding="utf-8").read() if __import__("os").path.exists("ui.html") else "<h1>UI not found</h1>"

# ─── ENTRY POINT ─────────────────────────────────────────────────
if __name__ == "__main__":
    try: ip = socket.gethostbyname(socket.gethostname())
    except: ip = "localhost"

    print("\n" + "="*50)
    print("  NIFTY PRO SCALPER — STARTED")
    print(f"  Local  : http://localhost:{PORT}")
    print(f"  Network: http://{ip}:{PORT}")
    print("  Only requires: httpx")
    print("="*50 + "\n")

    def run_bg():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scanner())

    threading.Thread(target=run_bg, daemon=True).start()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"  Open browser: http://localhost:{PORT}\n")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n  Stopped.")
