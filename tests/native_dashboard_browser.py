"""Optional real-browser companion to native_offline_startup.py's hold mode.

Run only against its disposable fixture, never an owner's authentication files.
The browser withholds recorder/info to prove database readiness cannot hide the UI.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture',type=Path)
    parser.add_argument('--classic',action='store_true')
    parser.add_argument('--url',default='http://127.0.0.1:18123')
    parser.add_argument('--browser',default='/usr/bin/chromium')
    parser.add_argument('--screenshot',default='/tmp/car-dashboard-first.png')
    args=parser.parse_args()
    if urlsplit(args.url).hostname not in ('127.0.0.1','localhost'):
        raise ValueError('Disposable loopback fixture required')
    tokens=json.loads(args.fixture.read_text())
    tokens.update(hassUrl=args.url,clientId=args.url+'/',expires=time.time()*1000+3600000)
    completed=False
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,
                args=['--no-sandbox','--disable-dev-shm-usage'])
            context=browser.new_context(viewport={'width':1280,'height':800},service_workers='block')
            context.add_init_script('localStorage.setItem("hassTokens", '+json.dumps(json.dumps(tokens))+');')
            # No external images/maps/brands should be needed for the car layout.
            context.route('**/*',lambda route:route.continue_() if urlsplit(route.request.url).hostname in ('127.0.0.1','localhost') else route.abort())
            page=context.new_page()
            requests=[]
            def route_websocket(route):
                server=route.connect_to_server()
                def send(message):
                    payload=json.loads(message)
                    requests.append(payload.get('type'))
                    if payload.get('type')!='recorder/info':server.send(message)
                route.on_message(send)
                server.on_message(route.send)
            page.route_web_socket('**/api/websocket',route_websocket)
            page.goto(args.url+('/drive-console' if args.classic else '/car-starter'),wait_until='domcontentloaded')
            page.locator('hui-root').wait_for(timeout=30000)
            page.locator('drive-console-widget' if args.classic else 'hui-gauge-card').first.wait_for(timeout=10000)
            page.wait_for_function('!document.getElementById("ha-launch-screen")',timeout=10000)
            if not args.classic:assert page.locator('hui-root').get_by_text('Offline editable drive',exact=True).count()>0
            marker=page.evaluate('performance.getEntriesByName("car-dashboard-visible").map(e=>e.startTime)')
            assert marker, 'Dashboard visibility milestone missing'
            # Fresh disposable HTTP settings require the native safety
            # confirmation. This does not change the production auth policy.
            confirmation=page.locator('dialog-http-pending-config')
            confirmation.get_by_role('button',name='Confirm',exact=True).wait_for(state='visible',timeout=10000)
            confirmation.get_by_role('button',name='Confirm',exact=True).click()
            confirmation.get_by_role('button',name='Confirm',exact=True).wait_for(state='hidden',timeout=10000)
            page.screenshot(path=args.screenshot)
            # Exercise the actual native dashboard editor, not just a WS save.
            if args.classic:
                page.locator('drive-console-layout').get_by_role('button',name='Edit dashboard',exact=True).click(timeout=10000)
            else:page.get_by_role('button',name='Edit dashboard',exact=True).click(timeout=10000)
            page.get_by_role('button',name='Done',exact=True).wait_for(timeout=10000)
            if args.classic:
                page.locator('hui-masonry-view').wait_for(timeout=10000)
                page.screenshot(path=args.screenshot.replace('.png','-editor.png'))
                page.locator('hui-card-options').filter(has=page.locator('drive-console-widget[data-widget=clock]')).get_by_role('button',name='Edit',exact=True).click()
                editor=page.locator('drive-console-widget-editor')
                editor.get_by_role('textbox',name='Heading',exact=True).fill('MY TEST DRIVE')
                page.get_by_role('button',name='Save',exact=True).click()
                page.get_by_role('button',name='Done',exact=True).click()
                page.locator('drive-console-widget').get_by_text('MY TEST DRIVE',exact=True).wait_for()
                page.reload(wait_until='domcontentloaded')
                page.locator('drive-console-widget').get_by_text('MY TEST DRIVE',exact=True).wait_for(timeout=10000)
                layout=page.locator('drive-console-layout')
                for name,path in [('Navigation','navigation'),('Media','media'),('Vehicle','vehicle'),('Health','health'),('Updates','updates'),('Settings','settings')]:
                    layout.get_by_role('button',name=name,exact=True).click()
                    page.wait_for_url('**/drive-console/'+path)
                    page.locator('drive-console-layout').get_by_role('button',name=name,exact=True).wait_for()
                layout.get_by_role('button',name='Navigation',exact=True).click()
                page.wait_for_url('**/drive-console/navigation')
                layout.get_by_role('button',name='Edit dashboard',exact=True).click()
                page.locator('hui-card-options').filter(has=page.locator('drive-console-widget[data-widget=map]')).get_by_role('button',name='Edit',exact=True).click()
                page.locator('drive-console-widget-editor').get_by_role('textbox',name='Heading',exact=True).fill('NAVIGATION ONLY')
                page.get_by_role('button',name='Save',exact=True).click()
                page.locator('hui-card-options').filter(has=page.locator('drive-console-widget[data-widget=page]')).get_by_role('button',name='Edit',exact=True).click()
                page.locator('drive-console-widget-editor').get_by_text('Page colors and typography',exact=True).click()
                page.locator('drive-console-widget-editor').get_by_role('textbox',name='Background color',exact=True).fill('#142238')
                page.get_by_role('button',name='Save',exact=True).click()
                page.get_by_role('button',name='Done',exact=True).click()
                layout.get_by_text('NAVIGATION ONLY',exact=True).wait_for()
                assert layout.evaluate("el=>el.style.getPropertyValue('--bg')")=='#142238'
                page.reload(wait_until='domcontentloaded')
                layout.get_by_text('NAVIGATION ONLY',exact=True).wait_for(timeout=10000)
                assert layout.evaluate("el=>el.style.getPropertyValue('--bg')")=='#142238'
                layout.get_by_role('button',name='Home',exact=True).click()
                page.wait_for_url('**/drive-console/drive')
                layout.get_by_text('MY TEST DRIVE',exact=True).wait_for()
                assert layout.get_by_text('NAVIGATION ONLY',exact=True).count()==0
                assert layout.evaluate("el=>el.style.getPropertyValue('--bg')")==''
                page.screenshot(path=args.screenshot.replace('.png','-tabs.png'))


            assert 'recorder/info' not in requests, 'Startup still waits on the recorder'
            print(f'PASS: saved native dashboard visible in {marker[0]/1000:.2f}s; native editor opened with recorder/info withheld')
            browser.close()
            completed=True
    finally:
        if completed:(args.fixture.parent/'browser-finished').touch()


if __name__=='__main__':main()
