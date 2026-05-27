import asyncio
import contextlib
import ipaddress
import json
import logging
import re
import socketserver
import threading
import time
from urllib.parse import parse_qs, unquote_plus
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from flask import Flask, Response, jsonify, request
from werkzeug.serving import WSGIRequestHandler, make_server
from websockets.server import serve

TOKEN_RE = re.compile(r"([A-Za-z])([+-]?\d*\.?\d*)")
ESP401_RE = re.compile(r"\[ESP401\]\s*(.*)$", re.IGNORECASE)
HTTP_METHOD_PREFIXES = ("GET ", "POST ", "PUT ", "PATCH ", "DELETE ", "HEAD ", "OPTIONS ")

IDENTITY_LINES = [
    "[VER:1.3a.20211103:]",
    "[OPT:PHSW,64,256,63,1024]",
    "[Board:LGT LASER RAY5 V1.1]",
    "[Machine:Longer Laser Ray5]",
    "[Software:v1.9.11]",
    "[Wavelength:460nm]",
    "[Working Size:XSIZE=400.00:YSIZE=365.00]",
    "[MSG:Mode=STA:SSID=ExampleWiFi:IP=127.0.0.1:MAC=10-20-BA-5F-04-54]",
    "[MSG:Mode=AP:SSDI=ExampleRay5_AP:IP=192.168.0.1:MAC=10-20-BA-5F-04-55:password=********]",
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
        {"F": "network", "P": "Sta/SSID", "H": "Station SSID", "T": "S", "V": "ExampleWiFi", "S": "32", "M": "1"},
        {"F": "network", "P": "Sta/Password", "H": "Station Password", "T": "S", "V": "********", "S": "64", "M": "8"},
        {"F": "network", "P": "Sta/IPMode", "H": "Station IP Mode", "T": "B", "V": "0", "O": [{"DHCP": "0"}, {"Static": "1"}]},
        {"F": "network", "P": "Sta/IP", "H": "Station Static IP", "T": "A", "V": "0.0.0.0"},
        {"F": "network", "P": "Sta/Gateway", "H": "Station Static Gateway", "T": "A", "V": "0.0.0.0"},
        {"F": "network", "P": "Sta/Netmask", "H": "Station Static Mask", "T": "A", "V": "0.0.0.0"},
        {"F": "network", "P": "AP/SSID", "H": "AP SSID", "T": "S", "V": "ExampleRay5_AP", "S": "32", "M": "1"},
        {"F": "network", "P": "AP/Password", "H": "AP Password", "T": "S", "V": "********", "S": "64", "M": "8"},
        {"F": "network", "P": "AP/IP", "H": "AP Static IP", "T": "A", "V": "192.168.0.1"},
        {"F": "network", "P": "AP/Channel", "H": "AP Channel", "T": "I", "V": "1", "S": "14", "M": "1"},
        {"F": "network", "P": "System/Hostname", "H": "Hostname", "T": "S", "V": "ExampleHostname", "S": "32", "M": "1"},
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
    lines: list[str] = field(default_factory=list)

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
    run_started_at: float = 0.0


@dataclass
class ActiveJob:
    file_path: str
    source: str
    lines: list[str]
    total_lines: int
    size_bytes: int
    started_at: float
    paused: bool = False
    line_index: int = 0
    processed_lines: int = 0
    progress_percent: float = 0.0
    pause_started_at: float = 0.0
    paused_total_seconds: float = 0.0
    stop_requested: bool = False
    completed: bool = False
    aborted_reason: str = ""


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


class RawTcpHub:
    def __init__(self) -> None:
        self._clients: set[socketserver.BaseRequestHandler] = set()
        self._lock = threading.RLock()
        self.log = logging.getLogger("emulator")

    def add(self, handler: socketserver.BaseRequestHandler) -> None:
        with self._lock:
            self._clients.add(handler)

    def remove(self, handler: socketserver.BaseRequestHandler) -> None:
        with self._lock:
            self._clients.discard(handler)

    def send(self, line: str) -> None:
        payload = (str(line).rstrip("\n") + "\n").encode("utf-8", errors="replace")
        dead: list[socketserver.BaseRequestHandler] = []
        with self._lock:
            clients = list(self._clients)
        for handler in clients:
            try:
                handler.request.sendall(payload)  # type: ignore[attr-defined]
            except Exception:
                dead.append(handler)
        if dead:
            with self._lock:
                for handler in dead:
                    self._clients.discard(handler)


def _looks_like_grbl_or_raw_noise(raw_requestline: bytes) -> bool:
    if not raw_requestline:
        return False
    line = raw_requestline.decode("latin-1", errors="replace").strip()
    if not line:
        return False
    upper = line.upper()
    if upper.startswith(HTTP_METHOD_PREFIXES):
        return False
    if upper.startswith(("G0", "G1", "G2", "G3", "$", "?", "M3", "M4", "M5", "$J=", "!", "~")):
        return True
    non_printable = sum(1 for ch in line if ord(ch) < 32 or ord(ch) > 126)
    if non_printable >= 1:
        return True
    return False


class QuietHttpRequestHandler(WSGIRequestHandler):
    raw_port_hint = 8850
    _warned_clients: dict[str, float] = {}
    _warn_lock = threading.Lock()
    _warn_interval_seconds = 10.0

    def log_error(self, format: str, *args: Any) -> None:
        raw_line = getattr(self, "raw_requestline", b"")
        if _looks_like_grbl_or_raw_noise(raw_line):
            peer = self.client_address[0] if self.client_address else "unknown"
            now = time.time()
            should_warn = False
            with self._warn_lock:
                prev = self._warned_clients.get(peer, 0.0)
                if (now - prev) >= self._warn_interval_seconds:
                    self._warned_clients[peer] = now
                    should_warn = True
            if should_warn:
                logging.getLogger("emulator").warning(
                    "Raw GRBL traffic received on HTTP port %s. Configure Tibbo/LightBurn to connect to raw TCP port %s instead.",
                    getattr(self.server, "server_port", 8848),
                    self.raw_port_hint,
                )
            return
        super().log_error(format, *args)


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


def strip_gcode_comments(line: str) -> str:
    text = str(line or "")
    text = re.sub(r"\(.*?\)", "", text)
    if ";" in text:
        text = text.split(";", 1)[0]
    return text.strip()


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
        self.job_line_delay_seconds = max(0.001, float(cfg.get("job_line_delay_seconds", 0.05)))
        self.job_min_duration_seconds = max(0.0, float(cfg.get("job_min_duration_seconds", 2.0)))
        self.job_max_duration_seconds = max(self.job_min_duration_seconds, float(cfg.get("job_max_duration_seconds", 600.0)))
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
        self.raw_hub = RawTcpHub()
        self.app = Flask(__name__)
        self.eeprom_data = self._load_eeprom_state()
        self.grbl_settings = self._load_grbl_settings_state()
        self.files: dict[str, UploadedFile] = {}
        self._files_lock = threading.RLock()
        self._seed_files()
        self._load_persisted_uploads()
        self._run_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.active_job: Optional[ActiveJob] = None
        self._active_job_thread: Optional[threading.Thread] = None
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
        try:
            decoded = content.decode("utf-8", errors="ignore")
        except Exception:
            decoded = ""
        lines = decoded.splitlines()
        rec = UploadedFile(
            filename=filename,
            path=norm,
            size=len(content),
            uploaded_at=now_iso(),
            content=content,
            compressed=filename.lower().endswith(".gz"),
            lines=lines,
        )
        with self._files_lock:
            self.files[norm] = rec
            if self.uploads_persist and source in {"upload", "persist"}:
                (self.uploads_dir / filename).write_bytes(content)
        self.log.info("[UPLOAD STORED] file=%s size=%d storage_key=%s", norm, rec.size, norm)
        return rec

    def _delete_file(self, path: str) -> bool:
        norm = self._normalize_sd_path(path)
        with self._files_lock:
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

    def _normalize_incoming_command(self, cmd: str) -> str:
        text = str(cmd or "").strip()
        prev = None
        cur = text
        for _ in range(2):
            try:
                dec = unquote_plus(cur)
            except Exception:
                dec = cur
            if dec == cur or dec == prev:
                cur = dec
                break
            prev = cur
            cur = dec
        text = cur.strip().replace("\\", "/")
        if len(text) >= 2 and ((text[0] == text[-1] == '"') or (text[0] == text[-1] == "'")):
            text = text[1:-1].strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\[\s*ESP220\s*\]", "[ESP220]", text, flags=re.IGNORECASE)
        text = re.sub(r"(\[ESP220\])\s+", r"\1", text, flags=re.IGNORECASE)
        text = re.sub(r"(\$SD/RUNZIP?=)\s+", r"\1", text, flags=re.IGNORECASE)
        text = re.sub(r"(\[ESP220\]/+)", "[ESP220]/", text, flags=re.IGNORECASE)
        text = re.sub(r"(\$SD/RUNZIP?=/+)/", r"\1", text, flags=re.IGNORECASE)
        return text

    def _resolve_file_for_run(self, requested: str) -> tuple[str | None, list[str]]:
        requested_norm = self._normalize_sd_path(requested)
        basename = Path(requested_norm).name
        candidates = [
            requested_norm,
            requested_norm.lstrip("/"),
            f"/{requested_norm.lstrip('/')}",
            basename,
            f"/{basename}",
        ]
        seen: set[str] = set()
        dedup_candidates: list[str] = []
        for c in candidates:
            cc = str(c or "").strip()
            if cc and cc not in seen:
                seen.add(cc)
                dedup_candidates.append(cc)
        self.log.info("[JOB LOOKUP] requested=%s candidates=%s", requested_norm, dedup_candidates)
        with self._files_lock:
            keys = list(self.files.keys())
            for candidate in dedup_candidates:
                cand_norm = self._normalize_sd_path(candidate)
                if cand_norm in self.files:
                    self.log.info("[JOB LOOKUP OK] file=%s storage_key=%s", requested_norm, cand_norm)
                    return cand_norm, dedup_candidates
        self.log.warning("[JOB LOOKUP FAILED] requested=%s available=%s", requested_norm, keys)
        return None, dedup_candidates

    def _extract_run_command(self, upper: str, stripped: str) -> tuple[str, str] | None:
        m_esp = re.match(r"^\[ESP220\]\s*/?(.*)$", stripped, flags=re.IGNORECASE)
        if m_esp:
            raw_file = str(m_esp.group(1) or "").strip()
            if raw_file:
                return "esp220", raw_file
        if re.match(r"^\$SD/RUNZIP\s*=", upper, flags=re.IGNORECASE):
            raw_file = str(stripped.split("=", 1)[1] if "=" in stripped else "").strip().lstrip("/")
            if raw_file:
                return "sd_runzip", raw_file
        if re.match(r"^\$SD/RUN\s*=", upper, flags=re.IGNORECASE):
            raw_file = str(stripped.split("=", 1)[1] if "=" in stripped else "").strip().lstrip("/")
            if raw_file:
                return "sd_run", raw_file
        return None

    def _setup_routes(self) -> None:
        @self.app.get("/command")
        def command() -> Response:
            raw_cmd = ""
            try:
                raw_qs = request.query_string.decode("utf-8", errors="replace")
                parsed = parse_qs(raw_qs, keep_blank_values=True)
                raw_cmd = str(parsed.get("commandText", [""])[0])
            except Exception:
                raw_cmd = ""
            cmd = request.args.get("commandText", "")
            pageid = request.args.get("PAGEID", "")
            self.log.info("[HTTP COMMAND RAW] commandText=%s", raw_cmd)
            self.log.info("[HTTP COMMAND DECODED] command=%s pageid=%s", cmd, pageid)
            self.log.info("[HTTP COMMAND] command=%s pageid=%s", cmd, pageid)
            body, content_type = self._handle_command(cmd)
            return Response(body, status=200, mimetype=content_type)

        @self.app.get("/files")
        def files() -> Response:
            req_path = self._normalize_sd_path(request.args.get("path", "/"))
            with self._files_lock:
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

        @self.app.get("/debug/job")
        def debug_job() -> Response:
            with self._state_lock:
                job = self.active_job
                state = self.state
                payload = {
                    "state": state.state,
                    "active_job": job.file_path if job else "",
                    "progress_percent": float(job.progress_percent) if job else 0.0,
                    "line_index": int(job.line_index) if job else 0,
                    "total_lines": int(job.total_lines) if job else 0,
                    "elapsed_seconds": float(self._job_elapsed_seconds_locked(job)) if job else 0.0,
                    "paused": bool(job.paused) if job else False,
                    "air_assist": bool(state.air_pump_state == "on"),
                    "spindle": int(state.spindle),
                    "mpos": {"x": float(state.x), "y": float(state.y), "z": float(state.z)},
                }
            return jsonify(payload)

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
        with self._state_lock:
            coords = f"{self.state.x:.3f},{self.state.y:.3f}" if self.status_axes == 2 else f"{self.state.x:.3f},{self.state.y:.3f},{self.state.z:.3f}"
            base = f"<{self.state.state}|MPos:{coords}|FS:{self.state.feed},{self.state.spindle}|Ov:100,100,100"
            accessory_flags: list[str] = []
            if int(self.state.spindle or 0) > 0:
                accessory_flags.append("S")
            if str(self.state.air_pump_state or "").lower() == "on":
                accessory_flags.append("F")
            if accessory_flags:
                base += f"|A:{''.join(accessory_flags)}"
            if self.state.current_file and self.state.state in {"Run", "Hold"}:
                base += f"|SD:{self.state.sd_percent:0.2f},{self.state.current_file}"
                elapsed = self._job_elapsed_seconds_locked(self.active_job)
                if elapsed >= 0.0:
                    base += f"|time:{elapsed:0.3f}"
            base += "|Heap:52012>"
            return base

    def _emit_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.hub.send(line)
            self.raw_hub.send(line)

    def _emit_status(self) -> None:
        line = self._status_line()
        self.last_status_line = line
        self.ws_status_count += 1
        self._emit_lines([line])

    def _job_elapsed_seconds_locked(self, job: Optional[ActiveJob]) -> float:
        if not job:
            return -1.0
        now = time.time()
        paused_extra = (now - job.pause_started_at) if (job.paused and job.pause_started_at > 0.0) else 0.0
        elapsed = max(0.0, now - job.started_at - job.paused_total_seconds - paused_extra)
        return elapsed

    def _enter_alarm(self, reason: str) -> None:
        self.log.info("[EMULATOR STOP] reason=%s", reason)
        with self._state_lock:
            self._stop_active_job_locked(reason=reason, to_alarm=True)
        self.log.info("[EMULATOR ALARM] state=Alarm")
        self._emit_status()

    def _clear_alarm(self) -> None:
        self.log.info("[EMULATOR UNLOCK] command=$X")
        with self._state_lock:
            self.active_job = None
            self.state.current_file = ""
            self.state.sd_percent = 0.0
            self._set_machine_state("Idle")
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

    def _set_machine_state(self, new_state: str) -> None:
        normalized = str(new_state or "Idle").strip() or "Idle"
        self.state.state = normalized
        if normalized == "Run":
            if not isinstance(self.state.run_started_at, (int, float)) or self.state.run_started_at <= 0:
                self.state.run_started_at = time.time()
        elif normalized not in {"Hold"}:
            self.state.run_started_at = 0.0

    def _stop_active_job_locked(self, reason: str, to_alarm: bool) -> None:
        job = self.active_job
        if job:
            job.stop_requested = True
            job.aborted_reason = reason
            self.log.info("[JOB STOP] file=%s reason=%s progress=%.2f", job.file_path, reason, job.progress_percent)
        self.state.spindle = 0
        self.state.feed = 0
        if to_alarm:
            self._set_machine_state("Alarm")
        else:
            self._set_machine_state("Idle")
        if not to_alarm:
            self.state.current_file = ""
            self.state.sd_percent = 0.0
            self.active_job = None

    def _start_job(self, file_path: str, source: str) -> bool:
        with self._state_lock:
            if self.active_job and self.state.state in {"Run", "Hold"}:
                self._emit_lines(["error: job already running"])
                return False
            with self._files_lock:
                rec = self.files.get(file_path)
            if not rec:
                self._emit_lines(["error:file not found"])
                self.log.warning("[EMULATOR RUN ERROR] reason=file_not_found filename=%s", file_path)
                return False
            lines = list(rec.lines or rec.content.decode("utf-8", errors="ignore").splitlines())
            total_lines = max(1, len(lines))
            self.active_job = ActiveJob(
                file_path=file_path,
                source=source,
                lines=lines,
                total_lines=total_lines,
                size_bytes=rec.size,
                started_at=time.time(),
            )
            self.state.current_file = file_path
            self.state.sd_percent = 0.0
            self._set_machine_state("Run")
            self.state.feed = 0
            self.log.info("[JOB START] file=%s lines=%d size=%d source=%s", file_path, len(lines), rec.size, source)
        self._emit_lines(["ok", f"[MSG:Running {Path(file_path).name}]"])
        self._emit_status()
        t = threading.Thread(target=self._job_runner_loop, daemon=True)
        self._active_job_thread = t
        t.start()
        return True

    def _apply_gcode_runtime_effects_locked(self, line: str) -> None:
        upper = line.upper()
        word = line_word(upper)
        tokens = parse_tokens(line)

        if word in {"G0", "G00", "G1", "G01"}:
            x = self.state.x
            y = self.state.y
            z = self.state.z
            if "X" in tokens:
                vx = float(tokens["X"][-1])
                x = vx if self.state.absolute_mode else x + vx
            if "Y" in tokens:
                vy = float(tokens["Y"][-1])
                y = vy if self.state.absolute_mode else y + vy
            if "Z" in tokens:
                vz = float(tokens["Z"][-1])
                z = vz if self.state.absolute_mode else z + vz
            if "F" in tokens:
                self.state.feed = int(float(tokens["F"][-1]))
            if "S" in tokens:
                self.state.spindle = int(float(tokens["S"][-1]))
            self._set_position(x, y, z)
            return

        if upper in {"G90", "G91"}:
            self.state.absolute_mode = upper == "G90"
            return
        if upper.startswith("M3") or upper.startswith("M4"):
            if "S" in tokens:
                self.state.spindle = int(float(tokens["S"][-1]))
            elif self.state.spindle <= 0:
                self.state.spindle = 1
            return
        if upper.startswith("M5"):
            self.state.spindle = 0
            return
        if upper == "M8":
            self._set_air_pump_state("on", "M8")
            return
        if upper == "M9":
            self._set_air_pump_state("off", "M9")
            return
        if upper in {"M2", "M30"}:
            return

    def _line_delay_seconds(self, total_lines: int) -> float:
        if total_lines <= 0:
            return self.job_line_delay_seconds
        per_line_from_min = self.job_min_duration_seconds / float(total_lines)
        per_line_from_max = self.job_max_duration_seconds / float(total_lines)
        d = max(self.job_line_delay_seconds, per_line_from_min)
        d = min(d, per_line_from_max)
        return max(0.001, d)

    def _job_runner_loop(self) -> None:
        last_status_emit = 0.0
        while True:
            with self._state_lock:
                job = self.active_job
                if not job:
                    return
                if job.stop_requested:
                    return
                if job.paused:
                    if time.time() - last_status_emit >= 0.75:
                        self._set_machine_state("Hold")
                        self.state.feed = 0
                        last_status_emit = time.time()
                        self._emit_status()
                    do_sleep = 0.05
                    do_line = None
                elif job.line_index >= job.total_lines:
                    job.progress_percent = 100.0
                    self.state.sd_percent = 100.0
                    self._set_machine_state("Run")
                    self._emit_status()
                    with self._files_lock:
                        rec = self.files.get(job.file_path)
                    if rec:
                        rec.last_run_at = now_iso()
                        rec.run_count += 1
                    elapsed = self._job_elapsed_seconds_locked(job)
                    self.log.info("[JOB COMPLETE] file=%s elapsed=%.3f lines=%d", job.file_path, elapsed, job.total_lines)
                    self._set_machine_state("Idle")
                    self.state.feed = 0
                    self.state.spindle = 0
                    self.state.current_file = ""
                    self.state.sd_percent = 0.0
                    self.active_job = None
                    self._emit_status()
                    return
                else:
                    do_line = job.lines[job.line_index] if job.line_index < len(job.lines) else ""
                    job.line_index += 1
                    do_sleep = self._line_delay_seconds(job.total_lines)
            if do_line is None:
                time.sleep(do_sleep)
                continue

            clean = strip_gcode_comments(do_line)
            upper = clean.upper()
            dwell_seconds = 0.0
            with self._state_lock:
                job = self.active_job
                if not job or job.stop_requested:
                    return
                if clean:
                    self._apply_gcode_runtime_effects_locked(clean)
                    if upper.startswith("G4"):
                        tok = parse_tokens(clean)
                        if "P" in tok:
                            dwell_seconds = min(1.0, max(0.0, float(tok["P"][-1]) / 1000.0))
                        elif "S" in tok:
                            dwell_seconds = min(1.0, max(0.0, float(tok["S"][-1])))
                if clean:
                    job.processed_lines += 1
                job.progress_percent = max(0.0, min(100.0, (float(job.line_index) / float(job.total_lines)) * 100.0))
                self.state.sd_percent = job.progress_percent
                self._set_machine_state("Run")
                now = time.time()
                if now - last_status_emit >= 0.75:
                    last_status_emit = now
                    self._emit_status()
            time.sleep(max(do_sleep, dwell_seconds))

    def _esp800_raw(self) -> str:
        return (
            "FW version:1.3a (20211103) # FW target:grbl-embedded  # FW HW:Direct SD  # primary sd:/sd "
            "# secondary sd:none # authentication:no # webcommunication: Sync: 8849:192.168.0.1,127.0.0.1 "
            "# hostname:ExampleHostname # axis:2"
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
        run_cmd = self._extract_run_command(upper, stripped)
        if run_cmd is not None:
            mode, raw_file = run_cmd
            resolved, _ = self._resolve_file_for_run(raw_file)
            log_file = self._normalize_sd_path(raw_file)
            if mode == "esp220":
                self.log.info("[RUN COMMAND DETECTED] mode=esp220 file=%s", log_file)
            elif mode == "sd_run":
                self.log.info("[RUN COMMAND DETECTED] mode=sd_run file=%s", log_file)
            else:
                self.log.info("[RUN COMMAND DETECTED] mode=sd_runzip file=%s", log_file)
            if not resolved:
                self._emit_lines(["error: file not found"])
                return "", "text/plain"
            start_mode = "esp220" if mode == "esp220" else ("runzip" if mode == "sd_runzip" else "run")
            self._start_job(resolved, start_mode)
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
        stripped = self._normalize_incoming_command(str(cmd or ""))
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
            if self._extract_run_command(upper, stripped) is not None:
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
                        {"SSID": "ExampleWiFi", "SIGNAL": "-45", "IS_PROTECTED": "1"},
                        {"SSID": "ExampleRay5_AP", "SIGNAL": "-30", "IS_PROTECTED": "1"},
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
            spindle_word = "M3" if int(self.state.spindle or 0) > 0 else "M5"
            spindle_val = int(self.state.spindle or 0)
            self._emit_lines([f"[GC:G0 G54 G17 G21 G90 G94 {spindle_word} {air_word} T0 F0 S{spindle_val}]", "ok"])
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
            with self._state_lock:
                if self.active_job:
                    if not self.active_job.paused:
                        self.active_job.paused = True
                        self.active_job.pause_started_at = time.time()
                    self._set_machine_state("Hold")
                    self.log.info("[JOB PAUSE] file=%s progress=%.2f", self.active_job.file_path, self.active_job.progress_percent)
                else:
                    self._set_machine_state("Hold")
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper == "~":
            with self._state_lock:
                if self.active_job:
                    if self.active_job.paused:
                        self.active_job.paused = False
                        if self.active_job.pause_started_at > 0:
                            self.active_job.paused_total_seconds += max(0.0, time.time() - self.active_job.pause_started_at)
                        self.active_job.pause_started_at = 0.0
                    self._set_machine_state("Run")
                    self.log.info("[JOB RESUME] file=%s progress=%.2f", self.active_job.file_path, self.active_job.progress_percent)
                else:
                    self._set_machine_state("Run")
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper == "$H" or upper == "G28":
            self._set_machine_state("Idle")
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
                self._set_machine_state("Idle")
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
            self._set_machine_state("Run")
            self._emit_lines(["ok"])
            self._emit_status()
            return "", "text/plain"

        if upper.startswith("M5"):
            with self._state_lock:
                self.state.spindle = 0
                if not self.active_job:
                    self._set_machine_state("Idle")
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
            self._set_machine_state("Run")
            self._emit_lines(["ok"])
            self._emit_status()
            self._set_machine_state("Idle")
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
            self._set_machine_state("Run")
            self._set_position(x, y, z)
            self._emit_lines(["ok"])
            self._emit_status()
            self._set_machine_state("Idle")
            return "", "text/plain"

        self._emit_lines(["error: unsupported command"])
        return "", "text/plain"


class RawTcpHandler(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.log = logging.getLogger("emulator")
        self.buffer = bytearray()
        self.server.emulator.raw_hub.add(self)  # type: ignore[attr-defined]
        self._probe_logged = False
        self.log.info("[RAW CONNECT] peer=%s", self.client_address)

    def handle(self) -> None:
        while True:
            try:
                data = self.request.recv(4096)
            except OSError:
                break
            if not data:
                break
            for b in data:
                if b in (10, 13):
                    self._flush_line()
                    continue
                self.buffer.append(b)
                if len(self.buffer) > 8192:
                    self.buffer.clear()

    def finish(self) -> None:
        self._flush_line()
        self.server.emulator.raw_hub.remove(self)  # type: ignore[attr-defined]
        self.log.info("[RAW DISCONNECT] peer=%s", self.client_address)

    def _flush_line(self) -> None:
        if not self.buffer:
            return
        raw = bytes(self.buffer)
        self.buffer.clear()
        line = raw.decode("latin-1", errors="replace").strip()
        if not line:
            return
        upper = line.upper()
        if not re.match(r"^(\$|G|M|\?|!|~|\x18)", upper):
            if not self._probe_logged:
                self.log.warning("[RAW PROBE BYTES] peer=%s bytes=%r", self.client_address, raw[:64])
                self._probe_logged = True
            return
        self.log.info("[RAW RX] peer=%s line=%s", self.client_address, line)
        body, _ctype = self.server.emulator._handle_command(line)  # type: ignore[attr-defined]
        if body:
            for out_line in str(body).replace("\r", "").split("\n"):
                out_line = out_line.strip()
                if out_line:
                    self.server.emulator.raw_hub.send(out_line)  # type: ignore[attr-defined]


class ThreadedRawTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], emulator: Emulator):
        self.emulator = emulator
        super().__init__(addr, RawTcpHandler)


def load_config(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        data = json.loads(example_path.read_text(encoding="utf-8"))
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return data
    raise FileNotFoundError(f"Missing config file: {path} (and no {example_path.name} fallback)")


def _validate_ports_or_exit(http_port: int, ws_port: int, raw_port: int, log) -> None:
    if len({http_port, ws_port, raw_port}) != 3:
        raise RuntimeError(
            f"Port conflict: http_port={http_port}, ws_port={ws_port}, raw_port={raw_port}. "
            "Use HTTP 8848, WebSocket 8849, Raw TCP 8850."
        )


def run_http(app: Flask, host: str, port: int) -> None:
    server = make_server(host, port, app)
    server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("emulator")
    cfg = load_config(Path(__file__).with_name("config.json"))
    emulator = Emulator(cfg)
    raw_host = str(cfg.get("raw_host", "127.0.0.1"))
    raw_port = int(cfg.get("raw_port", 8850))
    ws_port = int(cfg.get("ws_port", 8849))
    http_port = int(cfg.get("http_port", 8848))
    try:
        _validate_ports_or_exit(http_port=http_port, ws_port=ws_port, raw_port=raw_port, log=log)
    except Exception as exc:
        log.error("%s", exc)
        raise SystemExit(1)
    QuietHttpRequestHandler.raw_port_hint = raw_port

    http_server = make_server(
        str(cfg["http_host"]),
        http_port,
        emulator.app,
        request_handler=QuietHttpRequestHandler,
    )
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    log.info("HTTP server listening on http://%s:%s", cfg["http_host"], http_port)
    raw_server = ThreadedRawTcpServer((raw_host, raw_port), emulator)
    raw_thread = threading.Thread(target=raw_server.serve_forever, daemon=True)
    raw_thread.start()
    log.info("Raw GRBL TCP server listening on %s:%s", raw_host, raw_port)
    log.info("Raw GRBL TCP is optional (advanced bridge/Tibbo/raw-client testing).")
    log.info("Normal Ray5 Pilot emulator testing uses HTTP 8848 and WebSocket 8849.")

    async def ws_main() -> None:
        await emulator.hub.start(str(cfg["ws_host"]), ws_port)
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
        with contextlib.suppress(Exception):
            raw_server.shutdown()
        with contextlib.suppress(Exception):
            raw_server.server_close()
        http_thread.join(timeout=2.0)
        raw_thread.join(timeout=2.0)
        log.info("[EMULATOR SHUTDOWN] http server stopped")
        log.info("[EMULATOR SHUTDOWN] raw tcp server stopped")
        log.info("Emulator stopped.")


if __name__ == "__main__":
    main()
