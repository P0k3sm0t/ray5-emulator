from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"

INCLUDE_PATHS = [
    "ray5_emulator.py",
    "README.md",
    "requirements.txt",
    "start_emulator.bat",
    "start_emulator.sh",
    "config.example.json",
    "LICENSE",
    "tools/make_release_zip.py",
    "tools/safety_check.py",
    "tools",
]

EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "update_work",
    "emulator_uploads",
    "emulator_storage",
    "logs",
}

EXCLUDE_FILE_NAMES = {
    "config.json",
    "emulator_eeprom_state.json",
    "emulator_grbl_settings_state.json",
}

EXCLUDE_SUFFIXES = {".log", ".pyc", ".pyo"}


def _should_exclude(path: Path) -> bool:
    rel = path.relative_to(PROJECT_ROOT)
    for part in rel.parts:
        if part in EXCLUDE_DIR_NAMES:
            return True
    if path.name in EXCLUDE_FILE_NAMES:
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False


def _iter_included_files() -> tuple[list[Path], int]:
    files: list[Path] = []
    skipped = 0
    for rel in INCLUDE_PATHS:
        src = PROJECT_ROOT / rel
        if not src.exists():
            continue
        if src.is_file():
            if _should_exclude(src):
                skipped += 1
            else:
                files.append(src)
            continue
        for child in src.rglob("*"):
            if not child.is_file():
                continue
            if _should_exclude(child):
                skipped += 1
                continue
            files.append(child)
    unique: list[Path] = []
    seen: set[str] = set()
    for f in sorted(files, key=lambda p: p.relative_to(PROJECT_ROOT).as_posix()):
        key = str(f.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(f)
    return unique, skipped


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def main() -> int:
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    files, skipped = _iter_included_files()
    package_root = "ray5-emulator-release"
    zip_path = DIST_DIR / f"{package_root}.zip"
    sha_path = DIST_DIR / f"{package_root}.zip.sha256.txt"

    print(f"[RELEASE ZIP] writing {zip_path}")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src in files:
            arcname = f"{package_root}/{src.relative_to(PROJECT_ROOT).as_posix()}"
            zf.write(src, arcname)

    sha = _sha256_file(zip_path)
    sha_path.write_text(f"{sha}  {zip_path.name}\n", encoding="utf-8")
    print(f"[RELEASE ZIP] included={len(files)} skipped={skipped}")
    print(f"[RELEASE ZIP] sha256: {sha}")
    print(f"[RELEASE ZIP] checksum file: {sha_path}")
    print(f"[RELEASE ZIP] done: {zip_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
