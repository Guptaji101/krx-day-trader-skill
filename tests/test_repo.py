import glob
import json
import os
import re
import unittest

import _load  # noqa: F401
import krx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = sorted(os.path.basename(d) for d in glob.glob(os.path.join(ROOT, "krx-*")) if os.path.isdir(d))


class Repo(unittest.TestCase):
    def test_eleven(self):
        self.assertEqual(SKILLS, ["krx-alerts", "krx-analyst", "krx-broker", "krx-budget", "krx-entry", "krx-exit",
                                  "krx-insider", "krx-news", "krx-risk", "krx-scanner", "krx-timemachine"])

    def test_skill_files(self):
        for s in SKILLS:
            with open(os.path.join(ROOT, s, "SKILL.md"), encoding="utf-8") as fh:
                text = fh.read()
            m = re.match(r"^---\nname: (\S+)\ndescription: >-\n(.+?)\n---\n", text, re.S)
            self.assertTrue(m, s)
            self.assertEqual(m.group(1), s)
            self.assertLess(len(" ".join(m.group(2).split())), 1024, s)
            for script in re.findall(r'CLAUDE_SKILL_DIR\}/(\w+\.py)', text):
                self.assertTrue(os.path.exists(os.path.join(ROOT, s, script)), (s, script))
            self.assertIn("한국어 요약", text)
            self.assertIn("PAPER ONLY", text)

    def test_no_us_leftovers(self):
        for path in glob.glob(os.path.join(ROOT, "krx-*", "*.py")):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for bad in ("trader/", "New_York", "nasdaqtrader", "sec.gov", "alpaca"):
                self.assertNotIn(bad, text, (path, bad))

    def test_manifests_and_config(self):
        for f in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json"):
            with open(os.path.join(ROOT, f)) as fh:
                doc = json.load(fh)
            self.assertIn("krx-day-trader-skill", json.dumps(doc))
        with open(os.path.join(ROOT, "config.example.json")) as fh:
            cfg = json.load(fh)
        self.assertEqual(set(cfg) - set(krx.DEFAULTS), set())
        self.assertTrue(cfg["paper"])
        self.assertEqual(cfg["dart_api_key"], "")


if __name__ == "__main__":
    unittest.main()
