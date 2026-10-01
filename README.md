# 504 Tag Follow-Up

A daily-updated board showing which teammates were @mentioned on a REsimpli lead and haven't left a note since.

- **Board:** https://tscott504.github.io/504-tag-follow-up/ (team password required)
- **Updates:** every morning at about 6:45 AM Central, through GitHub Actions and the REsimpli Open API
- **Privacy:** the page and the data cache are encrypted with the team password. Nothing readable about leads, sellers or comments is stored in this repo.

## One-time setup

1. **Secrets:** Settings > Secrets and variables > Actions > New repository secret
   - `RESIMPLI_API_KEY`: your REsimpli Open API key
   - `BOARD_PASSWORD`: the team password
2. **Pages:** Settings > Pages > Build and deployment > Source: **GitHub Actions**
3. **First publish:** Actions > Refresh board > Run workflow. Untick "Pull new tags" for an instant publish of the page already in `site/`. The first run with it ticked reads every lead and takes about 45 minutes (the API allows 100 requests a minute). Later daily runs only read leads that changed.

## Changing things

- **Who's left out:** `EXCLUDE_PEOPLE` and `EXCLUDE_CAMPAIGNS` near the top of `refresh_tags.py`
- **Schedule:** the `cron` line in `.github/workflows/refresh.yml` (times are UTC)
- **Password:** update the `BOARD_PASSWORD` secret, then run the workflow. Anyone who ticked "Remember on this device" will be asked again.

Clearing a lead on the board only hides it in that person's browser. It never changes anything in REsimpli.
