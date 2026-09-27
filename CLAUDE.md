# Seyalini: project guide for Claude Code

Seyalini (Tamil *seyal* = action/work) is INIXR's AI agent team. Its first job is marketing INIXR's
Android AR apps (AlphaMagic AR first) with daily YouTube Shorts. The founder (Nithy) only reviews.

- Vision, users and roadmap: the "Seyalini — Vision, Goal and Plan" doc (claude.ai artifact).
- Owner: Nithy, GitHub `inicloudx/seyalini`. Never use the old name "ReachStack" anywhere.
- Status date of this file: 27 Sep 2026.

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
| Videos tab: play, download, delete, free up space, post / retry YouTube, check views | `dashboard/views.py` `videos*` | done |
| Multi-organisation: own logins, roles (owner/reviewer/viewer), encrypted keys, budgets | `core/` | done |
| Installable on phone (PWA) | `dashboard/pwa.py` | done |
| **Runs by itself 24/7** | Celery beat on a server | **NOT YET: next task below** |
| Instagram / Facebook, installs per video, YouTube audit | - | planned |

Agents (cards in `tenants/<org>/agents/*.yaml`, versioned in DB by `load_tenants`):
marketing (active), publisher (active), analyst (active), manager / catalyst / developer (planned).

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
```
Laptop `.env`: `JOBS_MODE=thread`, `GEMINI_API_KEY=...`, same `DJANGO_SECRET_KEY` on every laptop.

## 5. NEXT TASK: host Seyalini at https://seyalini.inixr.com on its own Oracle server

Decided 27 Sep 2026: the inixr.com website server is too small (1 GB RAM, no swap; its nginx runs inside
Docker), so Seyalini gets its **own free Oracle server** and subdomain. The website server is never touched.
`URL_PREFIX` stays empty (sub-path support still works and is tested, if ever needed).

1. **Server:** Oracle Console -> Compute -> Create instance. Ubuntu 24.04, shape **VM.Standard.A1.Flex
   2 OCPU / 12 GB** (Always Free; pick the aarch64 image). If A1 is "out of capacity", VM.Standard.E2.1.Micro
   (also free; setup.sh adds swap). Same SSH key as the website server. Check "Always Free-eligible".
2. **Oracle network:** in the instance's VCN Security List, add Ingress 0.0.0.0/0 TCP 80 and 443.
3. **DNS:** A record `seyalini` -> the server's public IP (where inixr.com's DNS is managed).
4. **GitHub deploy key + setup:** copy `deploy/setup.sh` to the server, `sudo bash setup.sh`. It stops twice:
   (a) prints a deploy key: add it in GitHub -> inicloudx/seyalini -> Settings -> Deploy keys (read-only);
   (b) asks for `GEMINI_API_KEY` in `/opt/seyalini/app/.env` (it generates the Django and encryption secrets:
   save them in a password manager). Re-run after each stop. It installs packages, system ffmpeg
   (`IMAGEIO_FFMPEG_EXE`), redis, the three systemd services, nginx, sudoers for restarts, 02:30 backup cron,
   and opens 80/443 in Ubuntu's iptables (Oracle images block everything but SSH).
5. **HTTPS and login:** the two commands setup.sh prints at the end (certbot, `createsuperuser` as `nithy`).
6. **YouTube:** Google Cloud (project "seyalini") -> Clients -> add redirect URI
   `https://seyalini.inixr.com/settings/youtube/callback/`. Then Seyalini Settings -> YouTube: paste client
   ID/secret, Connect, choose the channel (laptop used "Ini Cloudx"; Nithy may prefer "Inixr digital").
7. **Smoke test:** open the site, New Short, approve script and video, check it posts to YouTube,
   "Check views now" works, next morning a 07:00 script appears. Then on the phone "Add to Home screen".
8. **Updates:** push from the laptop, then on the server `sudo -u seyalini /opt/seyalini/app/deploy/update.sh`.

### Gotchas

- Fresh server DB: `tenants/inixr/tenant.yaml` carries the laptop's YouTube channel name, but Settings only
  shows "Connected" when the login token exists, so reconnect on the server as in step 6.
- `tenants/inixr/products/` also has two leftover AlphaMagic copies (`alpha-magic`, `alpha-magic-2`); the
  dashboard offers to archive them. Tests use a clean copy with only `alphamagic`.
- Video rendering is CPU-heavy: worker concurrency is 1 on purpose.
- Media files are served by nginx without login (URLs are hard to guess). Fine for marketing videos.
- `DJANGO_DEBUG=0` on the server, otherwise secure cookies and proxy https are off.
- YouTube uploads stay **private** until Google's API audit; publisher setting `youtube_privacy` in
  `tenants/inixr/agents/publisher.yaml`.
- Google retired Gemini 2.5 for new users (Sep 2026). Current IDs are in the agent YAML and `video_producer.py DEFAULTS`.
- Veo on the Gemini Developer API rejects `generate_audio` and `negativePrompt`; `tools/veo.py` drops unsupported options automatically.
- Oracle may reclaim Always Free instances that stay nearly idle; the daily jobs should keep it active.

## 6. After hosting (roadmap)

1. YouTube API audit (privacy policy + terms pages on inixr.com, form answers) -> auto-public.
2. Instagram Reels + Facebook (Meta Graph API) as more publisher targets.
3. Installs per video: Play Console reports by UTM campaign `seyalini` (link already in descriptions).
4. Phone approvals via notifications (Telegram tool exists: `tools/telegram.py`).
5. Manager, Catalyst, Developer agents; autonomy L2 -> L3 when approval rate > 90% for 30 days.
