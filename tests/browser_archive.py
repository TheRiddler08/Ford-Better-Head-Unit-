"""Optional real-browser archive test; requires Playwright and Chromium."""
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import server
from recorder import gps_recorder as gps, obd_full_recorder as obd
from playwright.sync_api import sync_playwright, expect
from duet import proxy

with tempfile.TemporaryDirectory() as temp:
    root=Path(temp);server.DB_PATH=root/'vehicle.sqlite3';server.GPS_DB_PATH=root/'gps.sqlite3'
    db=obd.open_db(server.DB_PATH)
    db.executemany('INSERT INTO readings VALUES(?,?,?,?,?)',[(1000+i,'RPM',str(i),i,'rpm') for i in range(1203)])
    db.commit();db.close()
    db=gps.open_db(server.GPS_DB_PATH)
    for i in range(2105):
        ts=1793100000+i
        gps.record_message(db,{'class':'TPV','mode':3,'lat':40+i*.00001,'lon':-74+i*.000015,
            'time':datetime.fromtimestamp(ts,timezone.utc).isoformat(),'speed':1.5},received=ts)
    db.close()
    http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    proxy.ProxyHandler.upstream_port=http.server_port
    tablet=ThreadingHTTPServer(('127.0.0.1',0),proxy.ProxyHandler)
    tablet_thread=threading.Thread(target=tablet.serve_forever,daemon=True);tablet_thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:'+str(tablet.server_port)+'/records')
            expect(page.locator('#recordRows tr')).to_have_count(500)
            page.locator('#moreRecords').click();expect(page.locator('#recordRows tr')).to_have_count(1000)
            page.locator('#recordRows tr').first.click();expect(page.locator('#recordJson')).to_contain_text('RPM');page.locator('#closeDetail').click()
            proxy.MAX_RESPONSE=32  # Prove export does not use the buffered response cap.
            with page.expect_download() as downloaded:page.locator('#exportCsv').click()
            file=root/'all.csv';downloaded.value.save_as(file)
            with file.open() as stream:assert len(list(csv.reader(stream)))==1204
            proxy.MAX_RESPONSE=16*1024*1024
            page.locator('#loadRoute').click();expect(page.locator('#routeStatus')).to_contain_text('2,000 actual GPS fixes loaded')
            expect(page.locator('#routeStatus')).to_contain_text('partial history')
            page.locator('#moreRoute').click();expect(page.locator('#routeStatus')).to_contain_text('2,105 actual GPS fixes loaded')
            expect(page.locator('#routePaths path')).to_have_count(1)
            assert page.locator('#routePaths path').get_attribute('d').count('L')==2104
            page.locator('#routeMap').click(position={'x':150,'y':140});expect(page.locator('#fixDetail')).to_contain_text('lat')
            page.locator('#recordKind').select_option('gps');expect(page.locator('#recordRows tr')).to_have_count(500)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
            print('PASS: 1,203 unaggregated values, complete CSV, 2,105 GPS vertices, point inspection, mobile layout')
    finally:
        tablet.shutdown();tablet.server_close();tablet_thread.join()
        http.shutdown();http.server_close();thread.join()
