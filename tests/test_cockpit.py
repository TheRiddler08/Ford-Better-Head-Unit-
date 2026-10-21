"""Static regression contract for the infotainment dashboard."""
import importlib.util
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/"app/web/cockpit.html").read_text(encoding="utf-8")
JS=(ROOT/"app/web/cockpit.js").read_text(encoding="utf-8")
CSS=(ROOT/"app/web/cockpit.css").read_text(encoding="utf-8")

class Index(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids=[]
        self.views=[]
        self.sources=[]
    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if "id" in d:self.ids.append(d["id"])
        if "data-view" in d:self.views.append(d["data-view"])
        if tag=="script" and "src" in d:self.sources.append(d["src"])

class CockpitTests(unittest.TestCase):
    def test_dom_integrity(self):
        p=Index();p.feed(HTML)
        self.assertEqual(len(p.ids),len(set(p.ids)))
        references=set(re.findall(r'\$\(["\\x27]([a-zA-Z][a-zA-Z0-9_-]+)["\\x27]\)',JS))
        self.assertEqual(references-set(p.ids),set())
    def test_page_structure(self):
        for name in ("home","navigation","media","vehicle","diagnostics","settings","updates"):
            self.assertIn('id="view-'+name+'"',HTML)
        self.assertIn('data-widget="clock"',HTML)
        self.assertIn('data-widget="gauges"',HTML)
    def test_offline_local_resources(self):
        p=Index();p.feed(HTML)
        self.assertEqual(p.sources,["/cockpit.js"])
        self.assertIn('href="/cockpit.css"',HTML)
        self.assertNotIn("https://",HTML)
        self.assertIn("@media(max-width:690px)",CSS)
    def test_safe_interface(self):
        self.assertIn("NO ROUTE",JS)
        self.assertIn("RECENT DATA",JS)
        self.assertIn("refreshRemoteConfiguration",JS)
        self.assertIn("data-unavailable",HTML)
        self.assertIn("updateRadio",HTML)

if __name__=="__main__":
    unittest.main()
