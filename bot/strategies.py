"""Indicators + the 8 backtested strategies. Champion: Donchian-20 + ATR stop + RSI filter."""

import pandas as pd
import numpy as np


def sma(s, n):
    return s.rolling(n).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100 * (g > 0).astype(float))


def macd(close, fast=12, slow=26, signal=9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig


def bollinger(close, n=20, mult=2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std()
    return mid, mid + mult * sd, mid - mult * sd


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def supertrend(df, n=7, mult=3.0):
    a = atr(df, n)
    hl2 = (df["high"] + df["low"]) / 2
    up = hl2 - mult * a
    dn = hl2 + mult * a
    # proper trailing bands
    fup = up.copy()
    fdn = dn.copy()
    for i in range(1, len(df)):
        fup.iloc[i] = max(up.iloc[i], fup.iloc[i - 1]) if df["close"].iloc[i - 1] > fup.iloc[i - 1] else up.iloc[i]
        fdn.iloc[i] = min(dn.iloc[i], fdn.iloc[i - 1]) if df["close"].iloc[i - 1] < fdn.iloc[i - 1] else dn.iloc[i]
    direction = pd.Series(1, index=df.index)
    st = pd.Series(np.nan, index=df.index)
    for i in range(len(df)):
        if i == 0:
            direction.iloc[i] = 1 if df["close"].iloc[i] > fdn.iloc[i] else -1
        else:
            prev = direction.iloc[i - 1]
            if prev == 1:
                direction.iloc[i] = -1 if df["close"].iloc[i] < fup.iloc[i] else 1
            else:
                direction.iloc[i] = 1 if df["close"].iloc[i] > fdn.iloc[i] else -1
        st.iloc[i] = fup.iloc[i] if direction.iloc[i] == 1 else fdn.iloc[i]
    return st, direction


def add_indicators(df, cfg):
    df = df.copy()
    df["sma50"] = sma(df["close"], 50)
    df["sma200"] = sma(df["close"], 200)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["rsi14"] = rsi(df["close"], 14)
    m, s = macd(df["close"])
    df["macd"], df["macd_sig"] = m, s
    mid, up, lo = bollinger(df["close"])
    df["bb_mid"], df["bb_up"], df["bb_lo"] = mid, up, lo
    df["atr14"] = atr(df, 14)
    _, d = supertrend(df)
    df["st_dir"] = d
    df["don_hi20"] = df["high"].rolling(20).max().shift(1)
    df["don_lo10"] = df["low"].rolling(10).min().shift(1)
    return df


def signal(df, strategy_id="champion", cfg=None):
    """Return target position series (0/1 long-only). Causal: uses row t to trade next bar."""
    cfg = cfg or {}
    pos = pd.Series(0, index=df.index)
    if strategy_id == "buyhold":
        pos[:] = 1
    elif strategy_id == "sma50_200":
        pos = (df["sma50"] > df["sma200"]).astype(int).fillna(0)
    elif strategy_id == "ema_atr":
        atrm = float(cfg.get("atr_mult", 3.0))
        trend = (df["ema20"] > df["ema50"])
        cur, stop = 0, 0.0
        out = []
        for i in range(len(df)):
            c, a = df["close"].iloc[i], df["atr14"].iloc[i]
            if pd.isna(a) or pd.isna(df["ema20"].iloc[i]):
                out.append(0); continue
            if cur == 0 and bool(trend.iloc[i]):
                cur, stop = 1, c - atrm * a
            elif cur == 1:
                stop = max(stop, c - atrm * a)
                if c < stop or not bool(trend.iloc[i]):
                    cur = 0
            out.append(cur)
        pos = pd.Series(out, index=df.index)
    elif strategy_id == "rsi":
        cur = 0
        out = []
        for v in df["rsi14"]:
            if pd.isna(v):
                out.append(0); continue
            if cur == 0 and v < 35:
                cur = 1
            elif cur == 1 and v > 65:
                cur = 0
            out.append(cur)
        pos = pd.Series(out, index=df.index)
    elif strategy_id == "macd":
        pos = (df["macd"] > df["macd_sig"]).astype(int).fillna(0)
    elif strategy_id == "boll":
        cur = 0
        out = []
        for i in range(len(df)):
            if pd.isna(df["bb_up"].iloc[i]):
                out.append(0); continue
            c = df["close"].iloc[i]
            if cur == 0 and c > df["bb_up"].iloc[i]:
                cur = 1
            elif cur == 1 and c < df["bb_mid"].iloc[i]:
                cur = 0
            out.append(cur)
        pos = pd.Series(out, index=df.index)
    elif strategy_id == "supertrend":
        pos = (df["st_dir"] == 1).astype(int).fillna(0)
    elif strategy_id == "champion":
        atrm = float(cfg.get("atr_mult", 3.0))
        rmin = float(cfg.get("rsi_min", 45))
        cur, stop = 0, 0.0
        out = []
        for i in range(len(df)):
            c = df["close"].iloc[i]
            hi, lo = df["don_hi20"].iloc[i], df["don_lo10"].iloc[i]
            a, r = df["atr14"].iloc[i], df["rsi14"].iloc[i]
            if pd.isna(hi) or pd.isna(a) or pd.isna(r):
                out.append(cur); continue
            if cur == 0:
                if c > hi and r > rmin:
                    cur, stop = 1, c - atrm * a
            else:
                stop = max(stop, c - atrm * a)
                if c < lo or c < stop:
                    cur = 0
            out.append(cur)
        pos = pd.Series(out, index=df.index)
    else:
        raise ValueError(f"unknown strategy {strategy_id}")
    return pos.fillna(0).astype(int)


def size_qty(equity, risk_pct, atr_rs, price_rs, margin_per_lot=6000.0, max_leverage=3, max_lots=20):
    """Risk-based qty: risk 1% equity per trade on a 3xATR stop. Returns integer lots (>=1)."""
    risk_rs = equity * risk_pct / 100.0
    stop = max(atr_rs * 3.0, price_rs * 0.01)
    qty = int(risk_rs // stop)
    qty = max(1, qty)
    qty = min(qty, max(1, int(equity * max_leverage // margin_per_lot)), max_lots)
    return qty
