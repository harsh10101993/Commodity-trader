"""GOLDPETAL live/paper trading bot on Shoonya (Noren API).

Trades the backtest winner (10y MCX-anchored data: Supertrend 7,3;
re-run backtest.py on your own data and set strategy.id accordingly).
Long-only, positional (daily bars), 1% risk per trade.

Setup:
  pip install -r requirements.txt
  cp config.example.yaml config.yaml   # fill credentials
  python shoonya_bot.py --config config.yaml --mode paper     # dry run (default)
  python shoonya_bot.py --config config.yaml --mode live      # real orders

Shoonya docs: https://api.shoonya.com | NorenRestApiPy (Finvasia/Shoonya).
MCX GOLDPETAL: 1 gram gold, 1 lot = 1 unit, tick Rs 1. Re-resolve the monthly
token/symbol every expiry via SearchScrip — tokens in the example config expire.
"""

import argparse
import hashlib
import time
from datetime import datetime, timezone

import pandas as pd
import yaml

from strategies import add_indicators, signal, size_qty

try:
    from NorenRestApiPy.NorenApi import NorenApi
except ImportError:  # paper-only mode still works for backtest/sizing review
    NorenApi = None


class ShoonyaBot:
    def __init__(self, cfg):
        self.cfg = cfg
        self.api = None
        self.day_pnl = 0.0
        self.day_trades = 0

    # ---------- connection ----------
    def login(self):
        if NorenApi is None:
            raise RuntimeError("NorenRestApiPy not installed. pip install -r requirements.txt")
        s = self.cfg["shoonya"]
        self.api = NorenApi(
            host="https://api.shoonya.com/NorenWClientTP/",
            websocket="wss://api.shoonya.com/NorenWSTP/",
        )
        pwd_sha = hashlib.sha256(s["password"].encode()).hexdigest()
        pin_sha = hashlib.sha256(s["pin_2fa"].encode()).hexdigest()
        # app-source key = sha256(userid + api_secret)
        app_key = hashlib.sha256((s["userid"] + "|" + s["api_secret"]).encode()).hexdigest()
        imei = s.get("imei", "abc1234")
        ret = self.api.login(
            userid=s["userid"], password=pwd_sha, twoFA=pin_sha,
            vendor_code=s["vendor_code"], api_secret=app_key, imei=imei,
        )
        if ret and ret.get("stat") == "Ok":
            print(f"[{now()}] Shoonya login OK: {s['userid']}")
            return True
        print(f"[{now()}] LOGIN FAILED: {ret}")
        return False

    # ---------- data ----------
    def history_daily(self, days=320):
        """Daily bars resampled from 60-min Shoonya history (Noren has no native
        daily interval, so we pull intraday and resample — standard practice)."""
        ins = self.cfg["instrument"]
        st = self.cfg["strategy"]
        to_ = datetime.now(timezone.utc)
        from_ = to_ - pd.Timedelta(days=int(days * 1.6))
        resp = self.api.get_time_price_series(
            exchange=ins["exchange"], token=ins["token"],
            starttime=int(from_.timestamp()), endtime=int(to_.timestamp()),
            interval="60",
        )
        rows = []
        for r in resp if isinstance(resp, list) else []:
            try:
                rows.append({
                    "date": pd.to_datetime(r.get("time") or r.get("ssboe")),
                    "open": float(r.get("into", r.get("o", 0))),
                    "high": float(r.get("inth", r.get("h", 0))),
                    "low": float(r.get("intl", r.get("l", 0))),
                    "close": float(r.get("intc", r.get("c", 0))),
                })
            except (TypeError, ValueError):
                continue
        raw = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
        if raw.empty:
            return raw
        if st.get("timeframe", "day") != "day":
            return raw  # intraday mode: trade the 60-min bars directly
        daily = (raw.set_index("date")
                    .resample("1D")
                    .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                    .dropna()
                    .reset_index())
        return daily[daily["close"] > 0].reset_index(drop=True)

    def quote(self):
        ins = self.cfg["instrument"]
        q = self.api.get_quotes(exchange=ins["exchange"], token=ins["token"])
        ltp = float(q.get("lp", 0) or 0)
        return ltp, q

    # ---------- decision ----------
    def decide(self, df):
        st = self.cfg["strategy"]
        dfi = add_indicators(df, st)
        tgt = signal(dfi, st.get("id", "champion"), st)
        last_pos = int(tgt.iloc[-2]) if len(tgt) > 1 else 0  # last CLOSED bar
        price = float(df["close"].iloc[-1])
        atrv = float(dfi["atr14"].iloc[-1])
        rsi = float(dfi["rsi14"].iloc[-1])
        return last_pos, price, atrv, rsi

    def qty(self, price, atrv):
        r = self.cfg["risk"]
        return size_qty(
            equity=float(r["equity"]), risk_pct=float(r.get("risk_pct", 1.0)),
            atr_rs=atrv, price_rs=price,
            margin_per_lot=float(r.get("margin_per_lot", 6000.0)),
            max_leverage=float(r.get("max_leverage", 3)),
            max_lots=int(r.get("max_lots", 20)),
        )

    # ---------- execution ----------
    def current_position_qty(self):
        ins = self.cfg["instrument"]
        book = self.api.get_positions() or []
        net = 0
        for p in book if isinstance(book, list) else []:
            if str(p.get("exch", "")) == ins["exchange"] and str(p.get("token", "")) == str(ins["token"]):
                net += int(p.get("netqty", 0) or 0)
        return net

    def place(self, side, qty, price):
        """side: 1 buy, -1 sell. Returns order id or None."""
        ins = self.cfg["instrument"]
        sess = self.cfg["session"]
        o = self.api.place_order(
            buy_or_sell="B" if side > 0 else "S",
            product_type=sess.get("product", "M"),
            exchange=ins["exchange"],
            tradingsymbol=ins["symbol"],
            quantity=qty,
            discloseqty=0,
            price_type="MKT",
            price="0",
            trigger_price="0",
            retention="DAY",
            amo="NO",
            remarks="goldpetal-champion",
        )
        oid = (o or {}).get("norenordno")
        print(f"[{now()}] ORDER {('BUY' if side > 0 else 'SELL')} {qty}x {ins['symbol']} @~{price:.1f} -> {oid or o}")
        return oid

    def sync_to_target(self, target_pos, price, atrv, mode):
        """Move live position to target (0 flat / 1 long). Long-only: never short."""
        target_pos = 1 if target_pos > 0 else 0
        qty_wanted = self.qty(price, atrv) if target_pos else 0
        if mode == "paper":
            print(f"[{now()}] PAPER signal={'LONG' if target_pos else 'FLAT'} "
                  f"price={price:.1f} ATR={atrv:.1f} qty={qty_wanted}")
            return {"mode": "paper", "target": target_pos, "qty": qty_wanted}
        held = self.current_position_qty()
        want = qty_wanted if target_pos else 0
        diff = want - held
        if diff == 0:
            print(f"[{now()}] in sync (held={held}). No order.")
            return {"mode": "live", "held": held, "order": None}
        # kill-switch
        r = self.cfg["risk"]
        if self.day_pnl <= -float(r.get("daily_max_loss_pct", 3.0)) / 100 * float(r.get("equity", 0)):
            print(f"[{now()}] DAILY STOP hit. No new orders.")
            return {"mode": "live", "blocked": "daily-stop"}
        oid = self.place(1 if diff > 0 else -1, abs(diff), price)
        self.day_trades += 1
        return {"mode": "live", "held": held, "order": oid, "diff": diff}

    def run_once(self, mode):
        if mode == "live" and self.api is None:
            if not self.login():
                return {"error": "login-failed"}
        if mode == "paper" or self.api is None:
            print(f"[{now()}] paper mode: connect Shoonya creds for live quotes. "
                  f"Use backtest.py with your MCX CSV to validate first.")
            return {"mode": "paper"}
        df = self.history_daily()
        if len(df) < 60:
            return {"error": f"not enough history ({len(df)} bars)"}
        tgt, price, atrv, rsi = self.decide(df)
        print(f"[{now()}] signal={'LONG' if tgt else 'FLAT'} price={price:.1f} ATR={atrv:.1f} RSI={rsi:.1f}")
        return self.sync_to_target(tgt, price, atrv, mode)

    def loop(self, mode, poll_s=60):
        print(f"GOLDPETAL champion bot | mode={mode} | long-only | risk "
              f"{self.cfg['risk'].get('risk_pct', 1.0)}%/trade")
        while True:
            try:
                self.run_once(mode)
            except Exception as e:  # never die on a poll error
                print(f"[{now()}] loop error: {type(e).__name__}: {e}")
            time.sleep(poll_s)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mode", default=None, choices=["paper", "live"])
    ap.add_argument("--poll", type=int, default=None)
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    mode = args.mode or cfg["session"].get("mode", "paper")
    bot = ShoonyaBot(cfg)
    if mode == "live":
        bot.login()
    bot.loop(mode, poll_s=args.poll or int(cfg["session"].get("poll_seconds", 60)))


if __name__ == "__main__":
    main()
