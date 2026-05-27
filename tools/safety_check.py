#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "ray5_emulator.py",
    "README.md",
    "requirements.txt",
    "config.example.json",
    "tools/make_release_zip.py",
    "tools/safety_check.py",
    ".gitignore",
]

TRACKED_FORBIDDEN_EXACT = {
    "config.json",
    "emulator_eeprom_state.json",
    "emulator_grbl_settings_state.json",
}

TRACKED_FORBIDDEN_PREFIXES = [
    "__pycache__/",
    "emulator_uploads/",
    "emulator_storage/",
    "dist/",
]

TRACKED_FORBIDDEN_SUFFIXES = [".log", ".pyc", ".pyo"]

SAFE_PLACEHOLDER_VALUES = {"YOUR_RAY5_IP", "ExampleWiFi", "ExampleRay5_AP", "ExampleHostname", "********"}

SUSPICIOUS_PATTERNS = [
    ("github token", re.compile(r"\bghp_[A-Za-z0-9]{20,}\b")),
    ("bearer token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_\.=]{16,}")),
    ("api key assignment", re.compile(r"(?i)\b(api[_-]?key|token|password)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]")),
    ("rtsp inline credentials", re.compile(r"rtsp://[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)),
    ("prior local test IP", re.compile(r"\b10\.0\.0\.195\b")),
]

SCAN_EXTS = {".py", ".md", ".json", ".txt", ".bat", ".sh", ".toml", ".ini", ".yml", ".yaml", ".js", ".html", ".css"}
SKIP_DIRS = {".git", "__pycache__", "dist", "build", "emulator_uploads", "emulator_storage"}


class CheckResult:
    def __init__(self) -> None:
        self.fail = False

    def pass_(self, msg: str) -> None:
        print(f"[PASS] {msg}")

    def warn(self, msg: str) -> None:
        print(f"[WARN] {msg}")

    def fail_(self, msg: str) -> None:
        self.fail = True
        print(f"[FAIL] {msg}")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True)


def _tracked_files() -> tuple[set[str], bool]:
    git = shutil.which("git")
    if not git:
        return set(), False
    cp = _run([git, "rev-parse", "--is-inside-work-tree"])
    if cp.returncode != 0 or "true" not in (cp.stdout or "").lower():
        return set(), False
    cp2 = _run([git, "ls-files"])
    if cp2.returncode != 0:
        return set(), False
    out = set()
    for line in (cp2.stdout or "").splitlines():
        s = line.strip().replace("\\", "/")
        if s:
            out.add(s)
    return out, True


def check_required_files(r: CheckResult) -> None:
    missing = [p for p in REQUIRED_FILES if not (ROOT / p).exists()]
    if missing:
        r.fail_("Required source files")
        for m in missing:
            print(f"  - missing: {m}")
    else:
        r.pass_("Required source files")


def check_gitignore_coverage(r: CheckResult) -> None:
    gi = ROOT / ".gitignore"
    if not gi.exists():
        r.fail_("GitHub ignore coverage")
        print("  - missing: .gitignore")
        return
    txt = gi.read_text(encoding="utf-8", errors="ignore")
    required = [
        "__pycache__/",
        "*.pyc",
        ".venv/",
        "venv/",
        "config.json",
        "emulator_eeprom_state.json",
        "emulator_grbl_settings_state.json",
        "emulator_uploads/",
        "emulator_storage/",
        "dist/",
        "build/",
        ".vscode/",
        ".idea/",
    ]
    miss = [x for x in required if x not in txt]
    if miss:
        r.fail_("GitHub ignore coverage")
        for m in miss:
            print(f"  - missing ignore rule: {m}")
    else:
        r.pass_("GitHub ignore coverage")


def check_config_example_safety(r: CheckResult) -> None:
    p = ROOT / "config.example.json"
    if not p.exists():
        r.fail_("Config example safety")
        print("  - missing: config.example.json")
        return
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        r.fail_("Config example safety")
        print(f"  - invalid JSON: {exc}")
        return
    problems = []
    if int(data.get("http_port", -1)) != 8848:
        problems.append("http_port must be 8848")
    if int(data.get("ws_port", -1)) != 8849:
        problems.append("ws_port must be 8849")
    if int(data.get("raw_port", -1)) != 8850:
        problems.append("raw_port must be 8850")
    if str(data.get("http_host", "")).strip() != "127.0.0.1":
        problems.append("http_host should be 127.0.0.1")
    if str(data.get("ws_host", "")).strip() != "127.0.0.1":
        problems.append("ws_host should be 127.0.0.1")
    if str(data.get("raw_host", "")).strip() != "127.0.0.1":
        problems.append("raw_host should be 127.0.0.1")
    text = p.read_text(encoding="utf-8", errors="ignore")
    if "10.0.0.195" in text:
        problems.append("contains prior local test IP 10.0.0.195")
    if problems:
        r.fail_("Config example safety")
        for pmsg in problems:
            print(f"  - {pmsg}")
    else:
        r.pass_("Config example safety")


def check_runtime_private_not_tracked(r: CheckResult, tracked: set[str], git_ok: bool) -> None:
    if not git_ok:
        r.warn("Git not available or not a git repo; skipping tracked-file checks")
        return
    bad = []
    for t in tracked:
        tl = t.lower()
        if t in TRACKED_FORBIDDEN_EXACT:
            bad.append(t)
            continue
        if any(t.startswith(pref) for pref in TRACKED_FORBIDDEN_PREFIXES):
            bad.append(t)
            continue
        if any(tl.endswith(suf) for suf in TRACKED_FORBIDDEN_SUFFIXES):
            bad.append(t)
    if bad:
        r.fail_("Runtime/private files tracked")
        for b in sorted(bad):
            print(f"  - tracked: {b}")
    else:
        r.pass_("Runtime/private files tracked")


def _iter_text_files():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if rel.as_posix() == "tools/safety_check.py":
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in SCAN_EXTS or p.name in {"README", "LICENSE"}:
            yield p


def check_content_sanitization(r: CheckResult) -> None:
    hits = []
    for p in _iter_text_files():
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for label, rx in SUSPICIOUS_PATTERNS:
            for m in rx.finditer(txt):
                line = txt.count("\n", 0, m.start()) + 1
                hits.append(f"{p.relative_to(ROOT)}:{line}: {label}")
    if hits:
        r.fail_("Content sanitization")
        for h in hits[:120]:
            print(f"  - {h}")
    else:
        r.pass_("Content sanitization")


def check_local_generated_warn(r: CheckResult) -> None:
    maybe = []
    for rel in ["config.json", "emulator_uploads", "emulator_storage", "dist", "__pycache__", "emulator_eeprom_state.json", "emulator_grbl_settings_state.json"]:
        if (ROOT / rel).exists():
            maybe.append(rel)
    if maybe:
        r.warn("Local generated files present but ignored")
        for m in maybe:
            print(f"  - present: {m}")
    else:
        r.pass_("Local generated files present but ignored")


def check_release_builder_coverage(r: CheckResult) -> None:
    p = ROOT / "tools" / "make_release_zip.py"
    if not p.exists():
        r.fail_("Release builder coverage")
        print("  - missing: tools/make_release_zip.py")
        return
    txt = p.read_text(encoding="utf-8", errors="ignore")
    required_include = [
        '"ray5_emulator.py"',
        '"README.md"',
        '"requirements.txt"',
        '"config.example.json"',
        '"tools/make_release_zip.py"',
        '"tools/safety_check.py"',
    ]
    required_exclude = [
        '".git"',
        '"__pycache__"',
        '"config.json"',
        '"emulator_uploads"',
        '"emulator_storage"',
        '"emulator_eeprom_state.json"',
        '"emulator_grbl_settings_state.json"',
        '"dist"',
        '"build"',
        '".venv"',
        '"venv"',
        '"update_work"',
    ]
    miss = []
    for token in required_include:
        if token not in txt:
            miss.append(f"missing include token: {token}")
    for token in required_exclude:
        if token not in txt:
            miss.append(f"missing exclude token: {token}")
    if miss:
        r.fail_("Release builder coverage")
        for m in miss:
            print(f"  - {m}")
    else:
        r.pass_("Release builder coverage")


def check_raw_tcp_docs(r: CheckResult) -> None:
    readme = ROOT / "README.md"
    if not readme.exists():
        r.fail_("Raw TCP optional docs")
        print("  - missing: README.md")
        return
    txt = readme.read_text(encoding="utf-8", errors="ignore")
    required = [
        "HTTP/API: `127.0.0.1:8848`",
        "WebSocket status: `127.0.0.1:8849`",
        "Raw GRBL TCP: `127.0.0.1:8850`",
        "raw_host` / `raw_port` are for optional advanced",
        "Normal Ray5 Pilot emulator testing uses only",
    ]
    miss = [x for x in required if x not in txt]
    if miss:
        r.fail_("Raw TCP optional docs")
        for m in miss:
            print(f"  - missing doc marker: {m}")
    else:
        r.pass_("Raw TCP optional docs")


def main() -> int:
    print("Ray5 Emulator Safety Check\n")
    r = CheckResult()
    tracked, git_ok = _tracked_files()
    check_required_files(r)
    check_gitignore_coverage(r)
    check_config_example_safety(r)
    check_runtime_private_not_tracked(r, tracked, git_ok)
    check_content_sanitization(r)
    check_release_builder_coverage(r)
    check_raw_tcp_docs(r)
    check_local_generated_warn(r)
    print("\nResult: FAIL" if r.fail else "\nResult: PASS")
    return 1 if r.fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
