"""Offline transport, configuration, and update tests. No actual radio required."""
import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

from control import config_core as config
from control import lorawan_protocol as wire

KEY=bytes(range(32))

class ConfigTests(unittest.TestCase):
    def test_valid_changes_and_diffs(self):
        base=copy.deepcopy(config.DEFAULT)
        desired=config.apply_changes(base,1,{"home.map.visible":False,"theme":"day",
                                              "labels.dashboard":"Drive System",
                                              "telemetry.pollSeconds":5})
        self.assertEqual(desired["revision"],1)
        self.assertFalse(desired["home"]["map"]["visible"])
        self.assertEqual(config.diff(base,desired),
            {"home.map.visible":False,"theme":"day","labels.dashboard":"Drive System",
             "telemetry.pollSeconds":5})

    def test_block_unknown_or_executable_fields(self):
        for field,value in [("home.map.inject","<script>alert(1)</script>"),
                            ("revision",100),("telemetry.pollSeconds",0),
                            ("nav.home",False),("labels.dashboard","\n")]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    config.apply_changes(config.DEFAULT,1,{field:value})

    def test_replay_and_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"config.json"
            original=config.read_config(path)
            self.assertEqual(original["revision"],0)
            new=config.apply_changes(original,1,{"home.media.order":1})
            config.write_config(path,new)
            self.assertEqual(config.read_config(path),new)
            with self.assertRaises(ValueError):
                config.apply_changes(new,1,{"theme":"day"})

class RadioTests(unittest.TestCase):
    def test_compact_frames_round_trip_and_missing(self):
        message={"revision":1,"changes":{"home.map.visible":False,
                   "labels.dashboard":"Small radio update"}}
        frames=wire.split_message("configuration",1,message,KEY)
        self.assertGreater(len(frames),1)
        self.assertLessEqual(max(map(len,frames)),46)
        agent=wire.Assembler(KEY)
        for f in frames[1:]:
            self.assertIsNone(agent.accept(f))
        need=agent.missing(1)
        self.assertEqual(need["missing"],[0])
        done=agent.accept(frames[0])
        self.assertEqual(done["payload"],message)
        self.assertEqual(done["kind"],"configuration")
        with self.assertRaises(ValueError):
            agent.accept(frames[0])

    def test_bad_key_or_tampered_frame_rejected(self):
        frames=wire.split_message("configuration",6,{"revision":6,"changes":{"theme":"day"}},KEY)
        receiver=wire.Assembler(bytes(reversed(KEY)))
        with self.assertRaises(ValueError):
            receiver.accept(frames[0])
        receiver=wire.Assembler(KEY)
        damaged=frames[0][:-1]+bytes([frames[0][-1]^1])
        with self.assertRaises(ValueError):
            receiver.accept(damaged)

    def test_frame_size_bound(self):
        with self.assertRaises(ValueError):
            wire.split_message("configuration",2,{"x":"y"},KEY,chunk_size=100)
        with self.assertRaises(ValueError):
            wire.split_message("ui-patch",2,{"x":"A"*60000},KEY)

class AgentTests(unittest.TestCase):
    def test_receive_delta_with_persistent_state_and_ack(self):
        from control.car_agent import load_agent_state,new_assembler,process_frame
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            state=root/"agent.json";carconfig=root/"car.json"
            message={"revision":1,"changes":{"home.clock.visible":False}}
            frames=wire.split_message("configuration",1,message,KEY)
            receiver=new_assembler(KEY,load_agent_state(state))
            for f in frames[:-1]:
                self.assertEqual(process_frame(receiver,f,state,carconfig,KEY),[])
            receiver=new_assembler(KEY,load_agent_state(state))
            answers=process_frame(receiver,frames[-1],state,carconfig,KEY)
            self.assertEqual(config.read_config(carconfig)["revision"],1)
            home=wire.Assembler(KEY)
            last=None
            for a in answers:
                last=home.accept(a)
            self.assertEqual(last["payload"]["applied"],True)
            self.assertEqual(load_agent_state(state)["last_sequence"],1)

if __name__=="__main__":
    unittest.main()
