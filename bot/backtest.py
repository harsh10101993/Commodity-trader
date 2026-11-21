"""Backtest all 8 strategies on a daily OHLC CSV. Next-bar-open execution, costs included.

Usage:
  python backtest.py --csv goldpetal_daily.csv --capital 200000 --leverage 1
  python backtest.py --synthetic --years 10   # quick demo without data

CSV format: date,open,high,low,close  (date YYYY-MM-DD)
"""

import argparse
import numpy as np
import pandas as pd

from strategies import add_indicators, signal

STRATS = ["buyhold", "sma50_200", "ema_atr", "rsi", "macd", "boll", "supertrend", "champion"]
NAMES = {
    "buyhold": "Buy & Hold", "sma50_200": "SMA 50/200", "ema_atr": "EMA 20/50 + ATR stop",
    "rsi": "RSI 35/65", "macd": "MACD", "boll": "Bollinger breakout",
    "supertrend": "Supertrend 7,3", "champion": "CHAMPION Donchian20+ATR+RSI",
}


def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def synthetic(years=10, seed=7):
    rng = np.random.default_rng(seed)
    n = int(years * 252)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    # anchored drift: ~2,600 -> ~12,500 over the span
    drift = np.linspace(np.log(2600), np.log(12500), n)
    rets = np.diff(drift, prepend=drift[0]) + 0.00035 + rng.normal(0, 0.011, n)
    # vol clustering
    shock = (rng.random(n) < 0.02) * rng.uniform(1.5, 3.5, n)
    vol = 1 + pd.Series(shock).ewm(alpha=0.06).mean().fillna(0).values
    rets = rets * vol
    close = np.exp(np.log(2600) + np.cumsum(rets))
    close *= 12500 / close[-1]  # pin end to today
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.0015, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": close})


def backtest(df, target, capital=200000.0, leverage=1.0, txn_pct=0.0005, brokerage=0.0, slip_bps=5.0):
    opens = df["open"].values
    closes = df["close"].values
    n = len(df)
    eq = capital
    curve = np.zeros(n)
    cur = 0
    trades = []
    entry_px = 0.0
    entry_i = 0
    for i in range(n):
        desired = int(target.iloc[i - 1]) if i > 0 else 0
        if desired != cur:
            px = opens[i] * (1 + np.sign(desired - cur) * slip_bps / 10000)
            turnover = abs(desired - cur) * eq * leverage
            cost = brokerage + turnover * txn_pct
            eq -= cost
            if cur != 0:
                pnl = (cur * (px - entry_px) / entry_px) * leverage
                trades.append((entry_i, i, cur, entry_px, px, pnl))
            if desired != 0:
                entry_px, entry_i = px, i
            cur = desired
        if i > 0:
            r = (closes[i] - closes[i - 1]) / closes[i - 1]
            eq *= 1 + cur * r * leverage
        curve[i] = eq
    if cur != 0:
        pnl = cur * (closes[-1] - entry_px) / entry_px * leverage
        trades.append((entry_i, n - 1, cur, entry_px, closes[-1], pnl))
    # metrics
    years = max((df["date"].iloc[-1] - df["date"].iloc[0]).days / 365.25, 1e-9)
    cagr = (eq / capital) ** (1 / years) - 1
    rets = np.diff(curve) / np.maximum(curve[:-1], 1e-9)
    sharpe = (rets.mean() - 0.06 / 252) / (rets.std() + 1e-12) * np.sqrt(252) if len(rets) > 2 else 0.0
    roll = np.maximum.accumulate(curve)
    dd = (curve - roll) / np.maximum(roll, 1e-9)
    maxdd = dd.min()
    wins = [t for t in trades if t[5] > 0]
    wr = len(wins) / len(trades) if trades else 0.0
    gw = sum(t[5] for t in trades if t[5] > 0)
    gl = abs(sum(t[5] for t in trades if t[5] <= 0))
    pf = gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0)
    calmar = cagr / abs(maxdd) if maxdd < 0 else (99.0 if cagr > 0 else 0.0)
    return {"equity": eq, "curve": curve, "cagr": cagr, "sharpe": sharpe, "maxdd": maxdd,
            "trades": len(trades), "winrate": wr, "pf": pf, "calmar": calmar,
            "total": eq / capital - 1, "dd": dd}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--capital", type=float, default=200000)
    ap.add_argument("--leverage", type=float, default=1.0)
    args = ap.parse_args()

    df = synthetic(args.years) if (args.synthetic or not args.csv) else load_csv(args.csv)
    dfi = add_indicators(df, {})
    rows = []
    for sid in STRATS:
        tgt = signal(dfi, sid)
        r = backtest(dfi, tgt, capital=args.capital, leverage=args.leverage)
        rows.append({"strategy": NAMES[sid], "id": sid, **{k: r[k] for k in ("total", "cagr", "sharpe", "maxdd", "calmar", "trades", "winrate", "pf", "equity")}})
    res = pd.DataFrame(rows)
    res["score"] = res["calmar"].clip(-2, 5) * 2 + res["sharpe"] * 1.2 + res["cagr"] * 8 + res["maxdd"] * 4
    res = res.sort_values("score", ascending=False)
    pd.set_option("display.float_format", lambda x: f"{x:.4f}")
    print(res.to_string(index=False))
    print(f"\nCHAMPION => {res.iloc[0]['strategy']}  (id={res.iloc[0]['id']})  "
          f"CAGR={res.iloc[0]['cagr']:.2%} MaxDD={res.iloc[0]['maxdd']:.2%} Sharpe={res.iloc[0]['sharpe']:.2f}")
    res.to_csv("backtest_results.csv", index=False)
    print("Saved backtest_results.csv")


if __name__ == "__main__":
    main()
