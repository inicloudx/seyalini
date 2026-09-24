# Seyalini (செயலினி): the AI workshop

Seyalini is a team of AI agents that runs the day-to-day work of a product company. **You set goals and approve. The agents do the work.**
It is built for INIXR first (tenant 1) and is ready to host other organisations as more tenants.

```
You (approve)  ->  Manager · Catalyst  ->  Marketing · Developer · …  ->  tools  ->  foundation
```

## What works today (Steps 0–3 + app onboarding)

| | |
|---|---|
| Foundation | Tenants, products (brand briefs), **versioned agent registry**, task board, approvals, learned rules, event log with cost |
| Marketing agent | Plans the topic for free (rotates pillars and letters, never repeats within 7 days), writes a Short script with 3 video prompts, and puts it in your inbox |
| Schedule | 7 AM and 2 PM India time, once per marketing-enabled product |
| Video agent (Step 3) | When you approve a script, it makes a finished **1080×1920 Short**: AI scene images with slow zoom (or Veo clips), captions, logo, end card with the Play Store CTA, and music. It then waits for your approval |
| You | Approve, or **Redo with a reason** (for scripts *and* videos). The reason becomes a permanent rule and the agent redoes the work |
| Add your app | **Products → Add your app**: answer a few questions and paste the Play Store link. The agent reads the listing, studies the screenshots, writes a sharper brand brief (pillars, visual style, questions for you), and you review it and activate it. Store screenshots are also saved as free footage for videos |
| Guardrails | Monthly budget cap per agent (hard stop). Dry-run mode costs nothing |
| Dashboard | Command Center at `http://localhost:8000` |

## Start (any laptop with Docker)

```bash
git clone <your private repo> seyalini && cd seyalini
bash scripts/setup.sh          # creates .env, starts everything, asks for an admin user
```
Open http://localhost:8000 and press **Write a Short now**.

The VS Code option: open the folder and choose **Reopen in Container**. It gives the same setup on every laptop.

### Without Docker (quick look)
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # then set: DATABASE_URL=sqlite:///db.sqlite3  and  CELERY_EAGER=1
python manage.py migrate && python manage.py createsuperuser && python manage.py load_tenants
python manage.py runserver
```

## Go live (real AI)
1. Get a free Gemini API key at https://aistudio.google.com/apikey
2. Put it in `.env` as `GEMINI_API_KEY=...` and restart. The dry-run banner disappears.
3. Optional: add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to get each script on your phone.

Try it from the terminal: `python manage.py run_agent marketing` (script), then `python manage.py make_video <script id>` (video).

## Organisations, logins and keys
- **You are the platform admin** (the superuser). **Platform** → *Create an organisation* makes the organisation, its owner login and the default AI team.
- **Everyone logs in as themselves.** Owners add people in **Settings → Team**: Owner (settings, keys, apps), Reviewer (approves), Viewer (read only).
- **Each organisation brings its own AI key** in **Settings → AI keys**. It is stored encrypted, never shown in full, and usage is billed to their own Google account. Only organisations marked `use_platform_keys: true` (INIXR) may use the keys in `.env`. Without a key, an organisation runs in free sample mode.
- Optional public sign-up page: set `ALLOW_SIGNUP=1` in `.env`.
- Keep `SEYALINI_ENCRYPTION_KEY` (or `DJANGO_SECRET_KEY`) stable. If it changes, owners must re-enter their keys.

## Working from several laptops
- **Code, agents, brand briefs:** this Git repo is the single source of truth. Push before leaving, pull on arrival.
- **Secrets:** keep your real `.env` in Bitwarden (free) as the secure note `seyalini-env`. `setup.sh` pulls it automatically when the Bitwarden CLI is unlocked.
- **Shared data (optional):** point `DATABASE_URL` to a free cloud Postgres (e.g. Neon) and every laptop sees the same tasks and history.

## Video modes (set `config.video.mode` in `tenants/inixr/agents/marketing.yaml`)

| Mode | What | Cost per Short | 60 Shorts / month |
|---|---|---|---|
| `images` (default) | 3 AI images (Gemini image model) + motion | ~$0.12 | ~$7 |
| `hybrid` | Veo 3.1 Lite clip for the hook, images after | ~$0.50 | ~$29 |
| `veo` | Veo clip for every scene | ~$1.20 (Lite) to $2.40 (Fast) | ~$72–144 |

Prices are Google's list prices as of Sep 2026. Check them before switching modes, then set `image_usd` / `veo_usd_per_second` in the card so the budget guard stays accurate.
**Free boost:** put real screen recordings of the app in `tenants/inixr/products/alphamagic/assets/clips/`. "Watch the Magic" videos then use real footage, which usually sells best. Add a logo, music and a brand font the same way (see `assets/README.md`).

## Low budget by design
- The planner uses no AI (free). Only the script writing calls a model, and it uses a cheap one (Gemini Flash).
- **You approve the script before any video is made (Step 3)**, so you never pay for a video you would reject.
- Every agent card has `monthly_budget_usd`. The agent stops when the cap is reached, and the dashboard shows the spend.
- Dry-run mode (no API key) runs the whole flow at zero cost.

## Grow it without rebuilding
- **Change an agent:** edit `tenants/<org>/agents/<agent>.yaml`, then run `python manage.py load_tenants`. This creates a new version and keeps the old one.
- **Add a product:** add `tenants/<org>/products/<app>/product.yaml` + `brief.md`. The agents pick it up on the next run.
- **Add an organisation (customer):** copy `tenants/inixr/` to `tenants/<new>/`, change the name, theme and briefs, then run `load_tenants`.
- **Add an agent:** create a card YAML and a folder under `agents/<name>/`. It reuses `AgentRuntime` (budget, rules, logging, model access) automatically.

## Layout
```
config/     settings, schedule (celery.py)
core/       foundation: models, tenant loader, approvals, tests
agents/     runtime.py (shared by all agents), llm.py, marketing/
tools/      telegram, imagegen, veo, editor (ffmpeg), fonts (next: youtube, meta)
dashboard/  Command Center (Django + HTMX)
tenants/    one folder per organisation: agents, products, brand briefs
```

## Tests
`python manage.py test` runs 33 tests on the free dry-run AI (about 1 minute): rotation, approvals, rules, budget stop, tenant isolation, agent versioning, and real MP4 rendering and remakes, and onboarding (store parsing, AI brief with screenshots, activation), and multi-app rules (duplicates, archive, per-app quota, focus).

## Roadmap
0–2 ✅ foundation + Marketing scripts · 3 ✅ video production · 4 Telegram approve buttons · 5 YouTube upload · 6 Instagram + Facebook · 7 analytics · 8 Manager · 9 Developer · 10 Catalyst · 11 Flag Magic · 12 second organisation
