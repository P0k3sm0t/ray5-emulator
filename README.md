# Ray5 Emulator

A local test emulator for the Longer Ray5 laser controller.

This project emulates the Ray5 ESP32 / GRBL-style network behavior so Ray5 Pilot, bridge tools, and related software can be tested without powering on the actual laser.

It is useful for development, UI testing, update testing, upload/run testing, GRBL settings testing, ESP32 EEPROM testing, and command-flow debugging.

---

## What It Emulates

The emulator provides mock Ray5 network services:

- HTTP command interface on port `8848`
- WebSocket status interface on port `8849`
- Raw GRBL/Tibbo TCP interface on port `8850` (optional/advanced)
- ESP3D-style command responses
- GRBL-style settings and status responses
- SD file upload/list/run behavior
- ESP32 EEPROM read/write behavior
- Movement/status updates
- Ray5 Pilot test workflows

Default addresses:

```text
HTTP:      http://127.0.0.1:8848
WebSocket: ws://127.0.0.1:8849/
Raw TCP:   127.0.0.1:8850 (optional)
```

WebSocket subprotocol:

```text
arduino
```

---

## Why This Exists

The real Ray5 does not behave like a plain serial GRBL board. It uses an ESP32 network layer with HTTP commands and WebSocket sideband status.

Important port mapping:

- `8848` is HTTP/web API only.
- `8849` is WebSocket status sideband.
- `8850` is optional raw newline-terminated GRBL/Tibbo TCP.

If raw GRBL traffic is sent to `8848`, the emulator logs one warning per client (rate-limited):
`Raw GRBL traffic received on HTTP port 8848. Configure Tibbo/LightBurn to connect to raw TCP port 8850 instead.`

This emulator helps test those behaviors locally, including:

- Ray5 Pilot Dashboard status
- GRBL settings page
- ESP32 / ESP3D page
- EEPROM backup/edit/save testing
- SD card upload and run workflows
- Imported Jobs upload/run testing
- LightBurn bridge testing
- Command box testing
- Offline development without keeping the laser powered on

---

## Requirements

- Windows, Linux, or macOS
- Python 3.10 or newer recommended
- Python packages from `requirements.txt`

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Quick Start

Clone the repo:

```bash
git clone https://github.com/YOUR_USERNAME/ray5-emulator.git
cd ray5-emulator
```

Install requirements:

```bash
pip install -r requirements.txt
```

Start the emulator:

```bash
python ray5_emulator.py
```

You should see output similar to:

```text
HTTP server listening on http://127.0.0.1:8848
Websocket server listening on ws://127.0.0.1:8849/
Raw GRBL TCP server listening on 127.0.0.1:8850
```

---

## Point Ray5 Pilot at the Emulator

In Ray5 Pilot, set the Ray5 host/settings to:

```text
Host: 127.0.0.1
HTTP Port: 8848
WebSocket Port: 8849
Raw TCP Port: 8850 (optional)
```

Then use Ray5 Pilot normally.

You can test:

- Dashboard status
- Manual movement
- GRBL page
- ESP32 page
- EEPROM save workflow
- SD file listing
- Upload / Upload + Run
- Command box

---

## Supported GRBL Commands

The emulator supports common Ray5 / GRBL-style commands such as:

```text
?
$I
$$
$#
$G
$N
$X
$C
$H
G28
G0 / G00
G1 / G01
$J=
M3
M4
M5
M8
M9
M2
M30
G90
G91
G20
G21
```

Examples:

```text
?
$I
$$
$H
G0 X100 Y100
M8
M9
```

Status responses look like:

```text
<Idle|MPos:0.000,0.000,0.000|FS:0,0|Ov:100,100,100|Heap:52012>
```

---

## Supported ESP32 / ESP3D Commands

The Ray5 uses ESP-style commands through the same HTTP command endpoint.

The emulator supports:

```text
[ESP800]json=yes
[ESP400]json=yes
[ESP401]P=<path_or_index> T=<type> V=<value> json=yes
[ESP410]json=yes
```

### ESP800

Returns mock firmware / ESP3D info.

Example:

```text
[ESP800]json=yes
```

Example response data includes:

```text
FW version:1.3a (20211103)
FW target:grbl-embedded
FW HW:Direct SD
hostname:ExampleHostname
webcommunication: Sync: 8849:192.168.0.1,127.0.0.1
```

### ESP400

Returns mock EEPROM settings.

Example:

```text
[ESP400]json=yes
```

Includes settings such as:

```text
Sta/SSID
Sta/Password
Sta/IPMode
AP/SSID
AP/Password
Http/Port
Radio/Mode
SD/history
Flame/Value
```

### ESP401

Writes a mock EEPROM setting.

Example:

```text
[ESP401]P=SD/history T=S V=Test_History json=yes
```

The emulator updates its internal EEPROM store so a later `[ESP400]json=yes` returns the changed value.

---

## SD Upload and Run Support

The emulator can accept mock SD uploads and file run commands.

Supported upload endpoints include:

```text
POST /upload
POST /upload?path=/filename.gc
POST /upload?name=filename.gc
POST /upload?filename=filename.gc
```

Supported file list endpoints include:

```text
GET /files
GET /files?path=/
GET /files?path=/sd
```

Supported run commands include:

```text
$sd/run=/filename.gc
$sd/runzip=/filename.gc.gz
```

When a file is run, the emulator can simulate a brief `Run` state and then return to `Idle`.

---

## Persistence

Depending on configuration, the emulator can persist mock state between runs.

Common persisted files may include:

```text
emulator_eeprom_state.json
emulator_grbl_settings_state.json
emulator_uploads/
```

This allows testing saves, refreshes, and restarts without losing emulator state.

To reset test state, delete the relevant persistence files/folders and restart the emulator.

---

## Configuration

Configuration is handled through `config.example.json` (release default) and optional local `config.json`.
If `config.json` is missing, the emulator creates it from `config.example.json` on first start.

Common options:

```json
{
  "http_host": "127.0.0.1",
  "http_port": 8848,
  "ws_host": "127.0.0.1",
  "ws_port": 8849,
  "raw_host": "127.0.0.1",
  "raw_port": 8850,
  "ws_subprotocol": "arduino",
  "machine_width": 400,
  "machine_height": 365,
  "status_axes": 3,
  "mock_ip": "127.0.0.1",
  "persist_eeprom_file": "emulator_eeprom_state.json",
  "persist_grbl_settings_file": "emulator_grbl_settings_state.json",
  "uploads": {
    "persist": true,
    "directory": "emulator_uploads",
    "default_files": true,
    "simulate_run_seconds": 3
  }
}
```

---

## Testing Ray5 Pilot

Start the emulator:

```bash
python ray5_emulator.py
```

Open Ray5 Pilot and set:

```text
Ray5 Host: 127.0.0.1
HTTP Port: 8848
WebSocket Port: 8849
Raw TCP Port: 8850 (optional)
```

Recommended tests:

### Dashboard

- Confirm status shows `Idle`
- Confirm MPos updates after move commands
- Confirm connection status is healthy

### GRBL Page

- Load GRBL settings
- Change a harmless setting
- Save settings
- Refresh and confirm the value persists

### ESP32 Page

- Load ESP3D info
- Load EEPROM settings
- Change `SD/history`
- Save settings
- Refresh and confirm the value persists

A safe ESP32 test setting is:

```text
SD/history
```

Example value:

```text
Test_History
```

Avoid testing Wi-Fi, IP, password, or radio settings first unless you intentionally want to test network-related behavior.

### Upload / Run

- Upload a `.gc` file
- Confirm it appears in `/files`
- Run it using `$sd/run=/filename.gc`
- Watch status change from `Run` back to `Idle`

---

## Testing the LightBurn Bridge

The emulator can also be used as a test target for the Ray5 LightBurn bridge.

Point the bridge to:

```text
Ray5 Host: 127.0.0.1
HTTP Port: 8848
WebSocket Port: 8849
Raw TCP Port: 8850 (optional)
```

Then connect LightBurn to the bridge as usual.

This lets you test:

- Framing behavior
- Job stream capture
- Upload-only workflow
- Upload-and-run workflow
- SD run commands
- Status polling
- GRBL handshake behavior

---

## Logs

The emulator logs useful debug messages such as:

```text
[HTTP COMMAND] command=...
[WS CONNECT]
[WS SEND]
[POSITION]
[GRBL SETTING WRITE]
[ESP401 WRITE]
[EMULATOR UPLOAD]
[EMULATOR RUN]
[EMULATOR RUN COMPLETE]
```

These logs are intended to help compare Ray5 Pilot, bridge, and Ray5 behavior without needing the real laser powered on.

---

## Stopping the Emulator

Press:

```text
Ctrl+C
```

The emulator should shut down cleanly and release ports `8848`, `8849`, and `8850`.

To check on Windows:

```powershell
netstat -ano | findstr :8848
netstat -ano | findstr :8849
netstat -ano | findstr :8850
```

`TIME_WAIT` entries are normal after shutdown. A remaining `LISTENING` entry means something is still running.

---

## Important Notes

This emulator is for development and testing only.

It does not drive a real laser, does not guarantee exact firmware behavior, and should not be treated as a safety system.

Always test real machine behavior carefully on the actual Ray5 before relying on a workflow for cutting, engraving, motion, or firmware changes.

---

## Known Limitations

- It emulates known Ray5 behavior but may not match every firmware version.
- Some ESP3D commands may be simplified.
- Motion simulation is basic.
- File run progress is simulated.
- It is designed mainly around Ray5 Pilot and bridge testing.
### Optional Raw TCP (Advanced)

`raw_host` / `raw_port` are for optional advanced bridge/Tibbo/raw-client socket testing.

Normal Ray5 Pilot emulator testing uses only:

- HTTP/API: `127.0.0.1:8848`
- WebSocket status: `127.0.0.1:8849`

Raw TCP is separate and optional:

- Raw GRBL TCP: `127.0.0.1:8850`
