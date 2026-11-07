import json,os,subprocess,sys,tempfile,time,unittest,sqlite3,importlib.util
from pathlib import Path
from recorder.virtual_obd import Adapter,values
ROOT=Path(__file__).resolve().parents[1]

class VirtualOBD(unittest.TestCase):
    def test_standard_elm_commands_and_manual_values(self):
        data={'rpm':2000,'speed':90,'coolant':90,'voltage':14.1,'fuel':50,'load':40,'throttle':20,'intake':25}
        adapter=Adapter(lambda:data)
        self.assertIn(b'ELM327',adapter.command('ATZ'))
        adapter.command('ATE0');adapter.command('ATH1')
        self.assertIn(b'7E8 04 41 0C 1F 40',adapter.command('010C'))
        self.assertIn(b'41 0D 5A',adapter.command('010D'))
        self.assertIn(b'41 00',adapter.command('0100'))
        self.assertIn(b'NO DATA',adapter.command('0902'))
        data['speed']=20;self.assertIn(b'41 0D 14',adapter.command('010D'))
    def test_invalid_manual_values_do_not_become_wire_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'values.json'
            for bad in ({'rpm':-1},{'speed':True},{'voltage':float('nan')},{'shell':'bad'}):
                path.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):values(path)
    @unittest.skipUnless(importlib.util.find_spec('obd'),'Optional end-to-end test needs python-OBD')
    def test_unchanged_recorder_records_serial_input_and_manual_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'values.json';state.write_text(json.dumps({'speed':50,'rpm':1800}))
            emulator=subprocess.Popen([sys.executable,str(ROOT/'recorder/virtual_obd.py'),'--values',str(state),'--seconds','60'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
            recorder=None
            try:
                line=emulator.stdout.readline();port=line.strip().split(': ',1)[1]
                env={**os.environ,'CAR_DB':str(root/'obd.sqlite3'),'OBD_PORT':port,'OBD_RECORDER_LOCK':str(root/'recorder.lock'),'CAR_OVERRIDE_DIR':str(root/'overrides')}
                recorder=subprocess.Popen([sys.executable,str(ROOT/'recorder/obd_full_recorder.py')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                deadline=time.monotonic()+40;changed=False
                while time.monotonic()<deadline:
                    try:
                        with sqlite3.connect(root/'obd.sqlite3') as db:
                            rows=db.execute("SELECT number FROM readings WHERE name='SPEED' ORDER BY rowid DESC LIMIT 1").fetchall()
                        if rows and rows[0][0]==50 and not changed:state.write_text(json.dumps({'speed':80,'rpm':2200}));changed=True
                        if changed and rows and rows[0][0]==80:return
                    except sqlite3.Error:pass
                    time.sleep(.2)
                self.fail('Recorder did not consume manual virtual serial changes')
            finally:
                for process in (recorder,emulator):
                    if process and process.poll() is None:process.terminate()
                for process in (recorder,emulator):
                    if process:
                        try:process.wait(timeout=8)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
                emulator.stdout.close()
