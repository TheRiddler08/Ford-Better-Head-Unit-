import tempfile
import unittest
from pathlib import Path
from home_server.coordinator import Coordinator, Conflict

class TestCoordinator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = Coordinator(Path(self.tmp.name) / "queue.sqlite3")

    def test_durable_small_changes(self):
        self.assertEqual(self.queue.state()["config"]["revision"], 0)
        self.queue.change("car", 0, {"theme":"day"})
        self.queue.change("car", 1, {"telemetry.pollSeconds":8})
        pending = Coordinator(self.queue.filename).pending()
        self.assertEqual([m["revision"] for m in pending["messages"]], [1,2])
        self.assertEqual(pending["messages"][1]["changes"]["telemetry.pollSeconds"], 8)

    def test_conflict_and_invalid_update(self):
        self.queue.change("car",0,{"theme":"day"})
        with self.assertRaises(Conflict):
            self.queue.change("car",0,{"units":"metric"})
        with self.assertRaises(ValueError):
            self.queue.change("car",1,{"os.exec":"bad"})
        self.assertEqual(self.queue.state()["config"]["revision"],1)

    def test_ack_verification_boundary(self):
        self.queue.change("car",0,{"theme":"day"})
        with self.assertRaises(ValueError):
            self.queue.record_authenticated_ack("car",2)
        self.queue.record_authenticated_ack("car",1)
        self.assertEqual(self.queue.pending()["messages"],[])
        with self.assertRaises(ValueError):
            self.queue.record_authenticated_ack("car",0)

if __name__ == "__main__":
    unittest.main()
