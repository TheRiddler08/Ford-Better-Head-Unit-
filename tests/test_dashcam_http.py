import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from unittest.mock import patch
from app import server
from duet import proxy


class DashcamHTTPTests(unittest.TestCase):
    def test_duet_streams_range_without_buffering_full_recording(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);root=home/'.local/share/car-data/objects';root.mkdir(parents=True)
            name='a'*64+'.mp4';data=bytes(range(256))*4096
            (root/name).write_bytes(data);(root/(name+'.json')).write_text(json.dumps({'name':'front.mp4','size':len(data),'kind':'dashcam'}))
            config=home/'fleet.json';config.write_text(json.dumps({'home':str(home),'role':'tiny'}))
            http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            class Tablet(proxy.ProxyHandler):upstream_port=http.server_port
            tablet=ThreadingHTTPServer(('127.0.0.1',0),Tablet)
            threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in (http,tablet)]
            for thread in threads:thread.start()
            try:
                base='http://127.0.0.1:'+str(tablet.server_port)
                with patch.object(server,'FLEET_FILE',config),patch.object(proxy,'MAX_RESPONSE',32):
                    # Video is streamed independently of the ordinary response cap.
                    with urlopen(Request(base+'/api/dashcam/clip?id=tiny:'+name,headers={'Range':'bytes=1000-1999'})) as response:
                        self.assertEqual(response.status,206);self.assertEqual(response.read(),data[1000:2000]);self.assertEqual(response.headers['Content-Range'],'bytes 1000-1999/'+str(len(data)))
                    with urlopen(base+'/api/dashcam/clip?id=tiny:'+name) as response:self.assertEqual(response.read(),data)
            finally:
                for s in (tablet,http):s.shutdown();s.server_close()
                for thread in threads:thread.join()
