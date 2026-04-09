import logging
import subprocess
from pathlib import Path

import storage
from result import Err, Ok, Result

log = logging.getLogger(__name__)

SUPPORTED = {".epub", ".mobi", ".docx", ".pdf"}


def expected_output_name(filename: str) -> str:
    """Predict the output filename after conversion."""
    p = Path(filename)
    if p.suffix.lower() in (".epub", ".mobi", ".docx"):
        return f"{p.stem}.kepub.epub"
    return p.name


def _find_kepub(directory: Path, stem: str) -> Result[Path]:
    """Locate the .kepub.epub file kepubify produced."""
    for f in directory.iterdir():
        if f.name.endswith(".kepub.epub") and f.name.startswith(stem):
            return Ok(f)
    for f in directory.iterdir():
        if f.name.endswith(".kepub.epub"):
            return Ok(f)
    return Err(f"kepubify produced no output for stem '{stem}'")


def _run(cmd: list[str]) -> Result[None]:
    """Run a subprocess, returning Ok or Err with stderr."""
    log.info("Running: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return Ok(None)
    except subprocess.CalledProcessError as e:
        msg = e.stderr.decode(errors="replace").strip()
        log.error("Command failed: %s\n%s", " ".join(cmd), msg)
        return Err(f"Command failed: {msg}")


def _kepubify(epub_path: Path) -> Result[Path]:
    """Run kepubify and return the output path."""
    output_dir = epub_path.parent
    result = _run(["kepubify", "--inplace", "-o", str(output_dir), str(epub_path)])
    if isinstance(result, Err):
        return result
    return _find_kepub(output_dir, epub_path.stem)


def _cleanup(*paths: Path) -> None:
    for p in paths:
        p.unlink(missing_ok=True)


def process(input_path: Path) -> Result[str]:
    """Convert a file and upload to S3. Returns Ok(s3_key) or Err(message)."""
    suffix = input_path.suffix.lower()
    log.info("Processing %s (type: %s)", input_path.name, suffix)

    try:
        if suffix == ".epub":
            match _kepubify(input_path):
                case Err() as e:
                    return e
                case Ok(kepub):
                    result = storage.upload(kepub)
                    _cleanup(kepub)
                    return result

        elif suffix in (".mobi", ".docx"):
            epub_path = input_path.parent / f"{input_path.stem}.epub"
            match _run(["ebook-convert", str(input_path), str(epub_path)]):
                case Err() as e:
                    return e
            match _kepubify(epub_path):
                case Err() as e:
                    _cleanup(epub_path)
                    return e
                case Ok(kepub):
                    result = storage.upload(kepub)
                    _cleanup(kepub, epub_path)
                    return result

        elif suffix == ".pdf":
            return storage.upload(input_path)

        else:
            return Err(f"Unsupported format: {suffix}")

    finally:
        _cleanup(input_path)
