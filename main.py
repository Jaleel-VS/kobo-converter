import os
import secrets
import shutil
import subprocess
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.responses import StreamingResponse

app = FastAPI()
security = HTTPBasic()

AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")


def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not AUTH_USERNAME:
        return  # auth disabled if env vars not set
    if not (
        secrets.compare_digest(credentials.username.encode(), AUTH_USERNAME.encode())
        and secrets.compare_digest(credentials.password.encode(), AUTH_PASSWORD.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )

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


def find_kepub(directory: Path, stem: str) -> Path | None:
    """Find the .kepub.epub file kepubify produced."""
    for f in directory.iterdir():
        if f.name.endswith(".kepub.epub") and f.name.startswith(stem):
            return f
    # fallback: any new .kepub.epub
    for f in directory.iterdir():
        if f.name.endswith(".kepub.epub"):
            return f
    return None


def run_kepubify(epub_path: Path) -> Path:
    """Run kepubify and return the output path."""
    output_dir = epub_path.parent
    subprocess.run(
        ["kepubify", "--inplace", "-o", str(output_dir), str(epub_path)],
        check=True, capture_output=True,
    )
    kepub = find_kepub(output_dir, epub_path.stem)
    if not kepub:
        raise FileNotFoundError(f"kepubify produced no output for {epub_path.name}")
    return kepub


def process_file(input_path: Path) -> str | None:
    """Convert file and upload to S3. Returns error message or None."""
    suffix = input_path.suffix.lower()

    try:
        if suffix == ".epub":
            kepub = run_kepubify(input_path)
            s3_upload(kepub)
            kepub.unlink(missing_ok=True)

        elif suffix in (".mobi", ".docx"):
            epub_path = input_path.parent / f"{input_path.stem}.epub"
            subprocess.run(
                ["ebook-convert", str(input_path), str(epub_path)],
                check=True, capture_output=True,
            )
            kepub = run_kepubify(epub_path)
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
async def index(_=Depends(check_auth)):
    files = sorted(s3_list_files())
    file_links = "".join(
        f'<li><a href="/download/{f}">{f}</a>'
        f'<form method="post" action="/delete/{f}">'
        f'<button type="submit" class="del" title="Delete">&times;</button></form></li>'
        for f in files
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kobo Converter</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:Georgia,serif;margin:0;padding:2em 1em;max-width:540px;margin:0 auto;
  background:#faf9f6;color:#2c2c2c}}
h1{{font-size:1.6em;margin:0 0 .2em;letter-spacing:-.02em}}
.subtitle{{color:#888;font-size:.9em;margin:0 0 2em}}
.card{{background:#fff;border:1px solid #e0ddd8;border-radius:8px;padding:1.5em;margin-bottom:1.5em}}
.card h2{{font-size:1em;margin:0 0 1em;color:#555;text-transform:uppercase;letter-spacing:.05em;font-family:sans-serif}}
input[type=file]{{display:block;margin-bottom:1em;font-size:.95em}}
input[type=submit]{{background:#2c2c2c;color:#faf9f6;border:none;padding:.6em 1.4em;
  border-radius:6px;cursor:pointer;font-size:.95em;font-family:inherit}}
input[type=submit]:active{{background:#555}}
ul{{list-style:none;padding:0;margin:0}}
li{{display:flex;align-items:center;justify-content:space-between;padding:.6em 0;
  border-bottom:1px solid #eee}}
li:last-child{{border-bottom:none}}
li a{{color:#2c2c2c;text-decoration:none;word-break:break-all;flex:1}}
li a:hover{{text-decoration:underline}}
.del{{background:none;border:none;color:#c44;font-size:1.3em;cursor:pointer;
  padding:0 0 0 .8em;line-height:1;font-family:sans-serif}}
.del:hover{{color:#a00}}
.empty{{color:#999;font-style:italic}}
form{{margin:0}}
</style></head>
<body>
<h1>&#128218; Kobo Converter</h1>
<p class="subtitle">Upload an ebook &mdash; get a Kobo-ready file back.</p>
<div class="card">
<h2>Convert</h2>
<form action="/upload" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".epub,.mobi,.docx,.pdf">
<input type="submit" value="Upload &amp; Convert">
</form>
</div>
<div class="card">
<h2>Library</h2>
<ul>{file_links or '<li class="empty">No files yet.</li>'}</ul>
</div>
</body></html>"""


@app.post("/upload")
async def upload(file: UploadFile, _=Depends(check_auth)):
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
async def download(filename: str, _=Depends(check_auth)):
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


@app.post("/delete/{filename:path}")
async def delete(filename: str, _=Depends(check_auth)):
    try:
        get_s3().delete_object(Bucket=S3_BUCKET, Key=f"{S3_PREFIX}{filename}")
    except ClientError:
        pass
    return RedirectResponse("/", status_code=303)
