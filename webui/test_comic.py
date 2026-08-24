#!/usr/bin/env python3
"""Unit & integration tests for Comic pipeline."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Use a separate test DB
os.environ["DRAMA_TEST_DB"] = os.path.join(tempfile.gettempdir(), "drama_test_comic.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database
database.DB_PATH = os.environ["DRAMA_TEST_DB"]
try:
    os.remove(database.DB_PATH)
except OSError:
    pass
database.init_db()

from database import (
    init_db, create_project, get_project,
    add_character, get_characters,
    add_chapter, get_chapters, get_chapter, delete_chapter,
    add_comic_panel, add_comic_panels_batch,
    update_comic_panel, update_comic_panel_image,
    get_comic_panels, get_comic_panel,
    delete_comic_panels, get_comic_episodes,
)


class TestComicDatabase(unittest.TestCase):
    """Comic panel CRUD operations."""

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.pid = create_project("漫画测试项目", "历史", "三国漫画测试")

    def test_add_and_get_panel(self):
        panel_id = add_comic_panel(
            self.pid, panel_number=1, episode_number=1,
            scene_desc="关羽在城楼上远眺", dialogue="[关羽]: 大哥，前方有埋伏！",
            sfx="唰——", camera="远景"
        )
        self.assertGreater(panel_id, 0)

        panel = get_comic_panel(panel_id)
        self.assertIsNotNone(panel)
        self.assertEqual(panel["project_id"], self.pid)
        self.assertEqual(panel["panel_number"], 1)
        self.assertEqual(panel["scene_desc"], "关羽在城楼上远眺")
        self.assertEqual(panel["dialogue"], "[关羽]: 大哥，前方有埋伏！")
        self.assertEqual(panel["sfx"], "唰——")
        self.assertEqual(panel["camera"], "远景")
        self.assertEqual(panel["status"], "pending")

    def test_add_panels_batch(self):
        panels = [
            {"project_id": self.pid, "panel_number": 1, "episode_number": 1,
             "scene_desc": "测试面板1", "camera": "中景"},
            {"project_id": self.pid, "panel_number": 2, "episode_number": 1,
             "scene_desc": "测试面板2", "dialogue": "[旁白]: 测试", "camera": "特写"},
            {"project_id": self.pid, "panel_number": 3, "episode_number": 1,
             "scene_desc": "测试面板3", "sfx": "轰！", "camera": "全景"},
        ]
        ids = add_comic_panels_batch(panels)
        self.assertEqual(len(ids), 3)

        all_panels = get_comic_panels(self.pid)
        self.assertEqual(len(all_panels), 3)

    def test_update_panel(self):
        panel_id = add_comic_panel(self.pid, panel_number=1, episode_number=1, scene_desc="初始描述")
        update_comic_panel(panel_id, scene_desc="修改后的描述", dialogue="新对白", status="generating")
        panel = get_comic_panel(panel_id)
        self.assertEqual(panel["scene_desc"], "修改后的描述")
        self.assertEqual(panel["dialogue"], "新对白")
        self.assertEqual(panel["status"], "generating")

    def test_update_panel_image(self):
        panel_id = add_comic_panel(self.pid, panel_number=1, episode_number=1)
        update_comic_panel_image(panel_id, image_local="/tmp/test.png", seed=12345)
        panel = get_comic_panel(panel_id)
        self.assertEqual(panel["image_local"], "/tmp/test.png")
        self.assertEqual(panel["seed"], 12345)
        self.assertEqual(panel["status"], "done")

    def test_get_panels_by_episode(self):
        add_comic_panel(self.pid, panel_number=1, episode_number=1, scene_desc="话1")
        add_comic_panel(self.pid, panel_number=2, episode_number=1, scene_desc="话1")
        add_comic_panel(self.pid, panel_number=1, episode_number=2, scene_desc="话2")

        ep1 = get_comic_panels(self.pid, episode_number=1)
        self.assertEqual(len(ep1), 2)

        ep2 = get_comic_panels(self.pid, episode_number=2)
        self.assertEqual(len(ep2), 1)

        all_panels = get_comic_panels(self.pid)
        self.assertEqual(len(all_panels), 3)

    def test_get_comic_episodes(self):
        add_comic_panel(self.pid, panel_number=1, episode_number=1, scene_desc="话1")
        add_comic_panel(self.pid, panel_number=2, episode_number=1, scene_desc="话1")
        add_comic_panel(self.pid, panel_number=1, episode_number=2, scene_desc="话2")
        add_comic_panel(self.pid, panel_number=2, episode_number=2, scene_desc="话2")

        # Mark some as done
        panels = get_comic_panels(self.pid)
        update_comic_panel_image(panels[0]["id"], "/tmp/a.png", status="done")
        update_comic_panel_image(panels[2]["id"], "/tmp/b.png", status="done")

        episodes = get_comic_episodes(self.pid)
        self.assertEqual(len(episodes), 2)

        ep1 = [e for e in episodes if e["episode_number"] == 1][0]
        self.assertEqual(ep1["panel_count"], 2)
        self.assertEqual(ep1["done_count"], 1)

        ep2 = [e for e in episodes if e["episode_number"] == 2][0]
        self.assertEqual(ep2["panel_count"], 2)
        self.assertEqual(ep2["done_count"], 1)

    def test_delete_panels(self):
        add_comic_panel(self.pid, panel_number=1, episode_number=1)
        add_comic_panel(self.pid, panel_number=2, episode_number=1)
        add_comic_panel(self.pid, panel_number=1, episode_number=2)

        # Delete episode 1 only
        delete_comic_panels(self.pid, episode_number=1)
        remaining = get_comic_panels(self.pid)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["episode_number"], 2)

        # Delete all
        delete_comic_panels(self.pid)
        self.assertEqual(len(get_comic_panels(self.pid)), 0)

    def test_panel_with_chapter(self):
        # Create a chapter
        cid = add_chapter(self.pid, chapter_number=1, title="桃园结义",
                          content="刘备关羽张飞的相遇...", summary="三英雄结义")
        # Create panel linked to chapter
        panel_id = add_comic_panel(
            self.pid, panel_number=1, episode_number=1,
            chapter_id=cid, scene_desc="桃园三结义"
        )
        panel = get_comic_panel(panel_id)
        self.assertEqual(panel["chapter_id"], cid)

    def test_page_number_ordering(self):
        add_comic_panel(self.pid, panel_number=1, episode_number=1, page_number=1)
        add_comic_panel(self.pid, panel_number=2, episode_number=1, page_number=1)
        add_comic_panel(self.pid, panel_number=3, episode_number=1, page_number=2)
        add_comic_panel(self.pid, panel_number=4, episode_number=1, page_number=2)

        panels = get_comic_panels(self.pid)
        # Should be ordered by page_number, panel_number
        self.assertEqual(panels[0]["page_number"], 1)
        self.assertEqual(panels[0]["panel_number"], 1)
        self.assertEqual(panels[3]["page_number"], 2)

    def test_default_values(self):
        panel_id = add_comic_panel(self.pid, panel_number=1)
        panel = get_comic_panel(panel_id)
        self.assertEqual(panel["episode_number"], 1)
        self.assertEqual(panel["page_number"], 1)
        self.assertEqual(panel["camera"], "中景")
        self.assertEqual(panel["model"], "GuoFeng3.4")
        self.assertEqual(panel["status"], "pending")
        self.assertEqual(panel["dialogue"], "")
        self.assertEqual(panel["sfx"], "")


class TestComicPipeline(unittest.TestCase):
    """Comic pipeline functions (no API calls needed)."""

    def test_check_comfyui_ready(self):
        from pipelines.comic_gen import check_comfyui_ready
        ready, msg = check_comfyui_ready()
        # ComfyUI may or may not be available — either way function returns
        self.assertIsInstance(ready, bool)
        self.assertIsInstance(msg, str)

    def test_build_comfyui_workflow(self):
        from pipelines.comic_gen import _build_comfyui_workflow
        wf = _build_comfyui_workflow("test prompt", "negative", 42, 600, 900)
        self.assertIsInstance(wf, dict)
        self.assertIn("3", wf)  # KSampler
        self.assertIn("4", wf)  # CheckpointLoader
        self.assertIn("5", wf)  # EmptyLatentImage
        self.assertIn("6", wf)  # CLIPTextEncode positive
        self.assertIn("7", wf)  # CLIPTextEncode negative
        self.assertIn("8", wf)  # VAEDecode
        self.assertIn("9", wf)  # SaveImage

    def test_workflow_dimensions(self):
        from pipelines.comic_gen import _build_comfyui_workflow
        wf = _build_comfyui_workflow("test", "neg", 1, 800, 1200)
        latent = wf["5"]["inputs"]
        self.assertEqual(latent["width"], 800)
        self.assertEqual(latent["height"], 1200)

    def test_workflow_seed(self):
        from pipelines.comic_gen import _build_comfyui_workflow
        wf = _build_comfyui_workflow("test", "neg", 99999, 600, 900)
        self.assertEqual(wf["3"]["inputs"]["seed"], 99999)

    def test_workflow_random_seed(self):
        from pipelines.comic_gen import _build_comfyui_workflow
        wf1 = _build_comfyui_workflow("test", "neg", None, 600, 900)
        wf2 = _build_comfyui_workflow("test", "neg", None, 600, 900)
        # Random seeds should likely differ
        self.assertIsNotNone(wf1["3"]["inputs"]["seed"])
        self.assertIsNotNone(wf2["3"]["inputs"]["seed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)