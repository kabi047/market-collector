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

## 2b. No credit card? Free cloud via GitHub Actions (what we use)
Repo: github.com/kabi047/market-collector (code only; credentials are in GitHub Secrets, data goes to a private Hugging Face dataset).

**Unattended cloud collection:** the `daily-session` workflow logs in at 08:00 IST
and retries at 08:15 on weekdays. Both recording jobs share this daily session.
Repository Actions secrets must contain `ZERODHA_API_KEY`, `ZERODHA_API_SECRET`,
`ZERODHA_USER_ID`, `ZERODHA_PASSWORD`, `ZERODHA_TOTP_SECRET`, `HF_TOKEN`, and `HF_REPO`.
The Hugging Face dataset must be private. No laptop or daily manual login is needed
while automatic login works. This uses an unofficial Kite web-login flow and can
break if Zerodha changes it. Keep the Kite market-data subscription active.

Run `daily-session` manually to test a **fresh login**, all 50 Nifty company quotes,
five levels of depth, and a write to private storage. Do not run this manual test
while a recorder is active, because it deliberately replaces the daily session.

**Manual fallback**, when automatic cloud login is unavailable: on your PC run
```
venv\Scripts\python collect.py login
```
It logs you in and sends the day's access token to the cloud automatically. You can then switch your PC off.

Then GitHub does the rest on its own servers:
- **~08:30:** job A starts, waits for 09:00 and records until 14:15.
- **~14:00:** job B starts and records until 15:45.
- Every 30 minutes each job uploads a chunk to the private dataset, so a crash loses at most 30 minutes.
- On a market holiday it notices there are no ticks and stops.

**Get the data on your PC later (needs internet):**
```
venv\Scripts\python collect.py pull-live
venv\Scripts\python collect.py eod        # evening: minute candles + OI for today's futures/options contracts
```
Data lands in `data/<stock|index|futures|options>/zerodha_live/<date>/` as chunk files (`A_0930.parquet` etc). The A and B jobs overlap by about 15 minutes, so drop duplicate rows by (symbol, exch_ts, volume) when you load.

**Manual test any time:** GitHub -> Actions -> live-recorder -> Run workflow -> mode `check`.
If morning authentication fails, recorders stop with "token is stale". Check the
`daily-session` run in GitHub Actions. Failed data uploads fail the recording job;
remaining files are preserved as a GitHub artifact for seven days when possible.
GitHub scheduled runs can be delayed, so this setup cannot guarantee gap-free capture.

## 3. Notes and limits
- **Live feed rate:** Zerodha's websocket sends snapshots, up to about 1 per second per instrument when something changes. It is not every exchange trade. This is the fastest data Kite Connect offers. Depth is 5 levels.
- **Rate limits:** historical calls are capped at 3 per second, and the downloader stays under that. Each minute request covers at most 60 days.
- **Indices** have no volume or depth. That is how the exchange publishes them.
- **Free sources:** Yahoo and NSE are best effort. NSE sometimes blocks cloud IPs. If fundamentals on the server log "nothing - blocked?", run `fundamentals` on your laptop instead.
- **Timestamps:** all times are IST (Asia/Kolkata), stored without a timezone. The server must be set to IST, which the setup script does.
- **Logs:** `state/logs/`. Progress, token and universe files are also in `state/`. Do not share `state/access_token.json` or `.env`.
