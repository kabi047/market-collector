# Market Data Collector (Step 1)

Collects market data only. **It never places orders.**

| Command | What it collects | Where it goes |
|---|---|---|
| `historical` | Day candles from 2000 (or listing). 1-minute candles as far back as Zerodha has them (≈2015+). Both are kept. Indices included. Futures: continuous front-month day candles with OI, plus minute candles for live contracts | `data/{stock,index,futures}/zerodha/{day,minute,day_continuous}/<SYMBOL>.parquet` |
| `live` | Every websocket tick from 09:00 to 15:45 in FULL mode: LTP, last qty, avg price, volume, total buy/sell qty, OHLC, OI, 5-level depth (price/qty/orders), exchange and receive timestamps. Covers the ~150 stocks, indices, current and next month futures, and NIFTY/BANKNIFTY/FINNIFTY/SENSEX options ±20 strikes for 2 expiries | `data/<cat>/zerodha_live/<date>/<SYMBOL>.parquet` |
| `eod` | Today's minute candles with OI for every F&O contract recorded live (Zerodha drops expired contracts, so this is the only way to keep them). Also tops up all historical files | `data/{futures,options}/zerodha/minute/` |
| `fundamentals` | **Yahoo:** quarterly and annual income statement, balance sheet, cash flow, ratios, holders, estimates, dividends. **NSE:** announcements, quarterly and annual results, corporate actions, board meetings, shareholding, annual report PDFs | `data/others/{fundamentals,corporate,reports}/<SYMBOL>/` |
| `news` | Google News for each company, plus ET, Moneycontrol, Mint and Business Standard market feeds. Grows forever, with no duplicates | `data/others/news/` |
| `status` | Summary of everything collected so far | — |

Universe: Nifty 50 + Nifty Next 50 + Nifty Midcap 50. That is roughly the top 150 NSE companies, and every Sensex stock is in it. Index lists are in `mdc/config.py`. Add your own stocks with `EXTRA_SYMBOLS` in `.env`.

All runs are **resumable and incremental**. Re-running fetches only what is missing. Every write is atomic, so a crash never corrupts a file.

## 1. Setup on this laptop (Windows)
```
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```
1. Go to developers.kite.trade, open your app and set the **Redirect URL** to `http://127.0.0.1/`.
2. Put the API key and secret in `.env` (see `.env.example`).
3. Log in, build the universe and start the history download:
```
venv\Scripts\python collect.py login          # do this once per day
venv\Scripts\python collect.py universe
venv\Scripts\python collect.py historical     # first run takes about 1-2 hours; it resumes if stopped
venv\Scripts\python collect.py fundamentals
venv\Scripts\python collect.py news
venv\Scripts\python collect.py status
```

## 2. Live market without your laptop: free cloud server
The live recorder needs a computer that is on from 09:00 to 16:00 every trading day. Use a **free cloud VM**.

**Recommended: Oracle Cloud "Always Free".**
- It is free forever: up to 4 ARM cores, 24 GB RAM and 200 GB disk.
- Pick the **Mumbai or Hyderabad** region, close to the NSE servers.
- Sign-up needs a card for verification only. Stay on Always-Free shapes and you are not charged.

Alternatives:
- **Google Cloud e2-micro:** free, but US regions only, 1 GB RAM and 30 GB disk. That is tight.
- **AWS free tier:** free for 12 months only.
- **GitHub Actions:** not suitable. Jobs stop at 6 hours and the disk is wiped after each run.

### Steps
1. Create an Ubuntu 22.04/24.04 VM. Shape: VM.Standard.A1.Flex with 2 OCPU and 12 GB, plus a 150-200 GB boot volume. Download the SSH key.
2. From your laptop, copy the project to the server (the data folder is not needed there):
   ```
   ssh -i key.pem ubuntu@<IP> "mkdir -p ~/collector"
   scp -i key.pem -r collect.py mdc deploy requirements.txt .env.example ubuntu@<IP>:~/collector/
   ```
3. On the server (`ssh -i key.pem ubuntu@<IP>`):
   ```
   cd ~/collector && bash deploy/setup_server.sh
   nano .env        # API key/secret + ZERODHA_USER_ID, ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET
   venv/bin/python collect.py login --auto
   venv/bin/python collect.py universe
   nohup venv/bin/python collect.py historical > state/logs/historical_first.log 2>&1 &
   ```
4. Done. Cron then runs everything by itself (`deploy/crontab.txt`):
   - **08:25:** auto-login.
   - **08:45:** live recorder, which waits for 09:00 and stops at 15:45.
   - **16:00:** eod.
   - **07:00 and 19:00:** news.
   - **Saturday:** fundamentals.
   - **Monday:** universe refresh.

**Auto-login** needs the TOTP *secret text*. In Kite go to Settings → Account security → External 2FA TOTP. If you already set TOTP up without saving the text key, disable it and re-enable it. When the QR code is shown, copy the key under it into `ZERODHA_TOTP_SECRET`. Auto-login uses Kite's web login, which Zerodha does not officially support. It could break if they change it, and a Telegram alert will tell you if it does.

**Phone alerts (recommended):** create a Telegram bot with @BotFather and put the token and your chat id in `.env`. You then get a message when live starts and ends (with tick counts) and when anything fails.

**Getting the data to your laptop** (from Windows PowerShell, whenever you have internet):
```
scp -i key.pem -r ubuntu@<IP>:~/collector/data/* "E:\Claude Stocks\data\"
```
Live ticks are roughly **0.5–1 GB per trading day**. This is an estimate, so check `du -sh data` after the first day. Pull and clear old live days before the 200 GB disk fills up.

## 3. Notes and limits
- **Live feed rate:** Zerodha's websocket sends snapshots, up to about 1 per second per instrument when something changes. It is not every exchange trade. This is the fastest data Kite Connect offers. Depth is 5 levels.
- **Rate limits:** historical calls are capped at 3 per second, and the downloader stays under that. Each minute request covers at most 60 days.
- **Indices** have no volume or depth. That is how the exchange publishes them.
- **Free sources:** Yahoo and NSE are best effort. NSE sometimes blocks cloud IPs. If fundamentals on the server log "nothing - blocked?", run `fundamentals` on your laptop instead.
- **Timestamps:** all times are IST (Asia/Kolkata), stored without a timezone. The server must be set to IST, which the setup script does.
- **Logs:** `state/logs/`. Progress, token and universe files are also in `state/`. Do not share `state/access_token.json` or `.env`.
