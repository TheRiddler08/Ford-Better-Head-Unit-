"""Browser coverage of GPS-independent planning, persistence, manual trips and replay."""
from pathlib import Path
import sys,base64
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.home_assistant import classic_resource
from playwright.sync_api import sync_playwright,expect
module,_=classic_resource(Path(__file__).resolve().parents[1])
html='''<script type="module">
import {defaultNavigation} from '/module.js';
window.savedNavigation=JSON.parse(localStorage.getItem('nav')||'null')||defaultNavigation();
window.h={connected:true,states:{'device_tracker.car_location':{state:'unavailable',attributes:{}}},fetchWithAuth:async(path,options={})=>{
 if(path==='/api/car_console/navigation'){if(options.method==='POST'){const data=JSON.parse(options.body);if(data.revision!==savedNavigation.revision+1)return new Response(JSON.stringify({error:'Conflict'}),{status:409});window.savedNavigation=data;localStorage.setItem('nav',JSON.stringify(data));}return Response.json(savedNavigation);}
 if(path.startsWith('/api/car_console/gps-history'))return Response.json({items:[{lat:39.5,lon:-98.35,ts:1794070000,segment:1},{lat:39.51,lon:-98.34,ts:1794070060,segment:1},{lat:39.52,lon:-98.33,ts:1794070120,segment:2,break_reason:'gap'},{lat:39.53,lon:-98.32,ts:1794070180,segment:2}],next:4,hasMore:false});
 if(path==='/api/car_console/route')return Response.json({error:'Local routing pack not installed'},{status:503});
 return new Response('',{status:404});}};
const card=document.createElement('drive-console-widget');card.style.cssText='display:block;width:900px;height:800px';card.setConfig({widget:'map'});document.body.append(card);card.hass=h;window.card=card;
</script>'''
png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1100,'height':1000},has_touch=True)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.route('http://127.0.0.1:19997/**',lambda r:r.fulfill(body=module if r.request.url.endswith('module.js') else html,content_type='text/javascript' if r.request.url.endswith('module.js') else 'text/html'))
    page.route('https://basemap.nationalmap.gov/**',lambda r:r.fulfill(body=png,content_type='image/png',headers={'Access-Control-Allow-Origin':'*'}))
    page.goto('http://127.0.0.1:19997/')
    expect(page.locator('#mapModeText')).to_contain_text('GPS disconnected')
    expect(page.locator('#mapTiles img').first).to_be_visible()
    expect(page.locator('#mapEmpty')).to_be_hidden()
    page.get_by_text('Places, routes & trips',exact=True).click()
    # Numeric coordinates and markers without any GPS fix.
    page.locator('#navLat').fill('40.0');page.locator('#navLon').fill('-100.0')
    page.locator('#navSelectCoordinate').click();page.locator('#navName').fill('Custom marker')
    page.locator('#navAddMarker').click()
    expect(page.locator('#navMarkers')).to_contain_text('Custom marker')
    page.wait_for_function('savedNavigation.markers.length===1')
    page.locator('#navAddPoint').click()
    page.locator('#navLat').fill('40.1');page.locator('#navLon').fill('-100.1');page.locator('#navSelectCoordinate').click();page.locator('#navAddPoint').click()
    page.wait_for_function('savedNavigation.route.waypoints.length===2')
    page.locator('#navCalculate').click();expect(page.locator('#navNotice')).to_contain_text('Local routing pack not installed')
    assert page.evaluate('savedNavigation.route.geometry.length')==0
    page.locator('#navName').fill('Manually logged trip');page.locator('#navDeparture').fill('2026-11-07T09:00');page.locator('#navArrival').fill('2026-11-07T10:00');page.locator('#navTripStatus').select_option('completed');page.locator('#navNotes').fill('My arrival and departure');page.locator('#navSaveTrip').click()
    page.wait_for_function('savedNavigation.trips.length===1')
    expect(page.locator('#navTrips')).to_contain_text('Manually logged trip')
    page.locator('#navDefaultLat').fill('41.5');page.locator('#navDefaultLon').fill('-101.5');page.locator('#navDefaultZoom').fill('7');page.locator('#navDefaultSave').click()
    page.wait_for_function('savedNavigation.settings.lat===41.5')
    page.reload();expect(page.locator('#gpsCoordinates')).to_contain_text('41.50000, -101.50000')
    page.get_by_text('Places, routes & trips',exact=True).click();expect(page.locator('#navMarkers')).to_contain_text('Custom marker');expect(page.locator('#navTrips')).to_contain_text('Manually logged trip')
    page.locator('#navHistory').click();expect(page.locator('#navHistoryStatus')).to_contain_text('1/4')
    # Replay overlays preserve both recorded segments rather than connecting gaps.
    assert page.locator('.nav-route-layer polyline').count()==2
    page.locator('#navReplaySeek').fill('3');expect(page.locator('#navHistoryStatus')).to_contain_text('4/4')
    # Mouse pan and wheel work when GPS is absent.
    page.locator('.nav-panel summary').click()
    before=page.locator('#gpsCoordinates').inner_text()
    surface=page.locator('#mapSurface').bounding_box();x=surface['x']+500;y=surface['y']+400
    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+80,y+30,steps=5);page.mouse.up();
    expect(page.locator('#gpsCoordinates')).not_to_have_text(before)
    page.mouse.wheel(0,-120)
    # Touch selection and pinch are handled by the same map without GPS.
    page.get_by_text('Places, routes & trips',exact=True).click();page.locator('#navPick').select_option('marker');page.locator('#navName').fill('Touch marker');page.locator('.nav-panel summary').click()
    page.touchscreen.tap(surface['x']+700,surface['y']+350)
    page.wait_for_function('savedNavigation.markers.length===2')
    initial_zoom=page.evaluate('card._zoom');session=page.context.new_cdp_session(page)
    x=surface['x']+650;y=surface['y']+400
    session.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x-40,'y':y,'id':1}]})
    session.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x-40,'y':y,'id':1},{'x':x+40,'y':y,'id':2}]})
    session.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x-80,'y':y,'id':1},{'x':x+80,'y':y,'id':2}]})
    session.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
    assert page.evaluate('card._zoom')>initial_zoom
    assert not errors,errors
    page.screenshot(path='/tmp/car-navigation-planner.png')
    browser.close()
print('GPS-independent markers, waypoints, manual trips, saved defaults, replay and mouse navigation passed')
