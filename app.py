"""Flask web app for editing FLAC AlbumArtist tags."""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any

from flask import Flask, render_template, request, session

from scanner import build_folder_tree, find_flac_files, get_music_root
from tags import read_flac_tags, set_albumartist

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flac-albumartist-dev-key")

# In-memory stores keyed by session id (single-user Docker tool)
_scan_store: dict[str, list[dict[str, Any]]] = {}
_job_store: dict[str, dict[str, Any]] = {}
_store_lock = threading.Lock()


def _session_id() -> str:
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def _get_scanned() -> list[dict[str, Any]]:
    sid = _session_id()
    with _store_lock:
        return list(_scan_store.get(sid, []))


def _set_scanned(rows: list[dict[str, Any]]) -> None:
    sid = _session_id()
    with _store_lock:
        _scan_store[sid] = rows


def _get_job() -> dict[str, Any] | None:
    sid = _session_id()
    with _store_lock:
        job = _job_store.get(sid)
        return dict(job) if job else None


def _update_job(data: dict[str, Any]) -> None:
    sid = _session_id()
    with _store_lock:
        existing = _job_store.get(sid, {})
        existing.update(data)
        _job_store[sid] = existing


@app.route("/")
def index():
    error = None
    tree = None
    music_root = None
    try:
        music_root = str(get_music_root())
        tree = build_folder_tree()
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        error = str(exc)
    return render_template(
        "index.html",
        tree=tree,
        music_root=music_root,
        error=error,
    )


@app.route("/scan", methods=["POST"])
def scan():
    selected = request.form.getlist("folders")
    if not selected:
        return render_template(
            "partials/preview.html",
            rows=[],
            new_albumartist="",
            error="Select at least one folder before scanning.",
            file_count=0,
        ), 400

    try:
        paths = find_flac_files(selected)
    except ValueError as exc:
        return render_template(
            "partials/preview.html",
            rows=[],
            new_albumartist="",
            error=str(exc),
            file_count=0,
        ), 400

    rows: list[dict[str, Any]] = []
    read_errors: list[str] = []
    for path in paths:
        try:
            rows.append(read_flac_tags(path))
        except Exception as exc:  # noqa: BLE001 — surface per-file errors in UI
            read_errors.append(f"{path}: {exc}")

    _set_scanned(rows)
    new_aa = request.form.get("albumartist", "").strip()
    return render_template(
        "partials/preview.html",
        rows=rows,
        new_albumartist=new_aa,
        error=None,
        file_count=len(rows),
        read_errors=read_errors,
    )


@app.route("/preview", methods=["POST"])
def preview():
    rows = _get_scanned()
    new_aa = request.form.get("albumartist", "").strip()
    if not rows:
        return render_template(
            "partials/preview.html",
            rows=[],
            new_albumartist=new_aa,
            error="No scanned files. Select folders and click Scan first.",
            file_count=0,
        )
    return render_template(
        "partials/preview.html",
        rows=rows,
        new_albumartist=new_aa,
        error=None,
        file_count=len(rows),
        read_errors=[],
    )


def _run_apply(sid: str, albumartist: str, rows: list[dict[str, Any]]) -> None:
    total = len(rows)
    updated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    with _store_lock:
        _job_store[sid] = {
            "status": "running",
            "current": 0,
            "total": total,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
            "current_file": "",
        }

    for i, row in enumerate(rows):
        path = row["path"]
        with _store_lock:
            job = _job_store[sid]
            job["current"] = i + 1
            job["current_file"] = row.get("filename", path)

        current_aa = (row.get("albumartist") or "").strip()
        if current_aa == albumartist:
            skipped += 1
            with _store_lock:
                _job_store[sid]["skipped"] = skipped
            continue

        try:
            set_albumartist(path, albumartist)
            updated += 1
            row["albumartist"] = albumartist
            with _store_lock:
                _job_store[sid]["updated"] = updated
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append(f"{path}: {exc}")
            with _store_lock:
                _job_store[sid]["failed"] = failed
                _job_store[sid]["errors"] = list(errors)

    with _store_lock:
        _job_store[sid].update(
            {
                "status": "done",
                "updated": updated,
                "skipped": skipped,
                "failed": failed,
                "errors": errors,
                "current_file": "",
            }
        )
        _scan_store[sid] = rows


@app.route("/apply", methods=["POST"])
def apply():
    rows = _get_scanned()
    albumartist = request.form.get("albumartist", "").strip()
    if not rows:
        return render_template(
            "partials/summary.html",
            job={
                "status": "done",
                "updated": 0,
                "skipped": 0,
                "failed": 0,
                "errors": ["No scanned files. Select folders and click Scan first."],
                "total": 0,
                "current": 0,
            },
        ), 400
    if not albumartist:
        return render_template(
            "partials/summary.html",
            job={
                "status": "done",
                "updated": 0,
                "skipped": 0,
                "failed": 0,
                "errors": ["Enter a new AlbumArtist value before applying."],
                "total": 0,
                "current": 0,
            },
        ), 400

    sid = _session_id()
    existing = _get_job()
    if existing and existing.get("status") == "running":
        return render_template("partials/progress.html", job=existing)

    thread = threading.Thread(
        target=_run_apply,
        args=(sid, albumartist, list(rows)),
        daemon=True,
    )
    thread.start()

    time.sleep(0.05)
    job = _get_job() or {
        "status": "running",
        "current": 0,
        "total": len(rows),
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "current_file": "",
    }
    return render_template("partials/progress.html", job=job)


@app.route("/apply/status")
def apply_status():
    job = _get_job()
    if not job:
        return render_template(
            "partials/summary.html",
            job={
                "status": "done",
                "updated": 0,
                "skipped": 0,
                "failed": 0,
                "errors": ["No apply job found."],
                "total": 0,
                "current": 0,
            },
        )
    if job.get("status") == "done":
        return render_template("partials/summary.html", job=job)
    return render_template("partials/progress.html", job=job)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
