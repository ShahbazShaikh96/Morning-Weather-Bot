# Kent weather webhook deployment

Code is based on main commit 1eaa5692748f6a970e22b74b38fc42096f1aadfd.
The existing weather_bot.py, test_weather_bot.py, telegram_setup.py and daily workflow
are unchanged. Daily channel sends remain independent of the private-chat webhook.
No AI API is used. GitHub secrets cannot be retrieved for reuse: enter the same bot
token directly into Render yourself. Never put tokens, DB URLs or webhook secrets
in chat, Git, URLs in your browser, screenshots, or shell command arguments.

## Free hosting constraints verified October 10, 2026

https://render.com/docs/free documents 15-minute idle sleep, about one-minute startup,
750 instance hours per workspace/month, ephemeral files, no free persistent disks,
and possible arbitrary restarts. Bandwidth/build quotas are workspace-dependent;
check Billing > Monthly Included Usage before deployment. Without a payment method,
exhaustion suspends services/builds; with one, supplementary charges can apply. Keep
this workspace without a payment method if strictly avoiding charges. No keep-alive
job is included. A cold request may time out, then Telegram retries; replies can
therefore take several minutes. Telegram retains incoming updates for at most 24h:
https://core.telegram.org/bots/api#getting-updates . Long suspensions can lose commands.

Free Render Postgres expires in 30 days and free Key Value is not durable. Use a
Neon **Free** Postgres project for the update ledger. Neon announced 1 GB/project
on October 2, 2026: https://neon.com/blog/neon-free-plan-1-gb-per-project . Review
https://neon.com/pricing and your console's compute/transfer quotas during setup.
No paid database plan is required. If quotas expire, health returns 503 and sends
stop rather than proceeding without duplicate protection.

## 1. Publish code

The connected GitHub integration returned 403 when creating a branch, despite repo
metadata showing push=true. Nothing was pushed. Use your authenticated local Git
client to import this bundle into a fresh clone, review the diff, commit on a branch,
push, and open/merge a PR. Include .github/workflows/web-tests.yml. Do not replace or
edit the existing .github/workflows/weather.yml. Alternatively reconnect GitHub with
repository Contents write access before asking Codex to publish the changes.

Run `python -m pip install -r requirements.txt` then `python -m unittest discover -v`.
The Postgres integration test runs in the supplied CI job against disposable Postgres.
Locally it needs TEST_DATABASE_URL pointing to a disposable database; never production.

## 2. Find your numeric Telegram user ID safely

In Telegram open https://t.me/kent_weather_bot , press Start and send /help.
Before webhook registration run `python manage_webhook.py identity` locally. Enter
the bot token only in its hidden terminal prompt. It displays recent private-chat
sender IDs; choose yours, not your channel ID. If there are several users, identify
yours independently in Telegram before continuing. Identity discovery deliberately
refuses to poll when a webhook is already configured. Do not run telegram_setup.py
once the webhook is active because getUpdates conflicts with webhook delivery.

## 3. Create the free durable ledger

Sign into https://console.neon.tech and create a project on the **Free** plan.
Choose a nearby region (for example US East) and the default Postgres database.
Open Connect and copy the pooled Postgres connection string with TLS enabled.
It is a credential: enter it only in Render's secret environment variable form.
The app creates telegram_updates on first boot, so the DB role needs CREATE TABLE
and SELECT/INSERT/UPDATE/DELETE permission in its schema. This ledger stores only
update IDs, states and timestamps, no message contents or Telegram credentials.
Do not delete/reset the ledger during deployments. Rows are retained indefinitely;
at personal-bot volume this is small. Rotating to a new Telegram bot requires a fresh
ledger because update IDs belong to the old bot. No periodic database query is used.

## 4. Create Render service and enter secrets yourself

Sign into https://dashboard.render.com and connect the repository. Choose New >
Blueprint and select the reviewed branch containing render.yaml, or New > Web Service
with runtime Python, instance **Free**, build `pip install -r requirements.txt`, start
`gunicorn 'web_app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 180 --access-logfile /dev/null`
and health check `/healthz`. Python version: 3.12.11.

Set these in Render's Environment form (secret values never in Git):

| Variable | Value |
|---|---|
| TELEGRAM_BOT_TOKEN | Existing @kent_weather_bot token from your password manager/BotFather |
| TELEGRAM_ALLOWED_USER_ID | Your positive numeric private-chat sender ID |
| TELEGRAM_WEBHOOK_SECRET | Random password-manager secret, 32–256 characters using A–Z a–z 0–9 _ - |
| DATABASE_URL | Neon pooled connection string with sslmode=require |
| WEATHER_TIMEZONE | America/New_York |
| NWS_USER_AGENT | MorningWeatherBot/2.0 (github.com/ShahbazShaikh96/Morning-Weather-Bot) |

The Render app does not need TELEGRAM_CHAT_ID. Leave both existing GitHub secrets
in place for daily channel briefings. Deploy and open https://YOUR-SERVICE.onrender.com/healthz .
Expect `{"status":"ok"}` and HTTP 200. A 503 means database/table access is unavailable;
check settings without sharing secret values. Startup intentionally fails closed on
missing/invalid credentials or inaccessible database.

## 5. Register the webhook securely

Run `python manage_webhook.py register` locally. Enter token at the hidden prompt,
then the Render HTTPS origin, then the same webhook secret at its hidden prompt.
The script checks health, calls setWebhook with max_connections=1 and allowed_updates
=[message], and preserves pending updates. Do not use token-bearing curl commands.
Registration does not affect GitHub's outbound sendMessage channel briefings.
Run `python manage_webhook.py status` for configuration, pending count and last error
timestamp; it never prints tokens or Telegram remote error descriptions.

## 6. Live end-to-end acceptance test

1. Send /help in your private chat with @kent_weather_bot. Confirm command instructions.
2. Send /forecast tomorrow 15:00. Expect Kent, an explicit New York date/time and °F/°C.
3. Send /clothes tomorrow evening. Expect 18:00–22:00 clothing advice, or an honest
   unavailable response when complete hourly coverage is absent.
4. Send /forecast 2099-01-01 15:00. Expect unavailable; no invented forecast.
5. From a different Telegram user and a group, try /help: expect no response.
6. Wait over 15 minutes without health polling, then send /help once. Allow several
   minutes for startup/retries. Check status: pending count should drain to zero.
7. Redeploy and repeat a new command; the ledger remains in Neon. CI verifies replay
   of the SAME update across app recreation and concurrent Postgres claims. Do not
   forge live update IDs; use test fixtures against a disposable test database.
8. Check GitHub Actions > Morning weather > Run workflow with send_message=true
   (test_connection=false). This sends a real channel briefing; confirm it in Telegram.

The bundled test_e2e.py exercises real local HTTP, command parsing, NWS fixtures,
reply sending, duplicate suppression and app recreation. Live Telegram acceptance
requires your accounts and secure secret entry and has not yet been performed.

## Retry contract and recovery

NWS/DB failures before the durable send claim return HTTP 503 for Telegram retry.
Concurrent/replayed update IDs are suppressed with a Postgres primary-key insert.
Explicit Telegram 429 releases the claim and returns 503 with Retry-After (Telegram's
own retry scheduling may differ). All other send failures are conservatively uncertain.
A committed `sending` claim after a crash, or an `uncertain` claim after a network error,
is never automatically resent: Telegram sendMessage has no idempotency key, so exactly
once delivery across the network/DB boundary cannot be guaranteed. This chooses
avoiding duplicate replies over possibly retrying an accepted message. If a reply is
missing, send a NEW command; do not delete old claims. Logs explicitly flag uncertainty.
There are no background tasks that could be lost after HTTP acknowledgement.

To roll back the app, use Render's previous deploy while retaining the Neon ledger.
To stop receiving commands, suspend/delete the Render service and remove the webhook
using Telegram deleteWebhook through a hidden-token local tool; daily channel sends
remain independent. Pending commands expire after 24h. Keep /healthz public; it exposes
only availability, not user IDs or credentials.

## Forecast response-time improvements

The interactive app caches NWS point metadata for 24 hours and hourly periods for
5 minutes in process memory. Repeated commands within five minutes avoid both NWS
HTTP requests; concurrent requests share a single cache refresh. Replies disclose the
five-minute cache window. Expired data is never served on errors, and restarts clear
the cache. /help already avoids NWS entirely. The daily briefing remains unchanged.
This cache does not replace the durable Neon update ledger. Render idle startup and
Neon wake-up/network latency still apply; instant replies after idle are not guaranteed.
No scheduled keep-alive, paid service, or additional credential is introduced.
