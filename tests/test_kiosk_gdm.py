"""GDM configuration tests; no root or graphical login required."""
import importlib.util
import unittest
from pathlib import Path

FILE=Path(__file__).resolve().parents[1]/"deploy"/"kiosk-gdm.py"
spec=importlib.util.spec_from_file_location("kiosk_gdm",FILE)
gdm=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gdm)

class GdmKioskTests(unittest.TestCase):
    SRC="# GDM file\n[daemon]\n#WaylandEnable=false\nWaylandEnable=false\nAutomaticLoginEnable=false\nAutomaticLogin=other\n[security]\nEnable=true\n"
    def test_enable(self):
        result=gdm.render_enable(self.SRC,"cayden")
        self.assertIn("AutomaticLoginEnable=true",result)
        self.assertIn("AutomaticLogin=cayden",result)
        self.assertIn("WaylandEnable=false",result)
    def test_idempotent(self):
        once=gdm.render_enable(self.SRC,"cayden")
        self.assertEqual(once,gdm.render_enable(once,"cayden"))
    def test_reverse_exact(self):
        self.assertEqual(self.SRC,gdm.render_disable(gdm.render_enable(self.SRC,"cayden")))
    def test_leave_other_section(self):
        src="[daemon]\nWaylandEnable=false\n[security]\nAutomaticLogin=other\n"
        self.assertEqual(src,gdm.render_disable(gdm.render_enable(src,"cayden")))
    def test_new_daemon(self):
        src="# file\n[security]\nEnable=true\n"
        self.assertIn("[daemon]",gdm.render_enable(src,"cayden"))
    def test_bad_username(self):
        with self.assertRaises(ValueError):
            gdm.render_enable(self.SRC,"bad\nAutomaticLogin=root")

if __name__=="__main__":
    unittest.main()
