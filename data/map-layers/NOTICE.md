# Bundled public camera-location snapshot

`cameras-us.json.gz` is the USA ALPR location export from
[DeFlock / FlockHopper](https://github.com/FoggedLens/deflockhopper_maps), revision
`6ec872341e446e1970bd1f4786c52713be03ba9a`, source snapshot dated **2026-10-08**.
`snapshot.json` records its SHA-256 checksum and provenance. This is public
community-reported infrastructure data, independent of the user's GPS history.

The underlying camera-location database is © OpenStreetMap contributors and is
available under the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
See [OpenStreetMap copyright and attribution](https://www.openstreetmap.org/copyright).
Individual contents of the database are available under the Database Contents
License. The dashboard displays the attribution and links to source OSM reports.
It does not claim these records establish current camera presence or activity.

The upstream FlockHopper software is MIT licensed, copyright 2025 FlockHopper
Contributors; no upstream application code is incorporated here. This export is
included so first installation can display camera reports without an Internet
connection. Later public downloads use the fixed upstream USA export URL over
HTTPS, with no vehicle coordinates or routes in the request.
