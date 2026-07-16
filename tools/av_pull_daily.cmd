@echo off
REM Daily Alpha Vantage EARNINGS pull (free tier ~25/day). Resumable: the cache
REM skips already-fetched symbols, so this safely runs until the universe is done.
REM Registered as Scheduled Task "hmt_av_earnings_daily". Remove with:
REM   schtasks /Delete /TN hmt_av_earnings_daily /F
cd /d "C:\Users\matth\Documents\Vibing\help_me_trade"
echo. >> "data\research\av_pull.log"
echo ==== %DATE% %TIME% ==== >> "data\research\av_pull.log"
"C:\Python313\python.exe" -m tools.av_earnings --from-earnings-db --limit 24 >> "data\research\av_pull.log" 2>&1
