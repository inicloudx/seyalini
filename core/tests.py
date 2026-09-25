"""Run: python manage.py test   (uses dry-run AI, costs nothing)"""
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from agents.marketing.script_writer import write_script
from agents.runtime import BudgetExceeded
from core.loader import load_all, sync_agent
from core.models import AgentCard, Event, Product, Rule, Task, Tenant


TMP_MEDIA = tempfile.mkdtemp(prefix="seyalini-test-media-")


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA)
class FoundationTests(TestCase):
    def setUp(self):
        load_all()
        self.tenant = Tenant.objects.get(slug="inixr")
        self.product = Product.objects.get(tenant=self.tenant, slug="alphamagic")
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()  # adds the superuser as owner

    def test_tenant_loaded_with_brief_and_agents(self):
        self.assertIn("AlphaMagic", self.product.brief)
        self.assertEqual(AgentCard.objects.filter(tenant=self.tenant, is_current=True).count(), 4)

    def test_agent_change_creates_new_version_and_keeps_old(self):
        card, change = sync_agent(self.tenant, {"key": "marketing", "name": "Marketing", "status": "active",
                                                "model": "gemini/gemini-2.5-flash", "monthly_budget_usd": 9})
        self.assertEqual(card.version, 2)
        self.assertEqual(AgentCard.objects.filter(tenant=self.tenant, key="marketing").count(), 2)
        self.assertFalse(AgentCard.objects.get(tenant=self.tenant, key="marketing", version=1).is_current)

    def test_script_goes_to_approval_inbox(self):
        task = write_script(self.product, slot="morning")
        self.assertEqual(task.status, "awaiting_approval")
        self.assertEqual(len(task.result["scenes"]), 3)
        self.assertEqual(task.approval.decision, "pending")

    def test_planner_rotates_pillars(self):
        pillars = [write_script(self.product).payload["pillar"] for _ in range(5)]
        self.assertEqual(len(set(pillars)), 5)

    def test_redo_creates_rule_and_rewrites(self):
        task = write_script(self.product)
        self.client.force_login(self.user)
        with self.captureOnCommitCallbacks(execute=True):
            r = self.client.post(f"/tasks/{task.id}/decide/", {"decision": "redo", "chip": "Weak hook"}, HTTP_HX_REQUEST="true")
        self.assertEqual(r.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "rejected")
        self.assertTrue(Rule.objects.filter(tenant=self.tenant, text="Weak hook", source="rejection").exists())
        redo = Task.objects.get(parent=task)
        self.assertEqual(redo.status, "awaiting_approval")
        self.assertEqual(redo.payload["pillar"], task.payload["pillar"])

    def test_approve(self):
        task = write_script(self.product)
        self.client.force_login(self.user)
        self.client.post(f"/tasks/{task.id}/decide/", {"decision": "approved"})
        task.refresh_from_db()
        self.assertEqual(task.status, "approved")

    def test_budget_guard_blocks_spending(self):
        card = AgentCard.objects.get(tenant=self.tenant, key="marketing", is_current=True)
        Event.objects.create(tenant=self.tenant, agent_key="marketing", kind="llm_call", message="x",
                             cost_usd=card.monthly_budget_usd + Decimal("0.01"))
        with self.assertRaises(BudgetExceeded):
            write_script(self.product)
        self.assertTrue(Task.objects.filter(status="failed").exists())

    def test_tenants_are_isolated(self):
        other = Tenant.objects.create(slug="democo", name="Demo Co")
        write_script(self.product)
        member = get_user_model().objects.create_user("guest", password="pw")
        other.memberships.create(user=member)
        self.client.force_login(member)
        page = self.client.get("/").content.decode()
        self.assertNotIn("AlphaMagic", page)

    def test_dashboard_renders(self):
        write_script(self.product)
        self.client.force_login(self.user)
        r = self.client.get("/")
        self.assertContains(r, "Needs you")
        self.assertContains(r, "Practice mode")
        self.assertContains(r, "How it's going")        # the 5-step progress view

    def test_simple_pages_render(self):
        self.client.force_login(self.user)
        for url, text in [("/videos/", "Videos"), ("/advanced/", "AI team"), ("/products/", "Your apps"),
                          ("/products/alphamagic/", "What the AI knows"), ("/products/new/", "Let the AI study my app"),
                          ("/settings/", "Practice mode"), ("/manifest.webmanifest", "Seyalini"), ("/sw.js", "fetch")]:
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), text)


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA)
class VideoTests(TestCase):
    """Step 3: approved script -> real MP4 (dry run renders placeholder images, costs nothing)."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.tenant = Tenant.objects.get(slug="inixr")
        self.product = Product.objects.get(tenant=self.tenant, slug="alphamagic")
        self.client.force_login(self.user)

    def _approve(self, task, decision="approved", chip=""):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/tasks/{task.id}/decide/", {"decision": decision, "chip": chip})

    def test_approved_script_becomes_video(self):
        script = write_script(self.product)
        self._approve(script)
        video = Task.objects.get(kind="short_video", parent=script)
        self.assertEqual(video.status, "awaiting_approval", video.result)
        mp4 = Path(TMP_MEDIA) / video.result["video"]
        self.assertTrue(mp4.exists() and mp4.stat().st_size > 50_000)
        self.assertGreaterEqual(video.result["seconds"], 20)
        page = self.client.get("/").content.decode()
        self.assertIn(video.result["video"], page)

    def test_video_redo_learns_rule_and_remakes(self):
        script = write_script(self.product)
        self._approve(script)
        video = Task.objects.get(kind="short_video", parent=script)
        self._approve(video, "redo", "Too dark")
        self.assertTrue(Rule.objects.filter(text="Video: Too dark").exists())
        remade = Task.objects.get(kind="short_video", parent=video)
        self.assertEqual(remade.status, "awaiting_approval")

    def test_cost_modes(self):
        from agents.marketing.video_producer import DEFAULTS, estimate, plan_sources

        script = {"scenes": [{"seconds": 8}] * 3}
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(plan_sources(script, "images", "parent_tip", DEFAULTS, empty), ["image"] * 3)
        self.assertEqual(plan_sources(script, "hybrid", "parent_tip", DEFAULTS, empty), ["veo", "image", "image"])
        self.assertEqual(estimate(["image"] * 3, script, DEFAULTS), Decimal("0.12"))
        self.assertEqual(estimate(["veo", "image", "image"], script, DEFAULTS), Decimal("0.48"))
        (empty / "clips").mkdir()
        (empty / "clips" / "demo.mp4").write_bytes(b"x")
        self.assertEqual(plan_sources(script, "images", "watch_the_magic", DEFAULTS, empty), ["image", "library", "image"])

    def test_video_budget_block(self):
        from agents.marketing.video_producer import produce_video

        script = write_script(self.product)
        card = AgentCard.objects.get(tenant=self.tenant, key="marketing", is_current=True)
        Event.objects.create(tenant=self.tenant, agent_key="marketing", kind="x", message="x", cost_usd=card.monthly_budget_usd)
        with self.assertRaises(BudgetExceeded):
            produce_video(script)
        self.assertTrue(Task.objects.filter(kind="short_video", status="failed").exists())


TMP_TENANTS = Path(tempfile.mkdtemp(prefix="seyalini-test-tenants-")) / "tenants"


def _copy_tenants():
    import shutil

    from django.conf import settings as dj

    if TMP_TENANTS.exists():
        shutil.rmtree(TMP_TENANTS)
    shutil.copytree(Path(dj.BASE_DIR) / "tenants", TMP_TENANTS)


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA, TENANTS_DIR=TMP_TENANTS)
class OnboardingTests(TestCase):
    """Add your app -> agent reads store + screenshots -> draft brief -> you activate."""

    def setUp(self):
        _copy_tenants()
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.tenant = Tenant.objects.get(slug="inixr")
        self.client.force_login(self.user)

    def _fake_store(self):
        from unittest import mock

        from PIL import Image

        def fake_download(urls, folder, limit=6):
            folder.mkdir(parents=True, exist_ok=True)
            paths = []
            for i, _ in enumerate(urls[:limit], 1):
                p = folder / f"store_{i}.png"
                Image.new("RGB", (1052, 592), (200, 120, 40)).save(p)
                paths.append(p)
            return paths

        listing = {"title": "Word Zoo", "description": "Word Zoo teaches kids words with talking animals.",
                   "category": "EDUCATIONAL", "content_rating": "Rated for 3+", "screenshots": ["https://x/1", "https://x/2"],
                   "url": "https://play.google.com/store/apps/details?id=com.demo.wordzoo"}
        return (mock.patch("tools.playstore.fetch", return_value=listing),
                mock.patch("tools.playstore.download_screenshots", side_effect=fake_download))

    def test_add_app_creates_draft_then_activate(self):
        p1, p2 = self._fake_store()
        with p1, p2:
            r = self.client.post("/products/new/", {
                "name": "Word Zoo", "store_url": "https://play.google.com/store/apps/details?id=com.demo.wordzoo",
                "what_it_does": "Kids tap animals to hear words", "audience": "Kids 3-6", "goal": "installs",
                "languages": "English", "brand_colour": "#2266AA"})
        self.assertEqual(r.status_code, 302)
        product = Product.objects.get(tenant=self.tenant, slug="word-zoo")
        self.assertEqual(product.status, "review")
        draft = product.config["draft"]
        self.assertIn("Word Zoo", draft["brief_markdown"])
        self.assertEqual(draft["screens_saved"], 2)
        self.assertEqual(product.config["accent"], "#2266AA")
        self.assertTrue(Task.objects.filter(product=product, kind="product_brief", status="awaiting_approval").exists())
        self.assertContains(self.client.get("/"), "Review the brief")
        page = self.client.get("/products/word-zoo/")
        self.assertContains(page, "Questions for you")

        r = self.client.post("/products/word-zoo/", {
            "brief": "# Word Zoo brief\nKids tap animals.", "visual_style": "Soft pastel 3D animals",
            "pillars": "Animal of the day | one animal, one word\nParent tip | reading at home"})
        product.refresh_from_db()
        self.assertEqual(product.status, "live")
        self.assertTrue(product.config["marketing"])
        self.assertEqual([p["key"] for p in product.config["pillars"]], ["animal_of_the_day", "parent_tip"])
        self.assertNotIn("draft", product.config)
        folder = TMP_TENANTS / "inixr" / "products" / "word-zoo"
        self.assertIn("Word Zoo brief", (folder / "brief.md").read_text())
        self.assertIn("visual_style", (folder / "product.yaml").read_text())
        # files survive a reload and the new product is marketed + its screenshots feed videos
        load_all()
        product.refresh_from_db()
        self.assertEqual(product.brief, "# Word Zoo brief\nKids tap animals.")
        script = write_script(product)
        self.assertEqual(script.status, "awaiting_approval")

    def test_ai_brief_uses_listing_and_screenshots(self):
        import json
        from unittest import mock

        from agents.llm import LLMResult

        seen = {}

        def fake_complete(model, messages, **kw):
            seen["content"] = messages[1]["content"]
            seen["api_key"] = kw.get("api_key")
            return LLMResult(text=json.dumps({
                "brief_markdown": "# Word Zoo - Brand Brief\n...", "visual_style": "pastel",
                "pillars": [{"key": "Animal Day", "name": "Animal Day", "idea": "x"}, {"name": "Tip", "idea": "y"}],
                "improvements": ["Added proof points"], "questions": ["Which languages?"], "made_for_kids": True}),
                cost_usd=Decimal("0.002"), tokens=1500, model=model, dry_run=False)

        product = Product.objects.create(tenant=self.tenant, slug="word-zoo", name="Word Zoo", config={})
        from core.secrets import set_secret
        set_secret(self.tenant, "GEMINI_API_KEY", "tenant-own-key-123")
        p1, p2 = self._fake_store()
        with p1, p2, override_settings(LLM_DRY_RUN=False), mock.patch("agents.llm.complete", side_effect=fake_complete):
            from agents.marketing.strategist import research_product
            task = research_product(product, {"name": "Word Zoo", "store_url": "com.demo.wordzoo", "what_it_does": "tap animals"})
        texts = [c for c in seen["content"] if c["type"] == "text"][0]["text"]
        images = [c for c in seen["content"] if c["type"] == "image_url"]
        self.assertIn("talking animals", texts)          # store description reached the AI
        self.assertEqual(len(images), 2)                  # screenshots reached the AI
        self.assertEqual(seen["api_key"], "tenant-own-key-123")  # the organisation's own key was used
        product.refresh_from_db()
        self.assertEqual([p["key"] for p in product.config["draft"]["pillars"]], ["animal_day", "tip"])
        self.assertEqual(task.status, "awaiting_approval")
        self.assertEqual(Event.objects.filter(task=task, kind="llm_call").first().cost_usd, Decimal("0.002"))

    def test_store_screenshots_become_free_footage(self):
        from agents.marketing.video_producer import DEFAULTS, library_files, plan_sources
        from PIL import Image
        from tools.editor import _prep_image

        assets = Path(tempfile.mkdtemp())
        (assets / "screens").mkdir()
        shot = assets / "screens" / "store_1.png"
        Image.new("RGB", (1052, 592), (10, 200, 10)).save(shot)
        self.assertEqual(library_files(assets), [shot])
        self.assertEqual(plan_sources({"scenes": [{}] * 3}, "images", "watch_the_magic", DEFAULTS, assets)[1], "library")
        out = _prep_image(shot, assets / "framed.jpg")
        self.assertEqual(Image.open(out).size, (1620, 2880))  # landscape shown whole in a 9:16 frame

    def test_playstore_parser(self):
        from tools.playstore import package_id, parse

        page = ('<script type="application/ld+json">{"@type":"SoftwareApplication","name":"Alpha Magic",'
                '"description":"AR alphabet","contentRating":"Rated for 3+","author":{"name":"Team INIXR"}}</script>'
                '<img src="https://play-lh.googleusercontent.com/AAA=w526-h296" alt="Screenshot image">')
        data = parse(page)
        self.assertEqual(data["title"], "Alpha Magic")
        self.assertEqual(data["developer"], "Team INIXR")
        self.assertEqual(data["screenshots"], ["https://play-lh.googleusercontent.com/AAA"])
        self.assertEqual(package_id("https://play.google.com/store/apps/details?id=com.a.b&hl=en"), "com.a.b")
        self.assertIsNone(package_id("not a link"))


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA, TENANTS_DIR=TMP_TENANTS)
class MultiAppTests(TestCase):
    """Several apps: no duplicates, archive, per-app settings, clear next step."""

    def setUp(self):
        _copy_tenants()
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.tenant = Tenant.objects.get(slug="inixr")
        self.alpha = Product.objects.get(slug="alphamagic")
        self.client.force_login(self.user)

    def test_same_store_link_is_blocked(self):
        r = self.client.post("/products/new/", {"name": "Alpha Magic", "what_it_does": "x", "goal": "installs",
                                                "store_url": "https://play.google.com/store/apps/details?id=com.inixrtechnology.alphamagic"})
        self.assertContains(r, "You already have this app")
        self.assertEqual(Product.objects.filter(tenant=self.tenant).count(), 1)

    def test_similar_name_needs_confirmation(self):
        data = {"name": "AlphaMagic", "what_it_does": "x", "goal": "installs"}
        self.assertContains(self.client.post("/products/new/", data), "Is this the same app")
        self.assertEqual(Product.objects.count(), 1)
        self.client.post("/products/new/", {**data, "confirm_new": "on"})
        self.assertEqual(Product.objects.count(), 2)

    def test_activate_goes_to_command_center(self):
        r = self.client.post("/products/alphamagic/", {"brief": "# brief", "pillars": "Tip | idea", "visual_style": ""})
        self.assertRedirects(r, "/?p=alphamagic", fetch_redirect_response=False)
        page = self.client.get("/?p=alphamagic")
        self.assertContains(page, "Saved.")

    def test_archive_stops_marketing_and_hides_app(self):
        from agents.marketing.script_writer import daily_run

        script = write_script(self.alpha)
        self.client.post("/products/alphamagic/archive/")
        self.alpha.refresh_from_db()
        script.refresh_from_db()
        self.assertEqual(self.alpha.status, "archived")
        self.assertFalse(self.alpha.config["marketing"])
        self.assertEqual(script.status, "rejected")
        self.assertEqual(daily_run("morning"), [])
        self.assertContains(self.client.get("/products/"), "Archived (1)")
        self.client.post("/products/alphamagic/restore/")
        self.alpha.refresh_from_db()
        self.assertEqual(self.alpha.status, "live")

    def test_shorts_per_day_controls_schedule(self):
        from agents.marketing.script_writer import daily_run

        self.client.post("/products/alphamagic/settings/", {"marketing": "on", "shorts_per_day": "1"})
        self.assertEqual(len(daily_run("morning")), 1)
        self.assertEqual(len(daily_run("evening")), 0)
        self.client.post("/products/alphamagic/settings/", {"shorts_per_day": "2"})  # marketing off
        self.assertEqual(daily_run("morning"), [])

    def test_focus_and_next_step(self):
        other = Product.objects.create(tenant=self.tenant, slug="flag-magic", name="Flag Magic", status="live",
                                       brief="# b", config={"marketing": True, "shorts_per_day": 1})
        write_script(self.alpha)
        write_script(other)
        page = self.client.get("/").content.decode()
        self.assertIn("All apps · 2", page)
        self.assertIn("New Short for", page)          # two apps -> choose which
        self.assertIn("Next step", page)
        self.assertIn("Write and approve your first script", page)
        page = self.client.get("/?p=flag-magic").content.decode()
        self.assertIn("New Short · Flag Magic", page)
        self.assertIn("/ 7<", page)
        self.assertNotIn("Letter of the Day", page)        # AlphaMagic's inbox hidden while focused on Flag Magic


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA)
class LetterWordTests(TestCase):
    def test_letter_uses_real_scene_word(self):
        load_all()
        product = Product.objects.get(slug="alphamagic")
        task = write_script(product)
        self.assertEqual(task.payload["letter"], "A")
        self.assertEqual(task.payload["word"], "Aeroplane")
        self.assertIn("Aeroplane", task.result["title"])
        self.assertIn("A for Aeroplane", product.brief)


class LoaderTests(TestCase):
    def test_folder_without_product_yaml_is_skipped(self):
        _copy_tenants()
        (TMP_TENANTS / "inixr" / "products" / "half-done" / "assets").mkdir(parents=True)
        with override_settings(TENANTS_DIR=TMP_TENANTS):
            load_all()
        self.assertFalse(Product.objects.filter(slug="half-done").exists())
        self.assertTrue(Product.objects.filter(slug="alphamagic").exists())


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA, TENANTS_DIR=TMP_TENANTS)
class DuplicateTidyTests(TestCase):
    def test_unfinished_copy_is_offered_for_archive(self):
        _copy_tenants()
        user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        t = Tenant.objects.get(slug="inixr")
        Product.objects.create(tenant=t, slug="alpha-magic-2", name="Alpha Magic", status="review",
                               config={"draft": {"brief_markdown": "x"}, "store_url": "https://play.google.com/store/apps/details?id=com.inixrtechnology.alphamagic"})
        self.client.force_login(user)
        page = self.client.get("/?p=all").content.decode()
        self.assertIn("looks like a second copy", page)
        self.assertIn("Archive this copy", page)
        r = self.client.post("/products/alpha-magic-2/archive/", {"next": "home"})
        self.assertRedirects(r, "/?p=all", fetch_redirect_response=False)
        self.assertEqual(Product.objects.get(slug="alpha-magic-2").status, "archived")
        self.assertNotIn("second copy", self.client.get("/?p=all").content.decode())


@override_settings(LLM_DRY_RUN=False, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA, TENANTS_DIR=TMP_TENANTS)
class MultiTenantTests(TestCase):
    """Each organisation: own logins, own roles, own encrypted keys, own data."""

    def setUp(self):
        import os
        _copy_tenants()
        self.admin = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.inixr = Tenant.objects.get(slug="inixr")
        os.environ.pop("GEMINI_API_KEY", None)

    def _make_org(self):
        from core.orgs import create_organisation
        return create_organisation("Kids Maths Co", "ravi", "ravi@example.com", "Str0ng-Passw0rd!")

    def test_secret_is_encrypted_and_masked(self):
        from core.models import TenantSecret
        from core.secrets import get_secret, set_secret
        set_secret(self.inixr, "GEMINI_API_KEY", "AIzaSyTESTKEY123456")
        row = TenantSecret.objects.get(tenant=self.inixr)
        self.assertNotIn("TESTKEY", row.value_encrypted)
        self.assertEqual(row.hint, "AIza…3456")
        self.assertEqual(get_secret(self.inixr, "GEMINI_API_KEY"), "AIzaSyTESTKEY123456")

    def test_platform_key_only_for_allowed_orgs(self):
        import os
        from core.secrets import get_secret, is_dry_run
        tenant, _ = self._make_org()
        os.environ["GEMINI_API_KEY"] = "platform-key"
        try:
            self.assertEqual(get_secret(self.inixr, "GEMINI_API_KEY"), "platform-key")   # INIXR may use yours
            self.assertEqual(get_secret(tenant, "GEMINI_API_KEY"), "")                    # others may not
            self.assertTrue(is_dry_run(tenant))
            self.assertFalse(is_dry_run(self.inixr))
        finally:
            os.environ.pop("GEMINI_API_KEY", None)

    def test_new_org_gets_owner_and_default_team(self):
        tenant, owner = self._make_org()
        self.assertEqual(tenant.memberships.get(user=owner).role, "owner")
        self.assertEqual(AgentCard.objects.filter(tenant=tenant, is_current=True).count(), 4)
        self.assertTrue(AgentCard.objects.get(tenant=tenant, key="marketing").status == "active")

    def test_orgs_cannot_see_each_other(self):
        tenant, owner = self._make_org()
        self.client.force_login(owner)
        page = self.client.get("/").content.decode()
        self.assertIn("Kids Maths Co", page)
        self.assertNotIn("AlphaMagic", page)
        self.assertEqual(self.client.get("/products/alphamagic/").status_code, 404)
        self.assertEqual(self.client.get("/platform/").status_code, 302)   # not the platform admin

    def test_roles(self):
        from core.orgs import add_member
        tenant, owner = self._make_org()
        viewer, _ = add_member(tenant, "meena", "", "Str0ng-Passw0rd!", "viewer")
        reviewer, _ = add_member(tenant, "arun", "", "Str0ng-Passw0rd!", "reviewer")
        product = Product.objects.create(tenant=tenant, slug="maths", name="Maths Fun", status="live", brief="# b",
                                         config={"marketing": True})
        with override_settings(LLM_DRY_RUN=True):
            task = write_script(product)
        self.client.force_login(viewer)
        self.client.post(f"/tasks/{task.id}/decide/", {"decision": "approved"})
        task.refresh_from_db()
        self.assertEqual(task.status, "awaiting_approval")             # viewer cannot approve
        self.assertEqual(self.client.get("/settings/").status_code, 302)  # nor open settings
        self.client.force_login(reviewer)
        self.client.post(f"/tasks/{task.id}/decide/", {"decision": "approved"})
        task.refresh_from_db()
        self.assertEqual(task.status, "approved")
        self.assertEqual(self.client.get("/settings/").status_code, 302)  # reviewer: no settings
        self.client.force_login(owner)
        self.assertContains(self.client.get("/settings/"), "AI keys")

    def test_owner_saves_key_and_adds_member_in_settings(self):
        from unittest import mock
        from core.secrets import own_secret
        tenant, owner = self._make_org()
        self.client.force_login(owner)
        with mock.patch("dashboard.settings_views._test_gemini", return_value=(True, "The key works.")):
            r = self.client.post("/settings/", {"section": "keys", "GEMINI_API_KEY": "AIzaOWNKEY987654"}, follow=True)
        self.assertContains(r, "The key works")
        self.assertEqual(own_secret(tenant, "GEMINI_API_KEY"), "AIzaOWNKEY987654")
        self.assertNotContains(r, "AIzaOWNKEY987654")                    # never shown in full
        self.assertContains(r, "AIza…7654")
        self.client.post("/settings/", {"section": "member_add", "username": "priya", "email": "", "role": "reviewer",
                                        "password": "Str0ng-Passw0rd!"})
        self.assertEqual(tenant.memberships.get(user__username="priya").role, "reviewer")

    def test_platform_admin_creates_org(self):
        self.client.force_login(self.admin)
        r = self.client.post("/platform/", {"name": "Tiny Tales", "owner_username": "lakshmi", "owner_email": "",
                                            "password": "Str0ng-Passw0rd!"}, follow=True)
        self.assertContains(r, "Tiny Tales is ready")
        t = Tenant.objects.get(name="Tiny Tales")
        self.assertFalse(t.settings["use_platform_keys"])
        self.assertTrue(self.client.login(username="lakshmi", password="Str0ng-Passw0rd!"))


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA, TENANTS_DIR=TMP_TENANTS)
class LiveDuplicateTests(TestCase):
    def test_richer_copy_is_kept_even_if_newer(self):
        _copy_tenants()
        user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        t = Tenant.objects.create(slug="inixr", name="INIXR")
        Product.objects.create(tenant=t, slug="alpha-magic", name="Alpha Magic", status="live", brief="# x",
                               config={"marketing": True, "store_url": "https://play.google.com/store/apps/details?id=com.inixrtechnology.alphamagic"})
        load_all()  # AlphaMagic AR (with letter scenes) now has the HIGHER id
        self.client.force_login(user)
        page = self.client.get("/?p=all").content.decode()
        self.assertIn("“Alpha Magic” is a second copy of “AlphaMagic AR”", page)

    def test_second_live_copy_is_offered_for_archive(self):
        _copy_tenants()
        user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        t = Tenant.objects.get(slug="inixr")
        Product.objects.create(tenant=t, slug="alpha-magic", name="Alpha Magic", status="live", brief="# x",
                               config={"marketing": True, "store_url": "https://play.google.com/store/apps/details?id=com.inixrtechnology.alphamagic"})
        self.client.force_login(user)
        page = self.client.get("/?p=all").content.decode()
        self.assertIn("is a second copy of “AlphaMagic AR”", page)
        self.client.post("/products/alpha-magic/archive/", {"next": "home"})
        self.assertEqual(Product.objects.get(slug="alpha-magic").status, "archived")
        self.assertEqual(Product.objects.get(slug="alphamagic").status, "live")
        self.assertNotIn("second copy", self.client.get("/?p=all").content.decode())


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA)
class FailedShortTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.product = Product.objects.get(slug="alphamagic")
        self.client.force_login(self.user)

    def _failed_script(self):
        return Task.objects.create(tenant=self.product.tenant, product=self.product, agent_key="marketing",
                                   kind="short_script", title="Script: Watch the Magic", status="failed",
                                   result={"error": "model gone"})

    def test_remove_hides_failed_short(self):
        t = self._failed_script()
        self.assertContains(self.client.get("/"), "Try again")
        self.client.post(f"/shorts/{t.id}/action/", {"action": "hide"})
        self.assertNotContains(self.client.get("/"), "model gone")

    def test_retry_writes_a_new_script(self):
        t = self._failed_script()
        self.client.post(f"/shorts/{t.id}/action/", {"action": "retry"})
        self.assertTrue(Task.objects.filter(kind="short_script", status="awaiting_approval").exists())
        self.assertTrue(Task.objects.get(id=t.id).result.get("hidden"))


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA)
class RejectTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.product = Product.objects.get(slug="alphamagic")
        self.client.force_login(self.user)

    def test_reject_video_throws_it_away_without_redo(self):
        write_script(self.product)
        script = Task.objects.get(kind="short_script")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/tasks/{script.id}/decide/", {"decision": "approved"})
        video = Task.objects.get(kind="short_video")
        with self.captureOnCommitCallbacks(execute=True):
                r = self.client.post(f"/tasks/{video.id}/decide/", {"decision": "discarded", "reason": "too dark"},
                                 HTTP_HX_REQUEST="true")
        self.assertContains(r, "Rejected")
        self.assertEqual(Task.objects.filter(kind="short_video").count(), 1)   # no remake
        self.assertTrue(Task.objects.get(id=script.id).result.get("hidden"))    # gone from Today
        self.assertTrue(Rule.objects.filter(text="Avoid: too dark").exists())
        self.assertNotContains(self.client.get("/"), "Video to check")


@override_settings(LLM_DRY_RUN=True, JOBS_MODE="sync", MEDIA_ROOT=TMP_MEDIA)
class VideoDeleteTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser("nithy", "n@example.com", "pw")
        load_all()
        self.product = Product.objects.get(slug="alphamagic")
        self.client.force_login(self.user)
        write_script(self.product)
        script = Task.objects.get(kind="short_script")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/tasks/{script.id}/decide/", {"decision": "approved"})
        self.video = Task.objects.get(kind="short_video")
        self.folder = Path(TMP_MEDIA) / "videos" / "inixr" / str(self.video.id)

    def test_delete_approved_video_frees_disk(self):
        self.client.post(f"/tasks/{self.video.id}/decide/", {"decision": "approved"})
        self.assertTrue(self.folder.exists())
        self.assertContains(self.client.get("/videos/"), "Delete")
        self.client.post(f"/videos/{self.video.id}/delete/")
        self.assertFalse(self.folder.exists())
        self.assertNotContains(self.client.get("/videos/"), "Download")

    def test_cleanup_removes_rejected_keeps_waiting(self):
        self.client.post(f"/tasks/{self.video.id}/decide/", {"decision": "discarded"})
        self.assertContains(self.client.get("/videos/"), "Free up space")
        self.client.post("/videos/cleanup/")
        self.assertFalse(self.folder.exists())
