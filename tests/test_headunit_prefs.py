"""Validation tests for locally saved vehicle info and offline commute schedules."""
import copy
import tempfile
import unittest
from pathlib import Path
from app import headunit_prefs as prefs

class HeadUnitPrefsTests(unittest.TestCase):
    def sample(self):
        data=copy.deepcopy(prefs.DEFAULT)
        data["destinations"].append({"id":"work","name":"Work","lat":39.65,"lon":-78.76,"usualMinutes":25})
        data["routines"].append({"id":"weekday","name":"Morning commute","destinationId":"work",
                                  "originId":None,"time":"07:30","days":[0,1,2,3,4]})
        return data

    def test_schedule_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"prefs.json"
            self.assertEqual(prefs.read(path),prefs.DEFAULT)
            data=self.sample()
            data["revision"]=1
            self.assertEqual(prefs.update(path,data),data)
            self.assertEqual(prefs.read(path),data)

    def test_prevent_stale_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"prefs.json"
            data=self.sample()
            data["revision"]=1
            prefs.update(path,data)
            with self.assertRaises(ValueError):
                prefs.update(path,data)

    def test_invalid_values(self):
        for field,value in (
            ("lat", 91),("lat",float("nan")),("usualMinutes", -1),
        ):
            with self.subTest(field=field,value=value):
                data=self.sample()
                data["destinations"][0][field]=value
                with self.assertRaises(ValueError):
                    prefs.validate(data)
        for edit in (
            lambda d: d["routines"][0].update(destinationId="missing"),
            lambda d: d["routines"][0].update(time="25:99"),
            lambda d: d["routines"][0].update(days=[0,0]),
            lambda d: d["vehicle"].update(nickname="<script>"),
        ):
            data=self.sample()
            edit(data)
            with self.assertRaises(ValueError):
                prefs.validate(data)

if __name__=="__main__":
    unittest.main()
