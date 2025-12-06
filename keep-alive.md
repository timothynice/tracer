# Keep Backend Alive on Render Free Tier

## Problem
Render free tier spins down services after 15 minutes of inactivity, causing 60+ second cold starts.

## Solution Options

### Option 1: External Monitoring Service (Recommended - Free)

Use a free monitoring service to ping your backend every 10-14 minutes:

#### UptimeRobot (Free)
1. Sign up at https://uptimerobot.com/
2. Create a new monitor:
   - Monitor Type: HTTP(s)
   - URL: `https://tracer-n32i.onrender.com/health`
   - Monitoring Interval: 5 minutes (free tier)
3. This keeps your service awake during active hours

#### Cron-Job.org (Free)
1. Sign up at https://cron-job.org/
2. Create a new cron job:
   - URL: `https://tracer-n32i.onrender.com/health`
   - Schedule: Every 10 minutes
   - Execution: GET request

### Option 2: GitHub Actions (Free for public repos)

Create `.github/workflows/keep-alive.yml`:

```yaml
name: Keep Backend Alive
on:
  schedule:
    # Run every 10 minutes during active hours (adjust timezone as needed)
    - cron: '*/10 0-23 * * *'
  workflow_dispatch: # Allow manual trigger

jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping backend health endpoint
        run: |
          curl -f https://tracer-n32i.onrender.com/health || exit 0
```

### Option 3: Upgrade to Render Paid Tier
- $7/month per service
- No cold starts - always running
- Better performance and reliability

## Current Behavior (Without Keep-Alive)

- ✓ First user of the day: 60+ second wait
- ✓ Subsequent users (within 15 min): Instant
- ✗ After 15 min idle: 60+ second wait again

## With Keep-Alive Monitoring

- ✓ Consistent performance for all users
- ✓ No cold start delays during active hours
- ✓ Free tier remains free

## Trade-offs

**Keep-Alive Pros:**
- Improves user experience significantly
- Free solutions available
- Easy to set up

**Keep-Alive Cons:**
- Uses your monthly free tier hours (750 hours/month = ~1041 hours for 30 days if running 24/7)
- Backend runs even when not in use
- May hit rate limits if you ping too frequently

## Recommendation

For a demo/portfolio project: Use UptimeRobot (5-minute intervals)
For production use: Consider upgrading to Render paid tier ($7/month)
