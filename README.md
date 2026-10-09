# Morning Weather Bot

Free U.S. weather briefing using NWS and Telegram. No AI API or AccuWeather key.

## Setup
1. Add these files to the default branch of your GitHub repository, preserving `.github/workflows/weather.yml`.
2. Create a bot with @BotFather. Add it to your Telegram channel with permission to post.
3. In Settings > Secrets and variables > Actions, add secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Never commit the token.
4. Optional repository variables: `WEATHER_LAT`, `WEATHER_LON`, `WEATHER_LOCATION`. Defaults are Kent, Ohio 44240. Location is configured, not GPS.
5. Open Actions > Morning weather > Run workflow. Leave Send to Telegram unchecked to preview. Check it for a delivery test.
6. The scheduled workflow sends at 8 a.m. America/New_York, including daylight saving changes. GitHub may delay scheduled jobs; this is a morning digest, not a real-time emergency alert service.

## Edit clothing rules
Change the `RULES` dictionary at the top of `weather_bot.py`. Temperature thresholds use Celsius. `umbrella_at_percent` applies to precipitation of any type. Layer selection uses the coolest hour in each period. The windbreaker threshold uses sustained wind when gust data is unavailable.

## Data limitations
Uses NWS daily and hourly forecasts and active alerts. Feels-like, gusts and separate snow probabilities are not supplied by the chosen feed and are explicitly marked unavailable. Overall precipitation probability is not a separate rain/snow probability. All rendered numeric temperatures use Fahrenheit and Celsius. Missing alerts never become 'no alerts'. Failed forecast retrieval fails the job without sending a stale briefing.

## Local preview
Run `python3 weather_bot.py --dry-run` with Python 3.12. Standard library only. A normal run sends to Telegram and needs the two environment variables above. Do not print tokens or commit .env files.

## Sources
- https://www.weather.gov/documentation/services-web-api
- https://core.telegram.org/bots/api#sendmessage
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule

After confirming delivery, pause your old ChatGPT task if you want to avoid duplicate briefings.
