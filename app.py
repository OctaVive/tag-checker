"""Flask web app for bulk-setting FLAC AlbumArtist to Various Artists."""

from __future__ import annotations

import gc
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request, session

from scanner import build_folder_tree, get_music_root, iter_flac_files, validate_selected_dirs
from tags import ensure_albumartist

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "flac-albumartist-dev-key")

TARGET_ALBUMARTIST = "Various Artists"
MAX_ERRORS = 50
PROGRESS_EVERY_N = 50
PROGRESS_EVERY_SEC = 1.0
GC_EVERY_N = 200

_job_meta: dict[str, Any] = {}
_meta_lock = threading.Lock()
_tree_cache: dict[str, Any] = {"root": None, "tree": None}

_JOB_DIR = Path(os.environ.get("JOB_STATUS_DIR", tempfile.gettempdir())) / "flac-albumartist-jobs"
_JOB_DIR.mkdir(parents=True, exist_ok=True)


def _session_id() -> str:
    if "sid" not in session:
        session["sid"] = uuid.uuid4().hex
    return session["sid"]


def _job_path(sid: str) -> Path:
    return _JOB_DIR / f"{sid}.json"


def _write_job(sid: str, data: dict[str, Any]) -> None:
    path = _job_path(sid)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _read_job(sid: str) -> dict[str, Any] | None:
    path = _job_path(sid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _get_job() -> dict[str, Any] | None:
    return _read_job(_session_id())


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


def _cached_folder_tree() -> dict[str, Any]:
    """Reuse the folder tree across requests; rebuild only if MUSIC_PATH changes."""
    root = str(get_music_root())
    if _tree_cache["root"] == root and _tree_cache["tree"] is not None:
        return _tree_cache["tree"]
    tree = build_folder_tree()
    _tree_cache["root"] = root
    _tree_cache["tree"] = tree
    return tree


def _run_apply(sid: str, selected_dirs: list[str]) -> None:
    processed = 0
    updated = 0
    skipped = 0
    failed = 0
    errors: list[str] = []
    last_write = 0.0
    current_file = ""

    state = {
        "status": "running",
        "processed": 0,
        "updated": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
        "current_file": "",
    }
    _write_job(sid, state)

    def flush(force: bool = False) -> None:
        nonlocal last_write
        now = time.monotonic()
        if not force and processed % PROGRESS_EVERY_N != 0 and (now - last_write) < PROGRESS_EVERY_SEC:
            return
        last_write = now
        _write_job(
            sid,
            {
                "status": "running",
                "processed": processed,
                "updated": updated,
                "skipped": skipped,
                "failed": failed,
                "errors": list(errors),
                "current_file": current_file,
            },
        )

    try:
        for path in iter_flac_files(selected_dirs):
            processed += 1
            current_file = Path(path).name
            try:
                result = ensure_albumartist(path, TARGET_ALBUMARTIST)
                if result == "skipped":
                    skipped += 1
                else:
                    updated += 1
            except Exception as exc:  # noqa: BLE001 — surface per-file errors in UI
                failed += 1
                if len(errors) < MAX_ERRORS:
                    errors.append(f"{path}: {exc}")

            flush()

            if processed % GC_EVERY_N == 0:
                gc.collect()
                # Briefly yield so HTTP threads can serve the UI under GIL pressure.
                time.sleep(0.01)
    except Exception as exc:  # noqa: BLE001
        if len(errors) < MAX_ERRORS:
            errors.append(f"Job failed: {exc}")
        failed += 1

    _write_job(
        sid,
        {
            "status": "done",
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "current_file": "",
        },
    )
    with _meta_lock:
        _job_meta.pop(sid, None)
    gc.collect()


def _start_apply_worker(sid: str, selected: list[str]) -> threading.Thread | Any:
    """Start apply in a child process (keeps UI responsive). Fall back to a thread."""

    def start_thread() -> threading.Thread:
        thread = threading.Thread(
            target=_run_apply,
            args=(sid, list(selected)),
            daemon=True,
            name=f"flac-apply-{sid[:8]}",
        )
        thread.start()
        return thread

    try:
        from multiprocessing import get_context

        ctx = get_context("spawn")
        proc = ctx.Process(
            target=_run_apply,
            args=(sid, list(selected)),
            daemon=True,
            name=f"flac-apply-{sid[:8]}",
        )
        proc.start()
        # If spawn fails immediately (e.g. odd launch context), fall back.
        time.sleep(0.15)
        job = _read_job(sid)
        if not proc.is_alive() and (not job or job.get("status") not in ("running", "done")):
            return start_thread()
        return proc
    except Exception:  # noqa: BLE001
        return start_thread()


@app.route("/")
def index():
    error = None
    tree = None
    music_root = None
    active_job = None
    try:
        _session_id()
        music_root = str(get_music_root())
        tree = _cached_folder_tree()
        job = _get_job()
        if job and job.get("status") == "running":
            active_job = job
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        error = str(exc)
    return render_template(
        "index.html",
        tree=tree,
        music_root=music_root,
        error=error,
        target_albumartist=TARGET_ALBUMARTIST,
        active_job=active_job,
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
        with _meta_lock:
            worker = _job_meta.get(sid)
        if worker is not None and getattr(worker, "is_alive", lambda: False)():
            return render_template("partials/progress.html", job=existing)
        # Stale "running" marker from a dead worker
        existing = {**existing, "status": "done", "errors": list(existing.get("errors") or []) + ["Previous job ended unexpectedly."]}
        _write_job(sid, existing)

    with _meta_lock:
        worker = _job_meta.get(sid)
        if worker is not None and worker.is_alive():
            job = _get_job() or existing
            return render_template("partials/progress.html", job=job)

    worker = _start_apply_worker(sid, list(selected))
    with _meta_lock:
        _job_meta[sid] = worker

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
    sid = _session_id()
    job = _get_job()
    with _meta_lock:
        worker = _job_meta.get(sid)

    if job and job.get("status") == "running" and worker is not None and not worker.is_alive():
        job = {
            **job,
            "status": "done",
            "current_file": "",
            "errors": list(job.get("errors") or []) + ["Worker exited unexpectedly."],
        }
        _write_job(sid, job)
        with _meta_lock:
            _job_meta.pop(sid, None)

    if not job:
        return render_template(
            "partials/summary.html",
            job=_empty_done_job(["No apply job found."]),
        )
    if job.get("status") == "done":
        return render_template("partials/summary.html", job=job)
    return render_template("partials/progress.html", job=job)


if __name__ == "__main__":
    # debug=False avoids the reloader (extra process + duplicated RAM).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
