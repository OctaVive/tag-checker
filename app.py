"""Flask web app for bulk-setting FLAC AlbumArtist to Various Artists."""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request, session

from scanner import build_folder_tree, get_music_root, iter_flac_files, validate_selected_dirs
from tags import get_albumartist, set_albumartist

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flac-albumartist-dev-key")

TARGET_ALBUMARTIST = "Various Artists"
MAX_ERRORS = 50

_job_store: dict[str, dict[str, Any]] = {}
_store_lock = threading.Lock()


def _session_id() -> str:
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def _get_job() -> dict[str, Any] | None:
    sid = _session_id()
    with _store_lock:
        job = _job_store.get(sid)
        return dict(job) if job else None


def _empty_done_job(errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "status": "done",
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "errors": errors or [],
        "current_file": "",
    }


def _run_apply(sid: str, selected_dirs: list[str]) -> None:
    processed = 0
    updated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []

    with _store_lock:
        _job_store[sid] = {
            "status": "running",
            "processed": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
            "current_file": "",
        }

    try:
        for path in iter_flac_files(selected_dirs):
            processed += 1
            name = Path(path).name
            with _store_lock:
                job = _job_store[sid]
                job["processed"] = processed
                job["current_file"] = name

            try:
                current = get_albumartist(path).strip()
                if current == TARGET_ALBUMARTIST:
                    skipped += 1
                    with _store_lock:
                        _job_store[sid]["skipped"] = skipped
                    continue

                set_albumartist(path, TARGET_ALBUMARTIST)
                updated += 1
                with _store_lock:
                    _job_store[sid]["updated"] = updated
            except Exception as exc:  # noqa: BLE001 — surface per-file errors in UI
                failed += 1
                msg = f"{path}: {exc}"
                if len(errors) < MAX_ERRORS:
                    errors.append(msg)
                with _store_lock:
                    _job_store[sid]["failed"] = failed
                    _job_store[sid]["errors"] = list(errors)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Job failed: {exc}")
        with _store_lock:
            _job_store[sid]["errors"] = list(errors)[:MAX_ERRORS]
            _job_store[sid]["failed"] = failed + 1

    with _store_lock:
        _job_store[sid].update(
            {
                "status": "done",
                "processed": processed,
                "updated": updated,
                "skipped": skipped,
                "failed": failed,
                "errors": errors,
                "current_file": "",
            }
        )


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
        target_albumartist=TARGET_ALBUMARTIST,
    )


@app.route("/apply", methods=["POST"])
def apply():
    selected = request.form.getlist("folders")
    if not selected:
        return render_template(
            "partials/summary.html",
            job=_empty_done_job(["Select at least one folder before applying."]),
        ), 400

    try:
        validate_selected_dirs(selected)
    except ValueError as exc:
        return render_template(
            "partials/summary.html",
            job=_empty_done_job([str(exc)]),
        ), 400

    sid = _session_id()
    existing = _get_job()
    if existing and existing.get("status") == "running":
        return render_template("partials/progress.html", job=existing)

    thread = threading.Thread(
        target=_run_apply,
        args=(sid, list(selected)),
        daemon=True,
    )
    thread.start()

    time.sleep(0.05)
    job = _get_job() or {
        "status": "running",
        "processed": 0,
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
            job=_empty_done_job(["No apply job found."]),
        )
    if job.get("status") == "done":
        return render_template("partials/summary.html", job=job)
    return render_template("partials/progress.html", job=job)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
