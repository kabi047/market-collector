# Recording deployment and recovery

The GitHub morning run on 18 November started at 16:38 IST. GitHub's scheduled
events cannot guarantee market-open timing. Login and storage checks alone do
not prove that a market session was captured.

## Always Free VM

An Oracle Always Free Ubuntu VM can run the collector on its own clock. Account
creation requires Oracle's card verification. Use an Always Free eligible shape
and boot volume; do not enable a paid upgrade. The primary deployment now uses an Always Free eligible AMD micro VM in
Hyderabad, with 1 OCPU, 1 GB RAM, 46.6 GB boot storage and 2 GB swap. The account
was verified as Free Tier; no paid upgrade was performed. Private connection
details are saved locally in `state/oci_deployment.json`.

Copy the repository and private `.env` to the VM, then run:

```sh
bash deploy/setup_worker.sh
```

The service starts after boot, logs in itself at 08:15 IST on weekdays, records
until 15:45, and retries failures every minute. Uploads run every five minutes.
View its status with `sudo systemctl status market-collector` and logs with
`sudo journalctl -u market-collector -f`. It requires no inbound web port.
Run only one VM worker with this API key. Disable GitHub recording schedules
after the VM is confirmed operational to avoid unnecessary parallel sessions.

## Free external scheduler with GitHub runners

`capture.yml` is now manual-only and accepts `morning`, `afternoon`, and `probe`.
Do not start parallel GitHub recordings while the VM is healthy. Old GitHub cron
triggers were removed after the primary VM service was installed. Both recording slots
log in themselves and reuse the day's shared token. Morning covers 09:00–14:00
with setup from 08:15; afternoon covers 13:40–15:45. The 20-minute overlap covers
the handoff. GitHub's per-job limit prevents a single runner covering the full
session. Dispatches for the same slot are serialized. Completed slots are saved
in private cloud storage so later retries skip them. Failed recording is retried
by subsequent triggers. Feed stalls cause failure rather than success.

An external scheduler can POST to:

```
https://api.github.com/repos/kabi047/market-collector/actions/workflows/capture.yml/dispatches
```

Use a fine-grained GitHub token restricted to this repository, with Actions write
permission. Keep it in the scheduler's private Authorization header:
`Authorization: Bearer <token>`, plus `Accept: application/vnd.github+json` and
`Content-Type: application/json`. Body:

```json
{"ref":"main","inputs":{"slot":"morning"}}
```

Schedule morning retries at 08:15, 08:30, 08:45 and each quarter-hour through
13:45 IST on weekdays; schedule afternoon retries at 13:40, 13:55, 14:10, 14:25,
14:40, 14:55, 15:10, 15:25. Use `probe` for an immediate authentication, all-50
company quote/depth, and storage test. Do not send Zerodha credentials to the
scheduler. Renew its GitHub token before expiry. cron-job.org signup was not
completed during setup; external triggers are not operational until connected.

This route avoids GitHub's cron-event delays, but runner allocation can still
be delayed. It has no guarantee of complete capture. Check actual uploaded
timestamps after a trading day. Failed uploads retain local files and attempt
a seven-day GitHub artifact backup. Raw depth for missed times cannot be
reconstructed from historical candles.
