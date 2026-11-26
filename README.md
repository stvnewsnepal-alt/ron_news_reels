Hamro Chitwan News Reels — starter automation
This project creates and publishes one fresh news Reel at each scheduled Nepal-time run.
Schedule
07:00 NPT
14:00 NPT
18:00 NPT
21:00 NPT
GitHub Actions cron is UTC, so the workflow uses 15 1, 15 8, 15 12, and 15 15.
Add GitHub Secrets
Repository → Settings → Secrets and variables → Actions → New repository secret:
FB_PAGE_ID: Facebook Page ID
FB_PAGE_ACCESS_TOKEN: Page access token with the required Reels publishing permissions
Do not paste tokens into source code.
Logo
Keep the existing logo image in the repository root. The code auto-detects an image in the root folder; if there are multiple images, rename the intended logo to logo.png. You can also set LOGO_PATH in the workflow.
Sources
The starter checks the homepages of Naya Patrika and Jana Aastha. Change SOURCE_URLS in .github/workflows/reels.yml if required. It refuses to use unrelated stock photos when an article photo cannot be downloaded.
Important first test
Run Actions → Hamro Chitwan News Reels → Run workflow manually. Check the output MP4 and Facebook Page before relying on the schedule. Meta may require Page access and the correct video/Reels publishing permissions.
Notes
Output is 1080×1350 (4:5).
Nepali voice uses Edge TTS ne-NP-SagarNeural.
Each run attempts one unused recent story; duplicate history is committed back to the repository.
This is a starter implementation. News websites can change their HTML, and Meta may require a different upload flow for some Page/app configurations. Check the first run logs before enabling unattended publishing.
