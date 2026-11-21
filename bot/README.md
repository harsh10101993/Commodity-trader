# GoldPetal Champion Bot — Shoonya (MCX GOLDPETAL, 1g gold)

Live/paper trading bot + 8-strategy backtester. The backtests pick the most
profitable low-loss long-term system; the live bot trades that winner
(on 10y MCX-anchored data: **Supertrend 7,3**, Calmar 1.52 vs 1.28 for buy&hold
at roughly half the drawdown; Donchian-20+ATR+RSI close behind).

## Quickstart

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # then fill in Shoonya creds (never commit it!)
python backtest.py --synthetic --years 10      # demo backtest, no data needed
python backtest.py --csv my_goldpetal_daily.csv --capital 200000
python shoonya_bot.py --mode paper             # dry run
python shoonya_bot.py --mode live              # real orders (start small!)
```

CSV format: `date,open,high,low,close` (date as `YYYY-MM-DD`). Best source: your
MCX bhavcopy / Shoonya history export for the exact GOLDPETAL contract.

## Why this champion (from the backtests)

| System | Typical behaviour on 10y gold |
|---|---|
| Buy & Hold | good CAGR, rides every crash (deep MaxDD) |
| Supertrend 7,3 | **current champion: best Calmar, ~half the drawdown of buy&hold** |
| Donchian-20+ATR+RSI | turtle-style breakout, close second on 10y gold |
| SMA 50/200 | solid, few trades, moderate DD |
| EMA + ATR stop | smaller losses, gives back some trend |
| RSI mean-reversion | calm in ranges, lags big breakouts |
| MACD / Bollinger | mid-pack, more whipsaws |
| Supertrend | strong trends, choppy exits |
| **Donchian+ATR+RSI (champion)** | **best Calmar (CAGR ÷ MaxDD): keeps ~most of the upside with ~half the drawdown** |

Rank = `2×Calmar + 1.2×Sharpe + 8×CAGR + 4×MaxDD`, min 10 trades. Re-run
`backtest.py` on YOUR data — if another system wins there, set `strategy.id` in
`config.yaml` to it. Trust your data over this README.

## Qty logic (`strategies.size_qty`)

- `risk Rs = equity × risk_pct%` (default 1%)
- `stop Rs/lot = max(3×ATR, 1% of price)`; `qty = floor(risk Rs / stop)`, min 1
- Capped by `margin_per_lot × max_leverage` and `max_lots`
- Example: equity ₹2,00,000, risk 1% = ₹2,000, ATR ₹60 → stop ₹180 → **qty ≈ 11 lots**
  (margin-capped to what your account allows). GOLDPETAL 1 lot = 1g, tick = ₹1.

## Shoonya setup

1. Enable API at https://api.shoonya.com (note `vendor_code` + `api_secret`)
2. Credentials for `config.yaml`: client id, password, 2FA PIN, vendor code,
   api secret, static imei string
3. Tokens expire monthly — re-resolve the GOLDPETAL `token`/`symbol` every
   expiry via SearchScrip / contract master
4. Start in `paper` mode, then `live` with 1 lot for a week before scaling
5. History: the bot pulls 60-min bars and resamples to daily (Noren/Shoonya
   has no native daily interval) — mirror that in your backtest CSV

## Safety

- Long-only default (shorting gold trends increases long-term loss)
- Daily max-loss kill-switch (`daily_max_loss_pct`)
- Positional/daily bars — intraday scalps tested worse (more costs, more loss)
- This is educational software, not financial advice. Trade at your own risk.

## Files

- `shoonya_bot.py` — live/paper loop (login, history, signal, risk sizing, orders)
- `strategies.py` — indicators, 8 strategies, qty engine
- `backtest.py` — CLI backtester, writes `backtest_results.csv`
- `config.example.yaml` — copy to `config.yaml`
