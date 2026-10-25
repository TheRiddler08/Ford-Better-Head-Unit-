# Home LoRaWAN server — first stage (no radio transmission yet)

This service runs on the **home Ubuntu server**, not the car Tiny or Duet.
It persists small, ordered configuration changes using the shared
`control/config_core.py` whitelist and prevents stale revisions. This is a
foundation for the future LoRaWAN bridge, not a complete working radio link.

## Install and run

On the **home server** with Python 3, clone the same repository and select the
branch containing this feature. Start it with:

```sh
cd ~/car-dashboard
mkdir -p ~/.config/car-home
python3 -c 'import secrets; print(secrets.token_hex(32))' > ~/.config/car-home/token
chmod 600 ~/.config/car-home/token
CAR_HOME_TOKEN_FILE="$HOME/.config/car-home/token" python3 -m home_server.server
```

The service binds only to `127.0.0.1:8790`. Access it locally or through an
authenticated SSH port forward. **Do not expose it directly to the internet**.

In another terminal:

```sh
TOKEN="$(cat ~/.config/car-home/token)"
curl -fsS http://127.0.0.1:8790/health
curl -fsS -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8790/api/state?device=car'
curl -fsS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"device":"car","expectedRevision":0,"changes":{"theme":"day"}}' \
  http://127.0.0.1:8790/api/change
curl -fsS -H "Authorization: Bearer $TOKEN" 'http://127.0.0.1:8790/api/pending?device=car'
```

The revision number must match the current `/api/state` response. Each
change is stored durably in SQLite until an independently authenticated car
acknowledgment arrives. **There is no public ACK endpoint**; a future
radio-transport adapter must authenticate the car and call the coordinator's
`record_authenticated_ack` only after validated application, not merely on
transmission or a gateway acknowledgment.

## What is NOT yet supported

- ChirpStack/MQTT LoRaWAN connection or real gateway
- Pairing a car-side LoRaWAN modem/end device
- Binary fragmentation/reassembly, retransmission and missing part requests
- Signed executable patches and rollback
- Car GPS/status uplinks or a browser configuration panel

Real LoRaWAN payloads can be very small, especially at low data rates.
The radio adapter must use compact, rate-limited binary messages rather than
transmitting the JSON change queue as-is. A complete update protocol needs
message authentication, replay protection, checksums, chunk IDs, and safe
resumption after car power loss.

Keep the existing Tiny-to-Duet Ethernet path unchanged. The home server is
an independent management service.
