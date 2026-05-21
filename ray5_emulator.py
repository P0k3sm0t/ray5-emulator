import asyncio
import contextlib
import ipaddress
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server
from websockets.server import serve

TOKEN_RE = re.compile(r"([A-Za-z])([+-]?\d*\.?\d*)")
ESP401_RE = re.compile(r"\[ESP401\]\s*(.*)$", re.IGNORECASE)

IDENTITY_LINES = [
    "[VER:1.3a.20211103:]",
    "[OPT:PHSW,64,256,63,1024]",
    "[Board:LGT LASER RAY5 V1.1]",
    "[Machine:Longer Laser Ray5]",
    "[Software:v1.9.11]",
    "[Wavelength:460nm]",
    "[Working Size:XSIZE=400.00:YSIZE=365.00]",
    "[MSG:Mode=STA:SSID=FBI Mobile:IP=127.0.0.1:MAC=10-20-BA-5F-04-54]",
    "[MSG:Mode=AP:SSDI=LongerLaser_0454:IP=192.168.0.1:MAC=10-20-BA-5F-04-55:password=12345678]",
    "ok",
]

SETTINGS_LINES = [
    "$0=3", "$1=250", "$2=0", "$3=0", "$4=0", "$5=1", "$6=0", "$10=1", "$11=0.010", "$12=0.002",
    "$13=0", "$20=1", "$21=1", "$22=1", "$23=3", "$24=200.000", "$25=2000.000", "$26=250.000", "$27=2.000",
    "$30=1000.000", "$31=0.000", "$32=1", "$100=80.000", "$101=80.000", "$102=100.000", "$110=24000.000",
    "$111=24000.000", "$112=1000.000", "$120=500.000", "$121=500.000", "$122=200.000", "$130=400.000", "$131=365.000",
    "$132=300.000", "ok",
]
GRBL_SETTING_WRITE_RE = re.compile(r"^\$(\d+)=([+-]?\d+(?:\.\d+)?)$")

OFFSETS_LINES = [
    "[G54:0.000,0.000,0.000]",
    "[G55:0.000,0.000,0.000]",
    "[G56:0.000,0.000,0.000]",
    "[G57:0.000,0.000,0.000]",
    "[G58:0.000,0.000,0.000]",
    "[G59:0.000,0.000,0.000]",
    "[G28:0.000,0.000,0.000]",
    "[G30:0.000,0.000,0.000]",
    "[G92:0.000,0.000,0.000]",
    "[TLO:0.000]",
    "[PRB:0.000,0.000,0.000:0]",
    "ok",
]

EEPROM_DEFAULT = {
    "EEPROM": [
        {"F": "network", "P": "Sta/SSID", "H": "Station SSID", "T": "S", "V": "FBI Mobile", "S": "32", "M": "1"},
        {"F": "network", "P": "Sta/Password", "H": "Station Password", "T": "S", "V": "******", "S": "64", "M": "8"},
        {"F": "network", "P": "Sta/IPMode", "H": "Station IP Mode", "T": "B", "V": "0", "O": [{"DHCP": "0"}, {"Static": "1"}]},
        {"F": "network", "P": "Sta/IP", "H": "Station Static IP", "T": "A", "V": "0.0.0.0"},
        {"F": "network", "P": "Sta/Gateway", "H": "Station Static Gateway", "T": "A", "V": "0.0.0.0"},
        {"F": "network", "P": "Sta/Netmask", "H": "Station Static Mask", "T": "A", "V": "0.0.0.0"},
        {"F": "network", "P": "AP/SSID", "H": "AP SSID", "T": "S", "V": "LongerLaser_0454", "S": "32", "M": "1"},
        {"F": "network", "P": "AP/Password", "H": "AP Password", "T": "S", "V": "12345678", "S": "64", "M": "8"},
        {"F": "network", "P": "AP/IP", "H": "AP Static IP", "T": "A", "V": "192.168.0.1"},
        {"F": "network", "P": "AP/Channel", "H": "AP Channel", "T": "I", "V": "1", "S": "14", "M": "1"},
        {"F": "network", "P": "System/Hostname", "H": "Hostname", "T": "S", "V": "grblesp", "S": "32", "M": "1"},
        {"F": "network", "P": "Http/Enable", "H": "HTTP Enable", "T": "B", "V": "1", "O": [{"OFF": "0"}, {"ON": "1"}]},
        {"F": "network", "P": "Http/Port", "H": "HTTP Port", "T": "I", "V": "8848", "S": "65001", "M": "1"},
        {"F": "network", "P": "Telnet/Enable", "H": "Telnet Enable", "T": "B", "V": "1", "O": [{"OFF": "0"}, {"ON": "1"}]},
        {"F": "network", "P": "Telnet/Port", "H": "Telnet Port", "T": "I", "V": "23", "S": "65001", "M": "1"},
        {"F": "network", "P": "Radio/Mode", "H": "Radio mode", "T": "B", "V": "3", "O": [{"AP": "2"}, {"AP_STA": "3"}, {"NONE": "0"}, {"STA": "1"}]},
        {"F": "network", "P": "SD/history", "H": "sd_history", "T": "S", "V": "NO_History", "S": "50", "M": "0"},
        {"F": "network", "P": "Accel/XZAngle", "H": "Accel XAngle Threshold Value", "T": "I", "V": "110", "S": "3000", "M": "60"},
        {"F": "network", "P": "Accel/Freq", "H": "Accel Freq Threshold Value", "T": "I", "V": "15", "S": "40", "M": "3"},
        {"F": "network", "P": "Accel/Time", "H": "Accel Time Threshold Value", "T": "I", "V": "15", "S": "100", "M": "3"},
        {"F": "network", "P": "Accel/ZAngle", "H": "Accel ZAngle Threshold Value", "T": "I", "V": "180", "S": "3000", "M": "80"},
        {"F": "network", "P": "Accel/XAngle", "H": "Accel XAngle Threshold Value", "T": "I", "V": "180", "S": "3000", "M": "80"},
        {"F": "network", "P": "Flame/Value", "H": "ADC Flame Threshold Value", "T": "I", "V": "2500", "S": "4095", "M": "0"},
    ]
}


@dataclass
class UploadedFile:
    filename: str
    path: str
    size: int
    uploaded_at: str
    content: bytes = b""
    compressed: bool = False
    last_run_at: str = ""
    run_count: int = 0

    def to_json(self) -> dict[str, Any]:
        modified = self.last_run_at or self.uploaded_at
        return {
            "name": self.filename,
            "path": self.path,
            "size": self.size,
            "type": "file",
            "modified": modified,
        }


@dataclass
class EmulatorState:
    x: float
    y: float
    z: float
    absolute_mode: bool = True
    state: str = "Idle"
    feed: int = 0
    spindle: int = 0
    air_assist: bool = False
    air_pump_state: str = "off"
    current_file: str = ""
    sd_percent: float = 0.0


class WsHub:
    def __init__(self, ping_seconds: float, ws_subprotocol: str, status_provider: Any = None, status_seconds: float = 3.0) -> None:
        self.ping_seconds = ping_seconds
        self.status_seconds = max(1.0, float(status_seconds or 3.0))
        self.ws_subprotocol = ws_subprotocol
        self.status_provider = status_provider
        self.clients: set = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.server = None
        self.current_id = "0"
        self.active_id = "0"
        self._ping_task: Optional[asyncio.Task] = None
        self._status_task: Optional[asyncio.Task] = None
        self.log = logging.getLogger("emulator")

    async def start(self, host: str, port: int) -> None:
        self.loop = asyncio.get_running_loop()
        self.server = await serve(self._handler, host, port, subprotocols=[self.ws_subprotocol])
        self.log.info("Websocket server listening on ws://%s:%d/", host, port)
        self._ping_task = asyncio.create_task(self._ping_loop())
        self._status_task = asyncio.create_task(self._status_loop())

    async def stop(self) -> None:
        if self._ping_task:
            self._ping_task.cancel()
            with contextlib.suppress(Exception):
                await self._ping_task
        if self._status_task:
            self._status_task.cancel()
            with contextlib.suppress(Exception):
                await self._status_task
        for ws in list(self.clients):
            with contextlib.suppress(Exception):
                await ws.close()
            self.clients.discard(ws)
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handler(self, websocket) -> None:
        self.clients.add(websocket)
        self.log.info("[WS CONNECT] peer=%s subprotocol=%s", websocket.remote_address, websocket.subprotocol)
        await websocket.send(f"CURRENT_ID:{self.current_id}")
        self.log.info("[WS SEND] CURRENT_ID:%s", self.current_id)
        await websocket.send(f"ACTIVE_ID:{self.active_id}")
        self.log.info("[WS SEND] ACTIVE_ID:%s", self.active_id)
        if callable(self.status_provider):
            try:
                status_line = str(self.status_provider() or "").strip()
                if status_line:
                    await websocket.send(status_line)
                    self.log.info("[WS SEND] %s", status_line)
            except Exception:
                pass
        try:
            async for _ in websocket:
                pass
        except Exception:
            pass
        finally:
            self.clients.discard(websocket)

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(self.ping_seconds)
            await self.broadcast("PING:0")

    async def _status_loop(self) -> None:
        while True:
            await asyncio.sleep(self.status_seconds)
            if callable(self.status_provider):
                try:
                    line = str(self.status_provider() or "").strip()
                except Exception:
                    line = ""
                if line:
                    await self.broadcast(line)

    async def broadcast(self, line: str) -> None:
        if not self.clients:
            return
        self.log.info("[WS SEND] %s", line)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(line)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def send(self, line: str) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(line), self.loop)


def parse_tokens(line: str) -> dict[str, list[float | str]]:
    out: dict[str, list[float | str]] = {}
    for letter, num in TOKEN_RE.findall(line):
        k = letter.upper()
        if k in {"G", "M"} and num:
            out.setdefault(k, []).append(f"{k}{num}")
            continue
        if not num:
            continue
        try:
            v: float | str = float(num)
        except ValueError:
            v = num
        out.setdefault(k, []).append(v)
    return out


def line_word(line: str) -> str:
    m = re.match(r"\s*([A-Za-z][0-9]+(?:\.[0-9]+)?)", line)
    return m.group(1).upper() if m else ""


def parse_esp401_pairs(payload: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for m in re.finditer(r"([A-Za-z])=", payload):
        key = m.group(1)
        start = m.end()
        nxt = re.search(r"\s+[A-Za-z]=", payload[start:])
        end = start + nxt.start() if nxt else len(payload)
        pairs[key] = payload[start:end].strip()
    return pairs


def is_sensitive_path(path: str) -> bool:
    low = path.lower()
    return any(k in low for k in ["password", "pass", "token", "key", "secret"])


def is_sensitive_descriptor(*parts: Any) -> bool:
    text = " ".join(str(p or "") for p in parts).lower()
    return any(k in text for k in ["password", "pass", "token", "key", "secret"])


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Emulator:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("emulator")
        self.status_axes = int(cfg.get("status_axes", 3))
        self.status_broadcast_seconds = float(cfg.get("status_broadcast_seconds", 3))
        self.machine_width = float(cfg.get("machine_width", 400))
        self.machine_height = float(cfg.get("machine_height", 365))
        uploads_cfg = cfg.get("uploads", {}) if isinstance(cfg.get("uploads"), dict) else {}
        self.uploads_persist = bool(uploads_cfg.get("persist", cfg.get("persist_uploaded_files", True)))
        self.uploads_dir = Path(uploads_cfg.get("directory", "emulator_uploads")).resolve()
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.default_files_enabled = bool(uploads_cfg.get("default_files", True))
        self.simulate_run_seconds = float(uploads_cfg.get("simulate_run_seconds", 3))
        self.persist_eeprom_file = Path(cfg.get("persist_eeprom_file", "emulator_eeprom_state.json")).resolve()
        self.persist_grbl_settings_file = Path(cfg.get("persist_grbl_settings_file", "emulator_grbl_settings_state.json")).resolve()
        self.state = EmulatorState(
            x=float(cfg.get("initial_x", 0.0)),
            y=float(cfg.get("initial_y", 0.0)),
            z=float(cfg.get("initial_z", 0.0)),
        )
        self.hub = WsHub(
            float(cfg.get("status_ping_seconds", 10)),
            str(cfg.get("ws_subprotocol", "arduino")),
            status_provider=self._status_line,
            status_seconds=self.status_broadcast_seconds,
        )
        self.app = Flask(__name__)
        self.eeprom_data = self._load_eeprom_state()
        self.grbl_settings = self._load_grbl_settings_state()
        self.files: dict[str, UploadedFile] = {}
        self._seed_files()
        self._load_persisted_uploads()
        self._run_lock = threading.Lock()
        self.ws_status_count = 0
        self.last_status_line = self._status_line()
        self._setup_routes()

    def _set_air_pump_state(self, state: str, command: str) -> None:
        normalized = "on" if str(state).strip().lower() == "on" else "off"
        self.state.air_pump_state = normalized
        self.state.air_assist = normalized == "on"
        self.log.info("[AIR PUMP] state=%s command=%s", normalized, command)

    def _load_eeprom_state(self) -> dict[str, Any]:
        if self.persist_eeprom_file.exists():
            try:
                data = json.loads(self.persist_eeprom_file.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("EEPROM"), list):
                    self.log.info("[EEPROM LOAD] file=%s", self.persist_eeprom_file)
                    return data
            except Exception:
                pass
        self.log.info("[EEPROM LOAD DEFAULTS]")
        return json.loads(json.dumps(EEPROM_DEFAULT))

    def _save_eeprom_state(self) -> None:
        self.persist_eeprom_file.write_text(json.dumps(self.eeprom_data, indent=2), encoding="utf-8")
        self.log.info("[EEPROM SAVE] file=%s", self.persist_eeprom_file)

    def _default_grbl_settings_map(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for line in SETTINGS_LINES:
            if line.startswith("$") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip()
        return out

    def _load_grbl_settings_state(self) -> dict[str, str]:
        if self.persist_grbl_settings_file.exists():
            try:
                data = json.loads(self.persist_grbl_settings_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    settings = {str(k): str(v) for k, v in data.items() if str(k).startswith("$")}
                    if settings:
                        self.log.info("[GRBL SETTINGS LOAD] file=%s", self.persist_grbl_settings_file)
                        return settings
            except Exception:
                pass
        return self._default_grbl_settings_map()

    def _save_grbl_settings_state(self) -> None:
        self.persist_grbl_settings_file.write_text(json.dumps(self.grbl_settings, indent=2), encoding="utf-8")
        self.log.info("[GRBL SETTINGS SAVE] file=%s", self.persist_grbl_settings_file)

    def _format_grbl_settings_lines(self) -> list[str]:
        items: list[tuple[int, str, str]] = []
        for key, raw_value in self.grbl_settings.items():
            if not key.startswith("$"):
                continue
            try:
                idx = int(key[1:])
            except Exception:
                continue
            try:
                v = float(raw_value)
                val = f"{v:.3f}"
            except Exception:
                val = str(raw_value)
            items.append((idx, key, val))
        items.sort(key=lambda x: x[0])
        return [f"{k}={v}" for _, k, v in items] + ["ok"]

    def _seed_files(self) -> None:
        if not self.default_files_enabled:
            return
        defaults = {
            "/test_square.gc": b"G21\nG90\nG0 X10 Y10\nG1 X30 Y10 S200 F1200\nM2\n",
            "/sample_frame.gc": b"G21\nG91\nG1 X10 Y0 S10 F2000\nG1 X0 Y10 S10 F2000\nM2\n",
            "/lightburn_job_001.gc": b"G21\nG90\nG0 X0 Y0\nM3 S300\nG1 X50 Y0 F1500\nM5\nM2\n",
        }
        for path, content in defaults.items():
            self._put_file(path, content, source="default")

    def _load_persisted_uploads(self) -> None:
        if not self.uploads_persist:
            return
        for p in self.uploads_dir.iterdir():
            if not p.is_file():
                continue
            path = f"/{p.name}"
            if path in self.files:
                continue
            content = p.read_bytes()
            self._put_file(path, content, source="persist")

    def _put_file(self, path: str, content: bytes, source: str = "upload") -> UploadedFile:
        norm = self._normalize_sd_path(path)
        filename = Path(norm).name
        rec = UploadedFile(
            filename=filename,
            path=norm,
            size=len(content),
            uploaded_at=now_iso(),
            content=content,
            compressed=filename.lower().endswith(".gz"),
        )
        self.files[norm] = rec
        if self.uploads_persist and source in {"upload", "persist"}:
            (self.uploads_dir / filename).write_bytes(content)
        return rec

    def _delete_file(self, path: str) -> bool:
        norm = self._normalize_sd_path(path)
        rec = self.files.pop(norm, None)
        if not rec:
            return False
        disk_path = self.uploads_dir / rec.filename
        if disk_path.exists() and self.uploads_persist:
            disk_path.unlink(missing_ok=True)
        return True

    def _normalize_sd_path(self, p: str) -> str:
        raw = str(p or "").strip().replace("\\", "/")
        if not raw:
            return "/"
        if raw.startswith("/sd/"):
            raw = raw[3:]
        elif raw == "/sd":
            raw = "/"
        if not raw.startswith("/"):
            raw = f"/{raw}"
        while "//" in raw:
            raw = raw.replace("//", "/")
        return raw

    def _setup_routes(self) -> None:
        @self.app.get("/command")
        def command() -> Response:
            cmd = request.args.get("commandText", "")
            pageid = request.args.get("PAGEID", "")
            self.log.info("[HTTP COMMAND] command=%s pageid=%s", cmd, pageid)
            body, content_type = self._handle_command(cmd)
            return Response(body, status=200, mimetype=content_type)

        @self.app.get("/files")
        def files() -> Response:
            req_path = self._normalize_sd_path(request.args.get("path", "/"))
            rows = [f.to_json() for _, f in sorted(self.files.items(), key=lambda kv: kv[0].lower())]
            self.log.info("[EMULATOR FILES] path=%s count=%d", req_path, len(rows))
            accept = str(request.headers.get("Accept", "")).lower()
            if "text/plain" in accept:
                payload = "\n".join(r["name"] for r in rows) + ("\n" if rows else "")
                return Response(payload, status=200, mimetype="text/plain")
            return jsonify({"ok": True, "path": req_path, "files": rows})

        @self.app.post("/upload")
        def upload() -> Response:
            filename = self._extract_upload_filename()
            data = self._extract_upload_bytes()
            if not filename:
                filename = f"uploaded_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gc"
            rec = self._put_file(filename, data, source="upload")
            self.log.info(
                "[EMULATOR UPLOAD] filename=%s size=%d content_type=%s",
                rec.path,
                rec.size,
                request.content_type,
            )
            self.log.info("[EMULATOR UPLOAD OK] path=%s", rec.path)
            if "application/json" in str(request.headers.get("Accept", "")).lower():
                return jsonify({"ok": True, "filename": rec.path, "size": rec.size})
            return Response("ok\n", status=200, mimetype="text/plain")

        @self.app.delete("/files")
        def delete_file() -> Response:
            path = request.args.get("path", "")
            ok = self._delete_file(path)
            self.log.info("[EMULATOR DELETE] filename=%s", self._normalize_sd_path(path))
            if ok:
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": "file not found"}), 404

        @self.app.get("/delete")
        def delete_file_compat() -> Response:
            path = request.args.get("path", "")
            ok = self._delete_file(path)
            self.log.info("[EMULATOR DELETE] filename=%s", self._normalize_sd_path(path))
            if ok:
                return Response("ok\n", status=200, mimetype="text/plain")
            return Response("error:file not found\n", status=404, mimetype="text/plain")

        @self.app.get("/debug/ws")
        def debug_ws() -> Response:
            return jsonify(
                {
                    "websocket_clients": len(self.hub.clients),
                    "page_id": self.hub.current_id,
                    "state": self.state.state,
                    "last_status": self.last_status_line,
                    "status_count": self.ws_status_count,
                }
            )

    def _extract_upload_filename(self) -> str:
        qpath = request.args.get("path", "").strip()
        qname = request.args.get("name", "").strip()
        qfilename = request.args.get("filename", "").strip()
        if "file" in request.files:
            up = request.files["file"]
            if up.filename:
                return up.filename.strip("/")
        if qfilename:
            return qfilename.strip("/")
        if qname:
            return qname.strip("/")
        if qpath and qpath != "/":
            return Path(qpath).name
        return ""

    def _extract_upload_bytes(self) -> bytes:
        if "file" in request.files:
            return request.files["file"].read()
        return request.get_data() or b""

    def _status_line(self) -> str:
        coords = f"{self.state.x:.3f},{self.state.y:.3f}" if self.status_axes == 2 else f"{self.state.x:.3f},{self.state.y:.3f},{self.state.z:.3f}"
        base = f"<{self.state.state}|MPos:{coords}|FS:{self.state.feed},{self.state.spindle}|Ov:100,100,100"
        if self.state.current_file and self.state.state == "Run":
            base += f"|SD:{self.state.sd_percent:0.2f},{self.state.current_file}"
        base += "|Heap:52012>"
        return base

    def _emit_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.hub.send(line)

    def _emit_status(self) -> None:
        line = self._status_line()
        self.last_status_line = line
        self.ws_status_count += 1
        self._emit_lines([line])

    def _enter_alarm(self, reason: str) -> None:
        self.log.info("[EMULATOR STOP] reason=%s", reason)
        self.state.spindle = 0
        self.state.feed = 0
        self.state.state = "Alarm"
        self.log.info("[EMULATOR ALARM] state=Alarm")
        self._emit_status()

    def _clear_alarm(self) -> None:
        self.log.info("[EMULATOR UNLOCK] command=$X")
        self.state.state = "Idle"
        self.state.feed = 0
        self.log.info("[EMULATOR ALARM CLEARED]")
        self._emit_lines(["ok"])
        self._emit_status()

    def _set_position(self, x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None) -> None:
        if x is not None:
            self.state.x = max(0.0, min(self.machine_width, x))
        if y is not None:
            self.state.y = max(0.0, min(self.machine_height, y))
        if z is not None:
            self.state.z = z
        self.log.info("[POSITION] x=%.3f y=%.3f z=%.3f", self.state.x, self.state.y, self.state.z)

    def _simulate_run(self, file_path: str, mode: str) -> None:
        if not self._run_lock.acquire(blocking=False):
            self._emit_lines(["error: Another interface is busy"])
            return

        def _runner() -> None:
            try:
                self.state.current_file = file_path
                self.state.state = "Run"
                self.state.feed = 1000
                self.state.sd_percent = 0.0
                self._emit_lines(["ok", f"[MSG:Running {Path(file_path).name}]"])
                self._emit_status()
                steps = [10.0, 25.0, 50.0, 75.0, 100.0]
                wait = max(0.2, self.simulate_run_seconds / len(steps))
                for pct in steps:
                    time.sleep(wait)
                    self.state.sd_percent = pct
                    self.log.info("[EMULATOR RUN PROGRESS] percent=%.2f", pct)
                    self._emit_status()
                rec = self.files.get(file_path)
                if rec:
                    rec.last_run_at = now_iso()
                    rec.run_count += 1
                self.state.state = "Idle"
                self.state.feed = 0
                self.state.spindle = 0
                self.state.current_file = ""
                self.state.sd_percent = 0.0
                self.log.info("[EMULATOR RUN COMPLETE] filename=%s", file_path)
                self._emit_status()
            finally:
                self._run_lock.release()

        threading.Thread(target=_runner, daemon=True).start()

    def _esp800_raw(self) -> str:
        return (
            "FW version:1.3a (20211103) # FW target:grbl-embedded  # FW HW:Direct SD  # primary sd:/sd "
            "# secondary sd:none # authentication:no # webcommunication: Sync: 8849:192.168.0.1,127.0.0.1 "
            "# hostname:grblesp # axis:2"
        )

    def _esp401_write(self, cmd: str) -> tuple[str, str]:
        m = ESP401_RE.match(cmd)
        if not m:
            return json.dumps({"cmd": "401", "status": "error", "error": "Invalid ESP401 format"}), "application/json"
        params = parse_esp401_pairs(m.group(1))
        p = params.get("P", "")
        t = params.get("T", "").upper()
        v = params.get("V", "")
        if not p or not t:
            return json.dumps({"cmd": "401", "status": "error", "error": "Missing P or T"}), "application/json"

        items: list[dict[str, Any]] = self.eeprom_data.get("EEPROM", [])
        index = -1
        mode = "path"
        for i, item in enumerate(items):
            if str(item.get("P", "")) == p:
                index = i
                mode = "path"
                break
        if index < 0 and p.isdigit():
            idx = int(p)
            if 0 <= idx < len(items):
                index = idx
                mode = "index"

        if index < 0:
            self.log.warning("[ESP401 ERROR] Setting not found P=%s", p)
            return json.dumps({"cmd": "401", "status": "error", "error": "Setting not found"}), "application/json"

        entry = items[index]
        path = str(entry.get("P", ""))
        label = str(entry.get("H", ""))
        options = entry.get("O") if isinstance(entry.get("O"), list) else []
        old_value = str(entry.get("V", ""))

        if t == "I":
            try:
                iv = int(v)
            except Exception:
                self.log.warning("[ESP401 ERROR] invalid integer path=%s", path)
                return json.dumps({"cmd": "401", "status": "error", "error": "Invalid integer"}), "application/json"
            min_raw = str(entry.get("M", "")).strip()
            max_raw = str(entry.get("S", "")).strip()
            if min_raw:
                try:
                    if iv < int(float(min_raw)):
                        self.log.warning("[ESP401 ERROR] path=%s error=value below min", path)
                        return json.dumps({"cmd": "401", "status": "error", "error": "Value below minimum"}), "application/json"
                except Exception:
                    pass
            if max_raw:
                try:
                    if iv > int(float(max_raw)):
                        self.log.warning("[ESP401 ERROR] path=%s error=value above max", path)
                        return json.dumps({"cmd": "401", "status": "error", "error": "Value above maximum"}), "application/json"
                except Exception:
                    pass
        elif t == "A":
            try:
                ipaddress.IPv4Address(v)
            except Exception:
                self.log.warning("[ESP401 ERROR] invalid ip path=%s", path)
                return json.dumps({"cmd": "401", "status": "error", "error": "Invalid IPv4"}), "application/json"
        elif t == "B" and options:
            allowed = {str(val) for opt in options if isinstance(opt, dict) for val in opt.values()}
            if str(v) not in allowed:
                self.log.warning("[ESP401 ERROR] invalid option path=%s value=%s", path, v)
                return json.dumps({"cmd": "401", "status": "error", "error": "Invalid option value"}), "application/json"
        elif t == "S":
            sv = str(v)
            min_raw = str(entry.get("M", "")).strip()
            max_raw = str(entry.get("S", "")).strip()
            if min_raw:
                try:
                    if len(sv) < int(float(min_raw)):
                        self.log.warning("[ESP401 ERROR] path=%s error=string shorter than min", path)
                        return json.dumps({"cmd": "401", "status": "error", "error": "String shorter than minimum length"}), "application/json"
                except Exception:
                    pass
            if max_raw:
                try:
                    if len(sv) > int(float(max_raw)):
                        self.log.warning("[ESP401 ERROR] path=%s error=string longer than max", path)
                        return json.dumps({"cmd": "401", "status": "error", "error": "String longer than maximum length"}), "application/json"
                except Exception:
                    pass

        entry["V"] = str(v)
        self._save_eeprom_state()
        sensitive = is_sensitive_descriptor(path, label)
        log_old = "******" if sensitive else old_value
        log_new = "******" if sensitive else str(v)
        if mode == "path":
            self.log.info("[ESP401 WRITE] mode=path path=%s old=%s new=%s", path, log_old, log_new)
        else:
            self.log.info("[ESP401 WRITE] mode=index index=%d path=%s old=%s new=%s", index, path, log_old, log_new)
        self.log.info("[ESP401 OK]")
        body = {"cmd": "401", "status": "ok", "data": {"P": path, "V": str(v)}}
        return json.dumps(body), "application/json"

    def _handle_run_delete_command(self, upper: str, stripped: str) -> Optional[tuple[str, str]]:
        if upper.startswith("$SD/RUN=") or upper.startswith("$SD/RUNZIP="):
            raw_file = stripped.split("=", 1)[1].strip()
            file_path = self._normalize_sd_path(raw_file)
            rec = self.files.get(file_path)
            if rec:
                mode = "runzip" if upper.startswith("$SD/RUNZIP=") else "run"
                if mode == "run":
                    self.log.info("[EMULATOR RUN] filename=%s mode=run", file_path)
                else:
                    self.log.info("[EMULATOR RUNZIP] filename=%s mode=runzip", file_path)
                self._simulate_run(file_path, mode)
            else:
                self.log.warning("[EMULATOR RUN ERROR] reason=file_not_found filename=%s", file_path)
                self._emit_lines(["error:file not found"])
            return "", "text/plain"

        if upper.startswith("$SD/DELETE="):
            raw_file = stripped.split("=", 1)[1].strip()
            file_path = self._normalize_sd_path(raw_file)
            ok = self._delete_file(file_path)
            self.log.info("[EMULATOR DELETE] filename=%s", file_path)
            if ok:
                self._emit_lines(["ok"])
            else:
                self._emit_lines(["error:file not found"])
            return "", "text/plain"

        return None

    def _handle_command(self, cmd: str) -> tuple[str, str]:
        stripped = (cmd or "").strip()
        upper = stripped.upper()

        if not stripped:
            self._emit_lines(["ok"])
            return "", "text/plain"

        if "\x18" in (cmd or "") or upper in {"CTRL-X", "CTRLX"}:
            self._enter_alarm("ctrl_x")
            self._emit_lines(["error: alarm"])
            return "", "text/plain"

        if upper == "$X":
            self._clear_alarm()
            return "", "text/plain"

        # While alarmed, block motion/homing/jog/run commands until unlocked.
        if self.state.state == "Alarm":
            w_alarm = line_word(upper)
            if upper.startswith("M5"):
                self.state.spindle = 0
                self._emit_lines(["ok"])
                self._emit_status()
                return "", "text/plain"
            if upper in {"?", "M8", "M9"}:
                if upper == "?":
                    self._emit_status()
                    return "Error", "text/plain"
                if upper == "M8":
                    self.log.info("[EMULATOR ALARM LOCK] command=%s", stripped)
                    self._emit_lines(["error: Alarm lock"])
                    self._emit_status()
                    return "", "text/plain"
                if upper == "M9":
                    self._set_air_pump_state("off", "M9")
                self._emit_lines(["ok"])
                self._emit_status()
                return "", "text/plain"
            if upper.startswith("$J=") or w_alarm in {"G0", "G00", "G1", "G01"} or upper in {"$H", "G28"}:
                self.log.info("[EMULATOR ALARM LOCK] command=%s", stripped)
                self._emit_lines(["error: Alarm lock"])
                self._emit_status()
                return "", "text/plain"

        run_delete_result = self._handle_run_delete_command(upper, stripped)
        if run_delete_result is not None:
            return run_delete_result

        if upper in {"[ESP800]JSON=YES", "[ESP800]"}:
            return self._esp800_raw(), "text/plain"

        if upper in {"[ESP400]JSON=YES", "[ESP400]"}:
            return json.dumps(self.eeprom_data), "application/json"

        if upper.startswith("[ESP401]"):
            return self._esp401_write(stripped)

        if upper == "[ESP499]RESET=YES":
            self.eeprom_data = json.loads(json.dumps(EEPROM_DEFAULT))
            self._save_eeprom_state()
            self.log.info("[EEPROM RESET DEFAULTS]")
            return json.dumps({"cmd": "499", "status": "ok", "message": "EEPROM reset to defaults"}), "application/json"

        if upper in {"[ESP410]JSON=YES", "[ESP410]"}:
            return json.dumps(
                {
                    "AP_LIST": [
                        {"SSID": "FBI Mobile", "SIGNAL": "-45", "IS_PROTECTED": "1"},
                        {"SSID": "LongerLaser_0454", "SIGNAL": "-30", "IS_PROTECTED": "1"},
                        {"SSID": "TestNetwork", "SIGNAL": "-70", "IS_PROTECTED": "0"},
                    ]
                }
            ), "application/json"

        if upper == "$I":
            self.log.info("[GRBL IDENTITY] command=$I")
            self._emit_lines(IDENTITY_LINES)
            return "", "text/plain"

        if upper == "$$":
            self._emit_lines(self._format_grbl_settings_lines())
            return "", "text/plain"

        if upper == "$#":
            self._emit_lines(OFFSETS_LINES)
            return "", "text/plain"

        if upper == "$G":
            air_word = "M8" if self.state.air_pump_state == "on" else "M9"
            self._emit_lines([f"[GC:G0 G54 G17 G21 G90 G94 M5 {air_word} T0 F0 S0]", "ok"])
            return "", "text/plain"

        if upper == "$N":
            self._emit_lines(["$N0=", "$N1=", "ok"])
            return "", "text/plain"

        m = GRBL_SETTING_WRITE_RE.match(stripped)
        if m:
            key = f"${m.group(1)}"
            new_raw = m.group(2)
            old_raw = self.grbl_settings.get(key, "")
            try:
                new_num = float(new_raw)
                new_fmt = f"{new_num:.3f}"
            except Exception:
                new_fmt = new_raw
            self.grbl_settings[key] = new_fmt
            self._save_grbl_settings_state()
            self.log.info("[GRBL SETTING WRITE] key=%s old=%s new=%s", key, old_raw, new_fmt)
            self.log.info("[GRBL SETTING OK] key=%s", key)
            self._emit_lines(["ok"])
            return "", "text/plain"

        if upper == "$C":
            self._emit_lines(["ok"])
            return "", "text/plain"

        if upper == "?":
            self._emit_status()
            return "Error", "text/plain"

        if upper == "!":
            self.state.state = "Hold"
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper == "~":
            self.state.state = "Run"
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper == "$H" or upper == "G28":
            self.state.state = "Idle"
            self._set_position(0.0, 0.0, 0.0)
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper in {"G90", "G91"}:
            self.state.absolute_mode = upper == "G90"
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper in {"G20", "G21", "G54", "G17", "G40", "M2", "M30", "M8", "M9"}:
            if upper in {"M2", "M30"}:
                self.state.state = "Idle"
                self.state.feed = 0
                self.state.current_file = ""
                self.state.sd_percent = 0.0
            if upper == "M8":
                self._set_air_pump_state("on", "M8")
            if upper == "M9":
                self._set_air_pump_state("off", "M9")
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper.startswith("M3") or upper.startswith("M4"):
            tok = parse_tokens(stripped)
            if "S" in tok:
                self.state.spindle = int(float(tok["S"][-1]))
            self.state.state = "Run"
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper.startswith("M5"):
            self.state.spindle = 0
            self.state.state = "Idle"
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper.startswith("$J="):
            jog = stripped[3:]
            jog_upper = jog.upper()
            jog_tokens = parse_tokens(jog)
            jog_relative = "G91" in jog_upper
            x = self.state.x
            y = self.state.y
            z = self.state.z
            if "X" in jog_tokens:
                dx = float(jog_tokens["X"][-1])
                x = x + dx if jog_relative else dx
            if "Y" in jog_tokens:
                dy = float(jog_tokens["Y"][-1])
                y = y + dy if jog_relative else dy
            if "Z" in jog_tokens:
                dz = float(jog_tokens["Z"][-1])
                z = z + dz if jog_relative else dz
            if "F" in jog_tokens:
                self.state.feed = int(float(jog_tokens["F"][-1]))
            self._set_position(x, y, z)
            self.state.state = "Run"
            self._emit_lines(["ok"])
            self._emit_status()
            self.state.state = "Idle"
            return "", "text/plain"

        w = line_word(upper)
        if w in {"G0", "G00", "G1", "G01"}:
            t = parse_tokens(stripped)
            x = self.state.x
            y = self.state.y
            z = self.state.z
            if "X" in t:
                vx = float(t["X"][-1])
                x = vx if self.state.absolute_mode else x + vx
            if "Y" in t:
                vy = float(t["Y"][-1])
                y = vy if self.state.absolute_mode else y + vy
            if "Z" in t:
                vz = float(t["Z"][-1])
                z = vz if self.state.absolute_mode else z + vz
            if "F" in t:
                self.state.feed = int(float(t["F"][-1]))
            if "S" in t:
                self.state.spindle = int(float(t["S"][-1]))
            self.state.state = "Run"
            self._set_position(x, y, z)
            self._emit_lines(["ok"])
            self._emit_status()
            self.state.state = "Idle"
            return "", "text/plain"

        self._emit_lines(["error: unsupported command"])
        return "", "text/plain"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_http(app: Flask, host: str, port: int) -> None:
    server = make_server(host, port, app)
    server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("emulator")
    cfg = load_config(Path(__file__).with_name("config.json"))
    emulator = Emulator(cfg)

    http_server = make_server(str(cfg["http_host"]), int(cfg["http_port"]), emulator.app)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    log.info("HTTP server listening on http://%s:%s", cfg["http_host"], cfg["http_port"])

    async def ws_main() -> None:
        await emulator.hub.start(str(cfg["ws_host"]), int(cfg["ws_port"]))
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            log.info("[EMULATOR SHUTDOWN] requested")
        finally:
            await emulator.hub.stop()
            log.info("[EMULATOR SHUTDOWN] websocket server closed")

    try:
        asyncio.run(ws_main())
    except KeyboardInterrupt:
        log.info("[EMULATOR SHUTDOWN] requested")
    finally:
        with contextlib.suppress(Exception):
            http_server.shutdown()
        with contextlib.suppress(Exception):
            http_server.server_close()
        http_thread.join(timeout=2.0)
        log.info("[EMULATOR SHUTDOWN] http server stopped")
        log.info("Emulator stopped.")


if __name__ == "__main__":
    main()
