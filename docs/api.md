start a run

    curl -d '{"cmd":"run", "profile":"cone-05-long-bisque"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

skip the first part of a run
restart the kiln on a specific profile and start at minute 60

    curl -d '{"cmd":"run", "profile":"cone-05-long-bisque","startat":60}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

stop a schedule

    curl -d '{"cmd":"stop"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

post a memo

    curl -d '{"cmd":"memo", "memo":"some significant message"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

stats for currently running schedule

    curl -X GET http://0.0.0.0:8081/api/stats

pause a run (maintain current temperature until resume)

    curl -d '{"cmd":"pause"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

resume a paused run
    
    curl -d '{"cmd":"resume"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api

## Settings API

Guard rules that apply across all of these endpoints:

- Safety-critical keys and kiln switching (`/api/settings/active_kiln`) are
  only allowed while the oven is `IDLE`.
- `ignore_*` safety-override flags require `"confirm": true` in the request
  body while a firing is in progress, or the update is rejected.
- `/api/settings/restart` returns `409` if the oven is not `IDLE`.

Each setting key applies in one of three ways, reported per-key in the
`outcomes` object of a successful `POST /api/settings`:

- `applied` — takes effect immediately.
- `applied-next-firing` — saved now, used starting with the next firing (e.g. PID tuning).
- `restart-required` — saved as pending; the server must be restarted (see
  `/api/settings/restart`) before it takes effect. `GET /api/settings` reports
  these as `restart_pending: true` and lists them in `pending`.

get the full settings snapshot (schema, current values, sources, pending restart-required changes, kilns, active kiln, oven state)

    curl -X GET http://0.0.0.0:8081/api/settings

Example response (abbreviated):

    {"schema": ..., "category_order": ..., "values": {...}, "sources": {...},
     "defaults": {...}, "pending": {}, "restart_pending": false,
     "active_kiln": null, "kilns": [...], "oven_state": "IDLE", "simulate": true}

update one or more global settings (applied immediately if not restart-required)

    curl -d '{"scope":"global","values":{"kwh_rate":0.25}}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings

Response:

    {"success": true, "outcomes": {"kwh_rate": "applied"}}

update a setting that requires a restart (e.g. `mqtt_port`) — saved as pending, not applied yet

    curl -d '{"scope":"global","values":{"mqtt_port":8883}}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings

Response:

    {"success": true, "outcomes": {"mqtt_port": "restart-required"}}

`GET /api/settings` afterward reports `"restart_pending": true, "pending": {"mqtt_port": 8883}`.

update a kiln-scoped setting (e.g. PID tuning), applied starting next firing

    curl -d '{"scope":"kiln","values":{"pid_kp":12.5}}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings

Response:

    {"success": true, "outcomes": {"pid_kp": "applied-next-firing"}}

invalid value — rejected all-or-nothing (no keys from the payload are applied, even valid siblings), HTTP 400

    curl -w '\n%{http_code}\n' -d '{"scope":"global","values":{"kwh_rate":0.30,"mqtt_port":-1}}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings

Response:

    {"success": false, "errors": {"mqtt_port": "must be between 1 and 65535"}}
    400

create a new kiln settings profile

    curl -d '{"name":"Test Kiln"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings/kilns

Response:

    {"success": true, "name": "Test Kiln"}

rename a kiln settings profile

    curl -d '{"name":"Renamed Kiln"}' -H "Content-Type: application/json" -X PUT http://0.0.0.0:8081/api/settings/kilns/Test%20Kiln

Response:

    {"success": true}

delete a kiln settings profile (rejected if it is the active kiln)

    curl -X DELETE http://0.0.0.0:8081/api/settings/kilns/Renamed%20Kiln

Response if it is the active kiln:

    {"success": false, "errors": {"name": "cannot delete the active kiln settings profile"}}
    400 (with -w '\n%{http_code}\n')

Response once deactivated first:

    {"success": true}

switch the active kiln (IDLE-only) — pass `"name":null` to deactivate

    curl -d '{"name":"Test Kiln"}' -H "Content-Type: application/json" -X POST http://0.0.0.0:8081/api/settings/active_kiln

Response:

    {"success": true, "restart_required": false}

restart the server (IDLE-only — returns 409 while firing) so systemd (`Restart=always`, see `deploy/kiln-controller.service`) brings it back up and applies any restart-required pending settings

    curl -X POST http://0.0.0.0:8081/api/settings/restart

Response:

    {"success": true}
