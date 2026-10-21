"""Static touchscreen UI contract checks; stdlib only, no vehicle hardware."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1] / "app" / "web"

class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.nav = []
        self.scripts = []
        self.styles = []
        self.buttons = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "button":
            self.buttons.append(attrs)
            if "data-page" in attrs:
                self.nav.append(attrs["data-page"])
        if tag == "script":
            self.scripts.append(attrs.get("src", ""))
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs.get("href", ""))

class TouchscreenContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "styles.css").read_text(encoding="utf-8")
        cls.document = Document()
        cls.document.feed(cls.html)

    def test_all_static_js_element_lookups_exist(self):
        # JavaScript uses $('elementId') to read and modify the existing DOM.
        referenced = set(re.findall(r"\$\(['\"]([A-Za-z][A-Za-z0-9_-]+)['\"]\)", self.js))
        missing = referenced - set(self.document.ids)
        self.assertEqual(missing, set(), "JavaScript refers to missing elements")

    def test_html_id_uniqueness(self):
        self.assertEqual(len(self.document.ids), len(set(self.document.ids)))

    def test_pages_exist_and_are_navigable(self):
        expected = {"overview", "sensors", "trips", "diagnostics"}
        self.assertEqual(set(self.document.nav), expected)
        for name in expected:
            self.assertIn("page-" + name, self.document.ids)

    def test_touchscreen_gauges_and_stale_data(self):
        expected = {"speedArc", "rpmArc", "speedValue", "rpmValue", "coolantValue",
                    "voltageValue", "fuelValue", "fuelBar", "statusPill",
                    "engineLoadValue", "intakeTempValue"}
        self.assertFalse(expected - set(self.document.ids))
        self.assertIn("data.fresh", self.js)
        self.assertIn("data-stale", self.js)

    def test_local_resources_only(self):
        self.assertEqual(self.document.scripts, ["/app.js"])
        self.assertEqual(self.document.styles, ["/styles.css"])
        self.assertNotIn("https://", self.html)
        self.assertNotIn("http://", self.html)

    def test_touchscreen_responsive_and_theme(self):
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn('data-theme="day"', self.css)
        self.assertIn("@media(max-width:640px)", self.css)
        self.assertIn("requestFullscreen", self.js)
        self.assertIn("car-dashboard-theme", self.js)

if __name__ == "__main__":
    unittest.main()
