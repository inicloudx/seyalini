# Seyalini: project guide for Claude Code

Seyalini (Tamil *seyal* = action/work) is INIXR's AI agent team. Its first job is marketing INIXR's
Android AR apps (AlphaMagic AR first) with daily YouTube Shorts. The founder (Nithy) only reviews.

- Vision, users and roadmap: the "Seyalini — Vision, Goal and Plan" doc (claude.ai artifact).
- Owner: Nithy, GitHub `inicloudx/seyalini`. Never use the old name "ReachStack" anywhere.
- Status date of this file: 1 Oct 2026.

## 1. What works today (the loop)

```
Plan idea -> Script -> Video (Veo clip + images + voice-over) -> Your check -> Post to YouTube -> Analyst reads views -> planner favours winning ideas
```

| Step | Where in the code | Status |
|---|---|---|
| App onboarding (Play Store link -> AI brand brief -> "Looks good") | `agents/marketing/strategist.py`, `dashboard/product_views.py` | done |
| Plan today's idea (pillars, A–Z letters, no AI cost) | `agents/marketing/planner.py` | done, uses `pillar_scores` from Analyst |
| Script (Gemini `gemini-3.5-flash-lite` via LiteLLM) | `agents/marketing/script_writer.py` | done |
| Video: hybrid = Veo clip for scene 1 + images, Ken Burns, captions, logo, end card | `agents/marketing/video_producer.py`, `tools/veo.py`, `tools/imagegen.py`, `tools/editor.py` | done |
| Voice-over per scene (Gemini TTS `gemini-3.8-flash-lite-tts`, Interactions API) | `tools/tts.py` | done, needs google-genai >= 2.25 |
| Background music (optional mp3s in `tenants/<org>/products/<app>/assets/music/`) | `tools/editor.py` | works when files exist |
| Review: Approve / Change something (redo + learned rule) / Reject (discard) | `core/approvals.py`, `partials/approval.html` | done |
| Publisher agent: YouTube upload (OAuth per organisation, private until Google audit) | `agents/publisher/`, `tools/youtube.py`, Settings -> YouTube | done |
| Analyst agent: YouTube views/likes, scores pillars, insights on Today | `agents/analyst/` | done |
| "Why so few views?" check: Analyst compares our Shorts with the Scout's top Shorts, Gemini watches our 2 latest public ones, gives causes + exactly 3 fixes (Marketing / You) + a test; Marketing fixes become `Rule`s (source `analyst`) only after your yes (or "use fixes 1 and 3" in chat). Videos tab button, Manager chat, Today card | `agents/analyst/diagnose.py` | done |
| Made for Kids per content idea: pillar `audience: kids|adults` (product.yaml or "Name | idea | adults" in the editor); adults ideas are written for parents/teachers and uploaded NOT made for kids; kids ideas stay made for kids | `publisher.made_for_kids()`, planner, script_writer, strategist | done |
| Manager agent = approvals from your phone: every script and video (the video itself) goes to Telegram with Approve / Change / Reject; "Change" asks what, the next message is the redo note; posted / failed alerts; plain-word chat (what's waiting, make a Short, check views, study trends); also in-app `/chat/` (💬) | `agents/manager/chat.py`, `dashboard/chat_views.py`, `tools/telegram.py`, `ChatMessage`, Settings -> Approve from your phone | done |
| Scout agent: daily study of top Shorts (YouTube search + Gemini watches them), proven patterns fed to every script, "What's working on YouTube" card | `agents/scout/`, 06:00 in `config/celery.py` | done |
| Videos tab: play, download, delete, free up space, post / retry YouTube, check views | `dashboard/views.py` `videos*` | done |
| Multi-organisation: own logins, roles (owner/reviewer/viewer), encrypted keys, budgets | `core/` | done |
| Installable on phone (PWA) | `dashboard/pwa.py` | done |
| **Runs by itself 24/7** | Docker on the server shared with Earnly (section 5) | **NOT YET: next task below** |
| Instagram / Facebook, installs per video, YouTube audit | - | planned |

Agents (cards in `tenants/<org>/agents/*.yaml`, versioned in DB by `load_tenants`):
marketing, publisher, analyst, scout, manager (active), catalyst / developer (planned).

## 2. Tech

- Python 3.12, Django 5.2, HTMX (vendored), no JS build step.
- DB: SQLite (laptop, and fine on the server for one company). Postgres-ready via `DATABASE_URL`.
- Jobs: `core/jobs.enqueue()`. `JOBS_MODE=thread` (laptop, no Redis) | `celery` (server, Redis) | `sync` (tests).
- Schedule: `config/celery.py` beat: 07:00 script, 14:00 script (only if 2/day), 21:00 analyst (Asia/Kolkata).
- AI: LiteLLM for text; `google-genai` for images, Veo, TTS. Prices newer than LiteLLM's table are in `agents/llm.py FALLBACK_PRICES`.
- Video: ffmpeg from `imageio-ffmpeg` (see ARM note in section 5), Pillow overlays.
- Settings load `.env` themselves (`config/settings.py _load_dotenv`).
- Tests: `python manage.py test core` (49 tests, dry-run AI, fake YouTube). Keep them green.

Money rules: every agent has `monthly_budget_usd`; `AgentRuntime.ensure_budget()` stops jobs at the cap.
Marketing = $15/month (1 hybrid Short/day ≈ ₹40). Google Cloud budget alert ₹1,000 (raise to ~₹1,500).

## 3. Conventions

- Plain, friendly UI copy for a non-technical owner. Mobile-first. Four tabs: Today, Videos, Apps, Settings.
  Engine-room details live under Settings -> "AI team & activity" (`/advanced/`).
- Never lose work over one failed step (Veo fails -> picture; voice fails -> silent; notes in the event log).
- Secrets: org keys encrypted in `TenantSecret` (Fernet from `SEYALINI_ENCRYPTION_KEY` or `DJANGO_SECRET_KEY`).
  Never print or commit keys. `.env` is git-ignored.
- Evolve, never rebuild: add agents as cards + a module under `agents/<name>/`, reuse `AgentRuntime`.
- After changing agent YAML or product YAML: `python manage.py load_tenants`.

## 4. Laptop run

```
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_tenants
python manage.py runserver        # http://127.0.0.1:8000
python manage.py telegram_poll    # 2nd window, only if Telegram is connected: phone replies on a laptop
```
Laptop `.env`: `JOBS_MODE=thread`, `GEMINI_API_KEY=...`, same `DJANGO_SECRET_KEY` on every laptop.

## 5. NEXT TASK: host Seyalini at https://seyalini.inixr.com (Docker, on the server it shares with Earnly)

Decided 1 Oct 2026: both free Oracle micro servers are used (inixr.com website; Earnly), so Seyalini runs in
Docker on **Earnly's server** (129.225.118.237, Ubuntu 24.04, x86, 1 GB RAM + swap), like Earnly does.
Earnly works 24/7 and has priority; Seyalini works a few hours a day.

How the two share the server:
- **Front door: NOT DECIDED YET (1 Oct 2026).** Earnly's nginx owns ports 80/443, and Nithy's rule is
  **never change anything on Earnly's side** (repo, containers, config). So Earnly cannot forward
  seyalini.inixr.com. The first design did that; it is rejected, and `docker-compose.yml` here still carries
  its leftovers (the `earnly_default` network on `nginx`, no public port) which must be reworked before hosting.
  Likely answer: Seyalini's own nginx on its own port (e.g. `https://seyalini.inixr.com:8443`; Telegram webhooks
  allow 8443) with its own certificate. Decide with Nithy first.
- **Containers** (`docker-compose.yml`): `redis` (queue), `web` (gunicorn, 1 process; Telegram replies run in its
  threads: `CHAT_JOBS_MODE=thread`), `worker` (celery solo pool + beat `-B`), `nginx` (static, media, proxy).
  Memory limits on each, `cpu_shares: 512` on the worker, `FFMPEG_THREADS=1`.
- **The worker sleeps** (`WORKER_SLEEPS=1`, `core/worker_sleep.py`): it stops itself after 15 idle minutes outside
  `WORK_WINDOWS` (India time, around the 06:00 / 07:00 / 14:00 / 21:00 jobs). `deploy/worker-wake.sh` (cron, every
  minute) starts it when a window opens or a job waits in Redis (e.g. you approved a script on Telegram).
- **Data** (volume `seyalini_data` -> `/app/data`): `db.sqlite3`, `media/`, `tenants/`. `seed_tenants` copies
  `tenants/` from the code on first start; later only agent cards are refreshed from git, apps and organisation
  settings belong to the dashboard on the server.

### Steps

1. **DNS:** A record `seyalini` -> 129.225.118.237 (Namecheap, same place as `earnly`).
2. **Front door:** rework per the decision above (nothing on Earnly's side).
3. **Seyalini code:** copy `deploy/server-setup.sh` to the server, `bash server-setup.sh` (as ubuntu). It adds 3 GB
   swap, makes a GitHub deploy key (add it in GitHub -> inicloudx/seyalini -> Settings -> Deploy keys, read-only,
   run again), clones to `~/seyalini`, installs the wake-up cron and the 03:15 backup.
4. **Start:** `cd ~/seyalini && ./deploy.sh` (makes `.env` with new secrets: save them). Put `GEMINI_API_KEY` in
   `~/seyalini/.env`, run `./deploy.sh` again. First build takes several minutes.
5. **https:** Seyalini's own certificate (script to be written with the front-door rework).
6. **Login:** `cd ~/seyalini && docker compose exec web python manage.py createsuperuser --username nithy`.
7. **YouTube:** Google Cloud (project "seyalini") -> Clients -> add redirect URI
   `https://seyalini.inixr.com/settings/youtube/callback/`; then Settings -> YouTube: client ID/secret, Connect.
8. **Telegram:** Settings -> Approve from your phone -> Connect my phone (one time; the server uses a webhook,
   so `telegram_poll` is NOT needed there, and must not run on the laptop for the same bot at the same time).
9. **Smoke test:** New Short -> approve on Telegram -> video -> approve -> posted. Next morning: 06:00 Scout,
   07:00 script. Check `docker compose ps -a`: the worker shows "Exited (0)" when asleep; that is normal.
10. **Updates:** push from the laptop, then on the server `cd ~/seyalini && ./deploy.sh`.

### Gotchas

- Fresh server DB: reconnect YouTube and Telegram there (steps 7-8); the laptop keeps its own database.
- `tenants/inixr/products/` has two leftover AlphaMagic copies (`alpha-magic`, `alpha-magic-2`); the dashboard
  offers to archive them. Tests use a clean copy with only `alphamagic`.
- Videos render slowly on this server (one ffmpeg thread, swap): several minutes each. Fine for 1-2 a day.
- Logs: `docker compose logs -f worker` / `web`. Worker stuck asleep with work waiting: `docker start seyalini_worker`
  and check `crontab -l` has `worker-wake.sh`.
- Media files are served by nginx without login (URLs are hard to guess). Fine for marketing videos.
- YouTube uploads stay **private** until Google's API audit; publisher setting `youtube_privacy` in
  `tenants/inixr/agents/publisher.yaml`.
- Google retired Gemini 2.5 for new users (Sep 2026). Current IDs are in the agent YAML and `video_producer.py DEFAULTS`.
- Veo on the Gemini Developer API rejects `generate_audio` and `negativePrompt`; `tools/veo.py` drops unsupported options automatically.
- Never touch Earnly from Seyalini work: not its repo, containers, volumes, nginx or `.env`. And never
  `git add` in `C:\Projects\earnly` from a Seyalini session (a staged file once got swept into an Earnly commit).

## 6. After hosting (roadmap)

1. YouTube API audit (privacy policy + terms pages on inixr.com, form answers) -> auto-public.
2. Instagram Reels + Facebook (Meta Graph API) as more publisher targets.
3. Installs per video: Play Console reports by UTM campaign `seyalini` (link already in descriptions).
4. Phone approvals via notifications (Telegram tool exists: `tools/telegram.py`).
5. Manager, Catalyst, Developer agents; autonomy L2 -> L3 when approval rate > 90% for 30 days.
