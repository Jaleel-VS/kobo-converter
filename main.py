import hashlib
import logging
import os
import secrets
import shutil
from pathlib import Path

from botocore.exceptions import ClientError
from fastapi import Cookie, FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from result import Err
from starlette.responses import StreamingResponse

import converter
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

app = FastAPI()

# --- Auth ---

AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32))


def _make_token() -> str:
    return hashlib.sha256(f"{AUTH_USERNAME}:{AUTH_PASSWORD}:{SESSION_SECRET}".encode()).hexdigest()


def _is_authenticated(session: str | None) -> bool:
    if not AUTH_USERNAME:
        return True
    return session == _make_token()


# --- Dirs ---

UPLOAD_DIR = Path("/app/books/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# --- Templates ---

CSS = (
    "*{box-sizing:border-box}"
    "body{font-family:Georgia,serif;margin:0;padding:2em 1em;"
    "max-width:540px;margin:0 auto;background:#faf9f6;color:#2c2c2c}"
    "h1{font-size:1.6em;margin:0 0 .2em;letter-spacing:-.02em}"
    ".subtitle{color:#888;font-size:.9em;margin:0 0 2em}"
    ".card{background:#fff;border:1px solid #e0ddd8;"
    "border-radius:8px;padding:1.5em;margin-bottom:1.5em}"
    ".card h2{font-size:1em;margin:0 0 1em;color:#555;"
    "text-transform:uppercase;letter-spacing:.05em;font-family:sans-serif}"
    "input[type=file]{display:block;margin-bottom:1em;font-size:.95em}"
    "input[type=text],input[type=password]{display:block;width:100%;"
    "padding:.5em;margin-bottom:.8em;border:1px solid #ddd;"
    "border-radius:4px;font-size:.95em;font-family:inherit}"
    "input[type=submit]{background:#2c2c2c;color:#faf9f6;border:none;"
    "padding:.6em 1.4em;border-radius:6px;cursor:pointer;"
    "font-size:.95em;font-family:inherit}"
    "input[type=submit]:active{background:#555}"
    "ul{list-style:none;padding:0;margin:0}"
    "li{display:flex;align-items:center;justify-content:space-between;"
    "padding:.6em 0;border-bottom:1px solid #eee}"
    "li:last-child{border-bottom:none}"
    "li a{color:#2c2c2c;text-decoration:none;word-break:break-all;flex:1}"
    "li a:hover{text-decoration:underline}"
    ".del{background:none;border:none;color:#c44;font-size:1.3em;"
    "cursor:pointer;padding:0 0 0 .8em;line-height:1;font-family:sans-serif}"
    ".del:hover{color:#a00}"
    ".empty{color:#999;font-style:italic}"
    ".error{color:#c44;font-size:.9em;margin-bottom:1em}"
    "form{margin:0}"
)


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>" + title + "</title>"
        "<style>" + CSS + "</style>"
        "</head><body>"
        "<h1>&#128218; Kobo Converter</h1>" + body + "</body></html>"
    )


def _login_html(error: str = "") -> str:
    return _page(
        "Login",
        '<p class="subtitle">Please log in.</p>'
        '<div class="card"><h2>Login</h2>' + error + '<form method="post" action="/login">'
        '<input type="text" name="username" placeholder="Username">'
        '<input type="password" name="password" placeholder="Password">'
        '<input type="submit" value="Log in">'
        "</form></div>",
    )


def _main_html() -> str:
    links = _render_file_links()
    return _page(
        "Kobo Converter",
        '<p class="subtitle">Upload an ebook &mdash; get a Kobo-ready file back.</p>'
        '<div class="card"><h2>Convert</h2>'
        '<form action="/upload" method="post" enctype="multipart/form-data">'
        '<input type="file" name="files" accept=".epub,.mobi,.docx,.pdf" multiple>'
        '<input type="submit" value="Upload &amp; Convert">'
        "</form></div>"
        '<div class="card"><h2>Library</h2><ul>' + links + "</ul></div>"
        '<form method="post" action="/logout">'
        '<input type="submit" value="Log out" style="background:none;border:none;'
        "color:#888;cursor:pointer;font-size:.85em;font-family:inherit;"
        'padding:0;text-decoration:underline">'
        "</form>",
    )


def _render_file_links() -> str:
    files = sorted(storage.list_files())
    if not files:
        return '<li class="empty">No files yet.</li>'
    return "".join(
        f'<li><a href="/download/{f}">{f}</a>'
        f'<form method="post" action="/delete/{f}">'
        f'<button type="submit" class="del" title="Delete">&times;</button>'
        f"</form></li>"
        for f in files
    )


# --- Routes ---


@app.get("/login", response_class=HTMLResponse)
async def login_page():
    if not AUTH_USERNAME:
        return RedirectResponse("/", status_code=303)
    return _login_html()


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if secrets.compare_digest(str(username), AUTH_USERNAME) and secrets.compare_digest(
        str(password), AUTH_PASSWORD
    ):
        response = RedirectResponse("/", status_code=303)
        response.set_cookie("session", _make_token(), httponly=True, samesite="strict")
        log.info("Login successful")
        return response

    log.warning("Login failed")
    return HTMLResponse(
        _login_html('<p class="error">Invalid credentials.</p>'),
        status_code=401,
    )


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    return response


@app.get("/", response_class=HTMLResponse)
async def index(session: str | None = Cookie(default=None)):
    if not _is_authenticated(session):
        return RedirectResponse("/login", status_code=303)
    return _main_html()


@app.post("/upload")
async def upload(request: Request, session: str | None = Cookie(default=None)):
    if not _is_authenticated(session):
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    files: list[UploadFile] = form.getlist("files")
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
            errors.append(f"{file.filename}: {result.err_value}")

    if errors:
        log.warning("Upload errors: %s", errors)
        msg = "\n".join(errors)
        return HTMLResponse(f"<pre>{msg}</pre>", status_code=500)

    return RedirectResponse("/", status_code=303)


@app.get("/download/{filename:path}")
async def download(filename: str, session: str | None = Cookie(default=None)):
    if not _is_authenticated(session):
        return RedirectResponse("/login", status_code=303)
    try:
        body, length = storage.download(filename)
        from urllib.parse import quote

        encoded = quote(filename)
        ascii_name = filename.encode("ascii", errors="replace").decode()
        disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"
        return StreamingResponse(
            body.iter_chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": disposition,
                "Content-Length": str(length),
            },
        )
    except ClientError:
        return HTMLResponse("<pre>File not found.</pre>", status_code=404)


@app.post("/delete/{filename:path}")
async def delete(filename: str, session: str | None = Cookie(default=None)):
    if not _is_authenticated(session):
        return RedirectResponse("/login", status_code=303)
    storage.delete(filename)
    return RedirectResponse("/", status_code=303)
