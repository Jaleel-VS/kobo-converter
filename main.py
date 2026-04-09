import os
import shutil
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import StreamingResponse

app = FastAPI()

UPLOAD_DIR = Path("/app/books/uploads")
PROCESSED_DIR = Path("/app/books/processed")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

S3_BUCKET = os.environ.get("S3_BUCKET", "kobo-converter-195950944512")
S3_PREFIX = "processed/"

SUPPORTED = {".epub", ".mobi", ".docx", ".pdf"}


def get_s3():
    return boto3.client("s3")


def s3_upload(local_path: Path):
    get_s3().upload_file(str(local_path), S3_BUCKET, f"{S3_PREFIX}{local_path.name}")


def s3_list_files() -> list[str]:
    try:
        resp = get_s3().list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_PREFIX)
        return [
            obj["Key"].removeprefix(S3_PREFIX)
            for obj in resp.get("Contents", [])
            if obj["Key"] != S3_PREFIX
        ]
    except ClientError:
        return []


def s3_download(filename: str):
    """Returns a streaming body for the file."""
    resp = get_s3().get_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}{filename}")
    return resp["Body"], resp["ContentLength"]


def process_file(input_path: Path) -> str | None:
    """Convert file and upload to S3. Returns error message or None."""
    suffix = input_path.suffix.lower()
    stem = input_path.stem

    try:
        if suffix == ".epub":
            subprocess.run(["kepubify", str(input_path)], check=True, capture_output=True)
            kepub = input_path.parent / f"{stem}.kepub.epub"
            s3_upload(kepub)
            kepub.unlink(missing_ok=True)

        elif suffix in (".mobi", ".docx"):
            epub_path = input_path.parent / f"{stem}.epub"
            subprocess.run(
                ["ebook-convert", str(input_path), str(epub_path)],
                check=True, capture_output=True,
            )
            subprocess.run(["kepubify", str(epub_path)], check=True, capture_output=True)
            kepub = input_path.parent / f"{stem}.kepub.epub"
            s3_upload(kepub)
            kepub.unlink(missing_ok=True)
            epub_path.unlink(missing_ok=True)

        elif suffix == ".pdf":
            s3_upload(input_path)

        else:
            return f"Unsupported format: {suffix}"

    except subprocess.CalledProcessError as e:
        return f"Conversion failed: {e.stderr.decode(errors='replace')}"
    except ClientError as e:
        return f"S3 upload failed: {e}"
    finally:
        input_path.unlink(missing_ok=True)

    return None


@app.get("/", response_class=HTMLResponse)
async def index():
    files = sorted(s3_list_files())
    file_links = "".join(
        f'<li><a href="/download/{f}">{f}</a></li>' for f in files
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kobo Converter</title>
<style>body{{font-family:sans-serif;max-width:600px;margin:2em auto;padding:0 1em}}
h1{{font-size:1.4em}}ul{{padding-left:1.2em}}li{{margin:.3em 0}}</style></head>
<body><h1>Kobo Converter</h1>
<form action="/upload" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".epub,.mobi,.docx,.pdf">
<input type="submit" value="Upload &amp; Convert">
</form>
<h2>Processed Files</h2>
<ul>{file_links or "<li>No files yet.</li>"}</ul>
</body></html>"""


@app.post("/upload")
async def upload(file: UploadFile):
    if not file.filename:
        return RedirectResponse("/", status_code=303)

    suffix = Path(file.filename).suffix.lower()
    if suffix not in SUPPORTED:
        return HTMLResponse(
            f"<pre>Unsupported file type: {suffix}\nSupported: {', '.join(SUPPORTED)}</pre>",
            status_code=400,
        )

    dest = UPLOAD_DIR / file.filename
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    error = process_file(dest)
    if error:
        return HTMLResponse(f"<pre>{error}</pre>", status_code=500)

    return RedirectResponse("/", status_code=303)


@app.get("/download/{filename:path}")
async def download(filename: str):
    try:
        body, length = s3_download(filename)
        return StreamingResponse(
            body.iter_chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(length),
            },
        )
    except ClientError:
        return HTMLResponse("<pre>File not found.</pre>", status_code=404)
