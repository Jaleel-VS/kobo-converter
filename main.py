import logging
import os
import secrets
import shutil
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.responses import StreamingResponse

import converter
import storage
from result import Err

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI()

# --- Auth ---

security = HTTPBasic()
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")


def check_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not AUTH_USERNAME:
        return
    if not (
        secrets.compare_digest(credentials.username.encode(), AUTH_USERNAME.encode())
        and secrets.compare_digest(credentials.password.encode(), AUTH_PASSWORD.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# --- Dirs ---

UPLOAD_DIR = Path("/app/books/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --- HTML ---

PAGE = """<!DOCTYPE html>
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
<input type="file" name="files" accept=".epub,.mobi,.docx,.pdf" multiple>
<input type="submit" value="Upload &amp; Convert">
</form>
</div>
<div class="card">
<h2>Library</h2>
<ul>{file_links}</ul>
</div>
</body></html>"""


def _render_file_links() -> str:
    files = sorted(storage.list_files())
    if not files:
        return '<li class="empty">No files yet.</li>'
    return "".join(
        f'<li><a href="/download/{f}">{f}</a>'
        f'<form method="post" action="/delete/{f}">'
        f'<button type="submit" class="del" title="Delete">&times;</button></form></li>'
        for f in files
    )


# --- Routes ---


@app.get("/", response_class=HTMLResponse)
async def index(_=Depends(check_auth)):
    return PAGE.format(file_links=_render_file_links())


@app.post("/upload")
async def upload(files: list[UploadFile], _=Depends(check_auth)):
    existing = set(storage.list_files())
    errors: list[str] = []

    for file in files:
        if not file.filename:
            continue

        suffix = Path(file.filename).suffix.lower()
        if suffix not in converter.SUPPORTED:
            errors.append(f"{file.filename}: unsupported type {suffix}")
            continue

        if converter.expected_output_name(file.filename) in existing:
            log.info("Skipping duplicate: %s", file.filename)
            continue

        dest = UPLOAD_DIR / file.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

        result = converter.process(dest)
        if isinstance(result, Err):
            errors.append(f"{file.filename}: {result.error}")

    if errors:
        log.warning("Upload errors: %s", errors)
        return HTMLResponse(f"<pre>{'\\n'.join(errors)}</pre>", status_code=500)

    return RedirectResponse("/", status_code=303)


@app.get("/download/{filename:path}")
async def download(filename: str, _=Depends(check_auth)):
    try:
        body, length = storage.download(filename)
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
    storage.delete(filename)
    return RedirectResponse("/", status_code=303)
