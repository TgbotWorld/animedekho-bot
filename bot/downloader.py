"""Download manager — Multi-engine with N_m3u8DL-RE, Direct HTTP, and FFmpeg fallback."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from glob import escape as glob_escape
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from bot.telegram import Client, enums
from bot.telegram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from api.models import Quality

log = logging.getLogger(__name__)

TG_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024

_TEMP_BASE = Path(tempfile.gettempdir()) / "animedekho_dl"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


class DownloadJob:
    """Represents an active download task for cancellation tracking."""

    def __init__(self, job_id: str, user_id: int, title: str, progress_msg: Message | None = None):
        self.job_id = job_id
        self.user_id = user_id
        self.title = title
        self.progress_msg = progress_msg
        self.task: asyncio.Task | None = None
        self.subprocesses: set[any] = set()
        self.temp_files: set[str] = set()
        self.is_cancelled: bool = False
        self.created_at: float = time.time()


class DownloadJobManager:
    """Tracks active download tasks and allows instant per-job cancellation (Issue #22 & #23).

    V3 #6 batch lifecycle:
        Batch Job ─┬─ Episode Job 1
                   ├─ Episode Job 2
                   └─ Episode Job 3
    One cancel on the batch ID stops the exact batch (and its episode
    children) without touching other users' jobs.
    """

    def __init__(self):
        self._jobs: dict[str, DownloadJob] = {}
        self._cancelled_ids: set[str] = set()
        self._batch_children: dict[str, set[str]] = {}
        self._child_parent: dict[str, str] = {}

    def create_job(self, user_id: int, title: str, progress_msg: Message | None = None) -> str:
        job_id = uuid.uuid4().hex[:10]
        self._jobs[job_id] = DownloadJob(job_id, user_id, title, progress_msg)
        return job_id

    def create_batch_job(self, user_id: int, title: str, total: int, progress_msg: Message | None = None) -> str:
        """V3 #6: create a parent batch job tracking total episodes."""
        batch_id = self.create_job(user_id, f"Batch: {title} ({total} eps)", progress_msg)
        self._batch_children[batch_id] = set()
        return batch_id

    def create_episode_job(self, batch_id: str | None, user_id: int, title: str, progress_msg: Message | None = None) -> str:
        """V3 #6: create a child episode job linked to its batch parent."""
        child_id = self.create_job(user_id, title, progress_msg)
        if batch_id:
            self._batch_children.setdefault(batch_id, set()).add(child_id)
            self._child_parent[child_id] = batch_id
        return child_id

    def get_job(self, job_id: str) -> DownloadJob | None:
        return self._jobs.get(job_id)

    def is_job_cancelled(self, job_id: str | None) -> bool:
        if not job_id:
            return False
        if job_id in self._cancelled_ids:
            return True
        job = self._jobs.get(job_id)
        if job and job.is_cancelled:
            return True
        # V3 #6: an episode child is cancelled when its batch parent is.
        parent = self._child_parent.get(job_id)
        if parent and (parent in self._cancelled_ids or
                       (self._jobs.get(parent) and self._jobs[parent].is_cancelled)):
            return True
        return False

    def batch_is_cancelled(self, batch_id: str | None) -> bool:
        """V3 #6: convenience alias for batch-parent checks in batch loops."""
        return self.is_job_cancelled(batch_id)

    def attach_task(self, job_id: str, task: asyncio.Task):
        job = self._jobs.get(job_id)
        if job:
            job.task = task

    def attach_process(self, job_id: str | None, proc: any):
        if not job_id:
            return
        job = self._jobs.get(job_id)
        if job and proc:
            job.subprocesses.add(proc)

    def attach_temp_file(self, job_id: str | None, file_path: str):
        if not job_id:
            return
        job = self._jobs.get(job_id)
        if job and file_path:
            job.temp_files.add(file_path)

    async def cancel_job(self, job_id: str, user_id: int, is_admin: bool = False) -> tuple[bool, str]:
        job = self._jobs.get(job_id)
        if not job:
            # V3 #6: batch IDs may have been popped as children finished —
            # still honour idempotent cancel via the tombstone set.
            if job_id in self._cancelled_ids:
                return True, "Already cancelled"
            return False, "Job not found or already finished"

        if job.user_id != user_id and not is_admin:
            return False, "You cannot cancel another user's download"

        self._cancelled_ids.add(job_id)
        if len(self._cancelled_ids) > 1000:
            self._cancelled_ids = set(list(self._cancelled_ids)[-500:])
        job.is_cancelled = True

        # V3 #6: cancelling a batch parent cancels every live episode child
        # (exact batch only — other users' jobs are untouched).
        for child_id in list(self._batch_children.get(job_id, set())):
            child = self._jobs.get(child_id)
            if child:
                child.is_cancelled = True
                self._cancelled_ids.add(child_id)
                for proc in list(child.subprocesses):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                child.subprocesses.clear()
                if child.task and not child.task.done():
                    child.task.cancel()
            self._child_parent.pop(child_id, None)
        self._batch_children.pop(job_id, None)
        # If this was an episode child, detach from its parent set.
        parent = self._child_parent.pop(job_id, None)
        if parent and parent in self._batch_children:
            self._batch_children[parent].discard(job_id)

        # Terminate running subprocesses
        for proc in list(job.subprocesses):
            try:
                proc.kill()
            except Exception:
                pass
        job.subprocesses.clear()

        # Cancel asyncio task
        if job.task and not job.task.done():
            job.task.cancel()

        # Cleanup temp files
        for p in list(job.temp_files):
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                elif os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        job.temp_files.clear()

        # Update progress message
        if job.progress_msg:
            try:
                await job.progress_msg.edit_text(
                    f"🛑 <b>Download Cancelled</b>\n\n"
                    f"📺 <b>{job.title}</b>\n"
                    f"<i>The download task was cancelled and temporary files cleaned up.</i>",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

        self._jobs.pop(job_id, None)
        return True, "Cancelled successfully"

    def remove_job(self, job_id: str | None):
        if job_id:
            self._jobs.pop(job_id, None)
            # V3 #6: keep parent→child bookkeeping consistent.
            self._batch_children.pop(job_id, None)
            parent = self._child_parent.pop(job_id, None)
            if parent and parent in self._batch_children:
                self._batch_children[parent].discard(job_id)


download_job_manager = DownloadJobManager()


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s]+', ' ', name).strip()
    return name[:120] or "video"


def make_episode_filename(series_title: str, season: int, episode: int, quality: str) -> str:
    return f"{sanitize_filename(series_title)} S{season:01d}E{episode:02d} [{quality}].mp4"


def make_movie_filename(movie_title: str, quality: str) -> str:
    return f"{sanitize_filename(movie_title)} [{quality}].mp4"


# ── Stylish Progress ──────────────────────────────────────────────────


_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spin_idx = 0

def _spinner() -> str:
    global _spin_idx
    _spin_idx = (_spin_idx + 1) % len(_SPIN_FRAMES)
    return _SPIN_FRAMES[_spin_idx]


def _progress_bar(pct: float, width: int = 20) -> str:
    """Smooth animated progress bar with gradient fill."""
    filled_exact = max(0.0, min(100.0, pct)) / 100 * width
    filled = int(filled_exact)
    partials = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
    partial_idx = int((filled_exact - filled) * len(partials))

    if filled >= width:
        return "█" * width
    bar = "█" * filled
    if partial_idx > 0:
        bar += partials[partial_idx]
        remaining = width - filled - 1
    else:
        remaining = width - filled
    bar += "░" * max(0, remaining)
    return bar


def _format_size(b: float) -> str:
    if b >= 1024**3: return f"{b / 1024**3:.2f} GB"
    if b >= 1024**2: return f"{b / 1024**2:.1f} MB"
    if b >= 1024: return f"{b / 1024:.1f} KB"
    return f"{b:.0f} B"


def _format_time(s: float) -> str:
    if s < 0: return "∞"
    if s < 60: return f"{int(s)}s"
    if s < 3600: return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60)}m"


def _format_speed(bps: float) -> str:
    if bps >= 1024**2: return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024: return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _calc_eta(pct: float, elapsed: float) -> str:
    if pct <= 0 or elapsed <= 0:
        return "calculating..."
    remaining = elapsed / pct * (100 - pct)
    return _format_time(remaining)


def _download_progress_text(title: str, quality: str, pct: float, size_bytes: float, elapsed: float, speed: float, eta: str = "") -> str:
    spin = _spinner()
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    bar = _progress_bar(pct)
    eta_str = eta or _calc_eta(pct, elapsed)

    lines = [
        f"{spin} <b>⬇️ Downloading</b>",
        f"",
        f"<b>{title}</b>",
        f"🎬 {quality}",
        f"",
        f"<code>{bar}</code> <b>{pct:.1f}%</b>",
        f"",
        f"📦 {_format_size(size_bytes)}  ⚡ {speed_str}",
        f"⏱ {_format_time(elapsed)}  ⏳ ETA: {eta_str}",
    ]
    return "\n".join(lines)


def _upload_progress_text(title: str, quality: str, current: float, total: float, speed: float = 0, elapsed: float = 0) -> str:
    pct = current / total * 100 if total > 0 else 0
    spin = _spinner()
    bar = _progress_bar(pct)
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    eta_str = _calc_eta(pct, elapsed) if pct > 0 and elapsed > 0 else "calculating..."

    lines = [
        f"{spin} <b>⬆️ Uploading to Telegram</b>",
        f"",
        f"<b>{title}</b>",
        f"🎬 {quality}",
        f"",
        f"<code>{bar}</code> <b>{pct:.1f}%</b>",
        f"",
        f"📦 {_format_size(current)} / {_format_size(total)}",
        f"⚡ {speed_str}  ⏳ ETA: {eta_str}",
    ]
    return "\n".join(lines)


def _done_text(title: str, quality: str, size_bytes: float, elapsed: float) -> str:
    bar = _progress_bar(100)
    avg_speed = size_bytes / elapsed if elapsed > 0 else 0
    return (
        f"✅ <b>Upload Complete!</b>\n\n"
        f"<b>{title}</b>\n"
        f"🎬 {quality}\n\n"
        f"<code>{bar}</code> <b>100%</b>\n\n"
        f"📦 {_format_size(size_bytes)}  ⚡ avg {_format_speed(avg_speed)}\n"
        f"⏱ Total: {_format_time(elapsed)}"
    )


async def _update_progress(
    msg: Message | None,
    text: str,
    last_edit: list[float],
    interval: float = 3.0,
    reply_markup: InlineKeyboardMarkup | None = None,
    job_id: str | None = None,
):
    if not msg:
        return
    now = time.time()
    if now - last_edit[0] < interval:
        return
    last_edit[0] = now
    markup = reply_markup
    if markup is None and job_id and not download_job_manager.is_job_cancelled(job_id):
        markup = cancel_markup_for(job_id)
    try:
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
    except Exception:
        pass


def cancel_markup_for(job_id: str | None) -> InlineKeyboardMarkup | None:
    """V2 #21: Build the persistent Cancel button markup for a job.

    Every active download/upload progress edit must include this markup,
    otherwise Telegram's edit_text() drops the button during upload stage.
    Terminal states (done/failed/cancelled) must pass None instead.
    """
    if not job_id:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Cancel Download", callback_data=f"cendl:{job_id}")]])


def _get_origin(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if not domain:
        return "https://animedekho.tv"
    if "megacloud" in domain or "rabbit" in domain or "dokicloud" in domain:
        return "https://megacloud.tv"
    elif "vmeas" in domain or "vidmoly" in domain or "vmbox" in domain or "vmpx" in domain:
        return "https://vidmoly.to"
    elif "as-cdn" in domain or "fireplayer" in domain:
        return f"https://{domain}"
    elif "turboviplay" in domain or "turbosplayer" in domain or "emturbovid" in domain:
        return "https://emturbovid.com"
    elif "xerver" in domain or "vidsrc" in domain or "googleusercontent" in domain:
        return "https://mirror.xerver.xyz"
    elif "animedrive" in domain or "hubcloud" in domain or "gamerxyt" in domain:
        return "https://hubcloud.ist"
    elif "toonflix" in domain or "workers.dev" in domain:
        return "https://drive.toonflix.in"
    return f"https://{domain}"


# ── Engine 1: Direct HTTP Download (MP4 / Direct Streams) ─────────────


async def direct_http_download(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
    referer: str = "",
    job_id: str | None = None,
) -> bool:
    """Download direct video file (MP4/MKV) via chunked HTTP stream with progress."""
    log.info("Direct HTTP download: url=%s quality=%s", url[:120], quality)
    last_edit = [0.0]
    start_time = time.time()
    download_job_manager.attach_temp_file(job_id, output_path)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": referer or _get_origin(url) + "/",
        "Accept": "*/*",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=2400, connect=30, sock_read=60)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status not in (200, 206):
                    log.warning("Direct HTTP download failed with status %d", resp.status)
                    return False

                ctype = resp.headers.get("Content-Type", "").lower()
                if any(bad in ctype for bad in ("text/html", "text/plain", "application/json")):
                    log.warning("Direct HTTP download rejected: URL returned non-media Content-Type '%s'", ctype)
                    return False

                total_bytes = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_bytes = 0
                last_time = time.time()

                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                        if download_job_manager.is_job_cancelled(job_id):
                            log.info("Direct HTTP download cancelled for %s", title)
                            return False
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        dt = now - last_time
                        if dt >= 1.0:
                            speed = (downloaded - last_bytes) / dt
                            last_bytes = downloaded
                            last_time = now
                            elapsed = now - start_time
                            pct = (downloaded / total_bytes * 100) if total_bytes > 0 else 0
                            if progress_msg and downloaded > 50_000:
                                await _update_progress(
                                    progress_msg,
                                    _download_progress_text(title, quality, pct, downloaded, elapsed, speed),
                                    last_edit,
                                    interval=3.0,
                                    job_id=job_id,
                                )

        success = os.path.exists(output_path) and os.path.getsize(output_path) > 50_000
        if success:
            log.info("Direct HTTP download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
        else:
            if os.path.exists(output_path):
                try: os.remove(output_path)
                except Exception: pass
        return success

    except Exception as e:
        log.warning("Direct HTTP download error: %s", e)
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return False


# ── Engine 2: N_m3u8DL-RE (Multi-audio HLS/DASH) ───────────────────────


async def n_m3u8dl_re_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    variant_url: str = "",
    job_id: str | None = None,
) -> bool:
    """Download using N_m3u8DL-RE — video + all audio tracks simultaneously."""
    if not shutil.which("N_m3u8DL-RE"):
        log.warning("N_m3u8DL-RE not found in PATH!")
        return False

    log.info("N_m3u8DL-RE download: url=%s quality=%s", stream_url[:120], quality)

    stem = Path(output_path).stem
    save_dir = str(Path(output_path).parent)
    origin = _get_origin(stream_url)

    # V2 #2: Never overwrite the caller's job_id — Cancel button mapping
    # must stay 1:1 with DownloadJobManager. Use the original ID as-is;
    # only generate a fresh one (same [:10] length as the manager) when
    # the caller did not supply one.
    effective_job_id = job_id if job_id else uuid.uuid4().hex[:10]
    job_temp_dir = _TEMP_BASE / f"re_{stem}_{effective_job_id}"
    job_temp_dir.mkdir(parents=True, exist_ok=True)

    clean_q = str(quality).strip().lower()
    m_h = re.search(r"(\d{3,4})p?", clean_q)
    if m_h and int(m_h.group(1)) in (240, 360, 480, 540, 720, 1080, 1440, 2160):
        height = m_h.group(1)
    elif "4k" in clean_q or "2160" in clean_q:
        height = "2160"
    elif "1080" in clean_q or "fhd" in clean_q:
        height = "1080"
    elif "720" in clean_q or "hdrip" in clean_q or "hd" in clean_q or "webrip" in clean_q:
        height = "720"
    elif "480" in clean_q or "sd" in clean_q or "dvdrip" in clean_q:
        height = "480"
    elif "360" in clean_q:
        height = "360"
    elif "240" in clean_q:
        height = "240"
    else:
        height = ""

    # Attempt 1: Try with resolution selector if height is specified
    async def _run_dl(target_url: str, select_res: bool) -> bool:
        cmd = [
            "N_m3u8DL-RE", target_url,
            "--save-dir", save_dir, "--save-name", stem,
            "--tmp-dir", str(job_temp_dir),
            "--del-after-done",
            "--thread-count", "16",
            "--download-retry-count", "5",
            "--binary-merge",
            "--no-ansi-color",
            "--no-log",
            "-M", "format=mp4",
            "--select-audio", "all",
            "--select-subtitle", "all",
            "--header", f"Referer: {origin}/",
            "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ]

        if select_res and height:
            cmd.extend(["--select-video", f"res=.*{height}.*:for=best"])
        elif str(quality).strip().lower() == "auto":
            cmd.extend(["--auto-select"])
        else:
            # V3 #2: never --auto-select for an explicit quality request.
            # Without a resolution filter N_m3u8DL-RE would grab best/auto
            # and bypass exact-quality enforcement. Keep the resolution
            # filter as the only selector.
            cmd.extend(["--select-video", f"res=.*{height}.*:for=best"] if height else ["--auto-select"])

        env = os.environ.copy()
        env["TERM"] = "xterm"
        env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
        env["DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION"] = "1"
        env["COMPlus_EnableDiagnostics"] = "0"
        env["DOTNET_EnableDiagnostics"] = "0"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        download_job_manager.attach_process(effective_job_id, proc)
        download_job_manager.attach_temp_file(effective_job_id, output_path)
        download_job_manager.attach_temp_file(effective_job_id, str(job_temp_dir))

        last_edit = [0.0]
        start_time = time.time()
        _STALL_TIMEOUT = 120
        _stall_detected = asyncio.Event()

        async def _monitor():
            last_size = 0
            last_time = time.time()
            last_change_time = time.time()
            while proc.returncode is None:
                if download_job_manager.is_job_cancelled(effective_job_id):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    break
                await asyncio.sleep(3)
                try:
                    total = sum(
                        f.stat().st_size for f in Path(save_dir).glob(f"*{glob_escape(stem)}*")
                        if f.is_file()
                    )
                    total += sum(f.stat().st_size for f in job_temp_dir.glob("**/*") if f.is_file())

                    now = time.time()
                    dt = now - last_time
                    speed = max(0, (total - last_size)) / dt if dt > 0 else 0

                    if total > last_size:
                        last_change_time = now
                    elif total == last_size and total > 0:
                        if now - last_change_time > _STALL_TIMEOUT:
                            log.warning("N_m3u8DL-RE stalled for %ds, killing", _STALL_TIMEOUT)
                            _stall_detected.set()
                            proc.kill()
                            break

                    last_size = total
                    last_time = now
                    elapsed = now - start_time

                    est_total = {
                        "1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30
                    }.get(quality, 200) * 1024 * 1024
                    pct = min(95, total / est_total * 100) if est_total > 0 else 0

                    stall_info = ""
                    if speed == 0 and total > 0:
                        stall_secs = int(now - last_change_time)
                        if stall_secs > 10:
                            stall_info = f"\n⚠️ Stalled for {stall_secs}s..."

                    if progress_msg and total > 100_000:
                        await _update_progress(
                            progress_msg,
                            _download_progress_text(title, quality, pct, total, elapsed, speed) + stall_info,
                            last_edit,
                            interval=3.0,
                            job_id=effective_job_id,
                        )
                except Exception:
                    pass

        monitor_task = asyncio.create_task(_monitor())
        stdout_data, stderr_data = b"", b""
        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=1800)
        except asyncio.TimeoutError:
            proc.kill()
            log.error("N_m3u8DL-RE timed out")
            return False
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        if _stall_detected.is_set():
            return False

        if proc.returncode != 0:
            err_msg = stderr_data.decode("utf-8", errors="ignore").strip() or stdout_data.decode("utf-8", errors="ignore").strip()
            log.warning("N_m3u8DL-RE exit code %d: %s", proc.returncode, err_msg[-500:])

        # Look for produced files
        if not os.path.exists(output_path):
            for ext in [".mp4", ".mkv", ".ts"]:
                alt = str(Path(save_dir) / f"{stem}{ext}")
                if os.path.exists(alt) and alt != output_path:
                    os.rename(alt, output_path)
                    break

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    try:
        # Step 1: If exact variant_url is given, download it directly
        # (variant came from strict exact-match resolution — no selector).
        if variant_url and variant_url != stream_url:
            log.info("N_m3u8DL-RE downloading targeted variant directly: %s", variant_url[:80])
            success = await _run_dl(variant_url, select_res=False)
            if success:
                log.info("N_m3u8DL-RE variant download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
                return True

        # Step 2: Run on stream_url with strict resolution filter.
        success = await _run_dl(stream_url, select_res=bool(height))
        if success:
            log.info("N_m3u8DL-RE download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
            return True

        # V3 #2: no Step-3 --auto-select retry for explicit qualities.
        # An auto retry would silently deliver the wrong quality.
        # Only 'auto' requests may retry without a resolution filter.
        if str(quality).strip().lower() == "auto":
            target = variant_url or stream_url
            log.info("N_m3u8DL-RE retrying with --auto-select on %s", target[:80])
            success = await _run_dl(target, select_res=False)
            if success:
                log.info("N_m3u8DL-RE auto-select complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
                return True

        return False
    finally:
        shutil.rmtree(job_temp_dir, ignore_errors=True)


# ── Engine 3: FFmpeg Fallback (M3U8 / Media Streams) ───────────────────


async def ffmpeg_download(
    stream_url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
    referer: str = "",
    job_id: str | None = None,
) -> bool:
    """Download stream using FFmpeg as a reliable universal fallback."""
    if not shutil.which("ffmpeg"):
        log.error("FFmpeg not found in PATH!")
        return False

    log.info("FFmpeg fallback download: url=%s quality=%s", stream_url[:120], quality)
    origin = referer or _get_origin(stream_url)

    cmd = ["ffmpeg", "-y"]
    headers = f"Referer: {origin}/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
    cmd.extend(["-headers", headers])
    cmd.extend(["-i", stream_url, "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-bsf:a", "aac_adtstoasc", output_path])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    download_job_manager.attach_process(job_id, proc)
    download_job_manager.attach_temp_file(job_id, output_path)

    # Drain stderr continuously (avoids pipe deadlock) and keep a tail so
    # failures log the REAL ffmpeg reason (HTTP 403, format errors, …).
    from collections import deque
    stderr_tail: deque = deque(maxlen=25)

    async def _drain_stderr():
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_tail.append(line.decode("utf-8", "replace").strip()[-300:])
        except Exception:
            pass

    drain_task = asyncio.create_task(_drain_stderr())

    last_edit = [0.0]
    start_time = time.time()

    async def _monitor():
        last_size = 0
        last_time = time.time()
        while proc.returncode is None:
            if download_job_manager.is_job_cancelled(job_id):
                try:
                    proc.kill()
                except Exception:
                    pass
                break
            await asyncio.sleep(3)
            try:
                total = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                now = time.time()
                dt = now - last_time
                speed = max(0, (total - last_size)) / dt if dt > 0 else 0
                last_size = total
                last_time = now
                elapsed = now - start_time

                est_total = {
                    "1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30
                }.get(quality, 200) * 1024 * 1024
                pct = min(95, total / est_total * 100) if est_total > 0 else 0

                if progress_msg and total > 100_000:
                    await _update_progress(
                        progress_msg,
                        _download_progress_text(title, quality, pct, total, elapsed, speed),
                        last_edit,
                        interval=3.0,
                        job_id=job_id,
                    )
            except Exception:
                pass

    monitor_task = asyncio.create_task(_monitor())
    try:
        await asyncio.wait_for(proc.wait(), timeout=1800)
    except asyncio.TimeoutError:
        proc.kill()
        log.error("FFmpeg timed out for %s", stream_url[:60])
        return False
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        try:
            await asyncio.wait_for(drain_task, timeout=2)
        except Exception:
            drain_task.cancel()

    success = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    if success:
        log.info("FFmpeg download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
    else:
        # V3 #10: never fail silently — the final DM block shows diagnostics,
        # but the file log must carry the exact failing URL/quality too.
        log.warning("FFmpeg download failed (no output file): url=%s quality=%s", stream_url[:120], quality)
        if stderr_tail:
            log.warning("FFmpeg stderr tail: %s", " | ".join(
                ln for ln in list(stderr_tail)[-6:] if ln and "frame=" not in ln
            )[:600])
    return success


# ── Video Validation & M3U8 Variant Helpers ───────────────────────────


async def resolve_m3u8_variant(master_url: str, target_quality: str, referer: str = "") -> str:
    """V3 #2: strict exact-variant only.

    If master_url is an M3U8 master playlist, return the variant URL whose
    height exactly equals the requested quality, else return "" (no exact
    match → caller must try the next source, never auto-pick closest).
    Non-playlist URLs pass through unchanged. ``auto`` passes through.
    """
    if not master_url or ".m3u8" not in master_url.lower():
        return master_url

    t_clean = (target_quality or "").strip().lower()
    if t_clean == "auto":
        return master_url
    h_target = None
    m_res = re.search(r"(\d{3,4})p?", t_clean)
    if m_res and int(m_res.group(1)) in (240, 360, 480, 540, 720, 1080, 1440, 2160):
        h_target = int(m_res.group(1))
    elif "4k" in t_clean or "2160" in t_clean:
        h_target = 2160
    elif "1080" in t_clean or "fhd" in t_clean:
        h_target = 1080
    elif "720" in t_clean or "hdrip" in t_clean or "hd rip" in t_clean or "webrip" in t_clean or "hd" in t_clean:
        h_target = 720
    elif "480" in t_clean or "sd" in t_clean or "dvdrip" in t_clean:
        h_target = 480
    elif "360" in t_clean:
        h_target = 360
    elif "240" in t_clean:
        h_target = 240

    if not h_target:
        return master_url

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": referer or _get_origin(master_url) + "/",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(master_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return master_url
                text = await resp.text()

        if "#EXT-X-STREAM-INF" not in text:
            return master_url

        from urllib.parse import urljoin
        lines = text.splitlines()
        variants: list[tuple[int, str]] = []
        curr_h = 0

        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#EXT-X-STREAM-INF:"):
                res_m = re.search(r"RESOLUTION=\d+x(\d+)", line, re.I)
                if res_m:
                    curr_h = int(res_m.group(1))
                else:
                    curr_h = 0
            elif not line.startswith("#") and curr_h > 0:
                var_url = urljoin(master_url, line)
                variants.append((curr_h, var_url))
                curr_h = 0

        if not variants:
            return master_url

        exact = [u for h, u in variants if h == h_target]
        if exact:
            return exact[0]

        # V3 #2: no closest/auto fallback — exact quality absent in this
        # master playlist, so signal failure for next-source fallback.
        log.info("resolve_m3u8_variant: no exact %sp variant in master (%s variants) — rejecting",
                 h_target, len(variants))
        return ""
    except Exception as e:
        log.debug("resolve_m3u8_variant error: %s", e)
        return master_url


def _parse_safe_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        v_clean = val.strip()
        if not v_clean or v_clean.upper() in ("N/A", "NONE", "NULL"):
            return default
        try:
            return float(v_clean)
        except (ValueError, TypeError):
            return default
    return default


async def validate_video_file(file_path: str, min_size_bytes: int = 500_000) -> tuple[bool, str, dict]:
    """
    Validate that the downloaded file is a genuine, uncorrupted, playable video.
    Checks:
    1. File existence and minimum size threshold (default 500 KB).
    2. FFprobe inspection for valid video streams, codecs, and duration.
    Returns: (is_valid, error_reason, metadata_dict)
    """
    if not os.path.exists(file_path):
        return False, "File does not exist on disk", {}

    size = os.path.getsize(file_path)
    if size < min_size_bytes:
        return False, f"File size too small ({size} bytes, min {min_size_bytes})", {}

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return True, "", {"size": size}

    cmd = [
        ffprobe_bin,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,duration:format=duration,size,format_name",
        "-of", "json",
        file_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip()
            return False, f"FFprobe validation failed (exit {proc.returncode}): {err_msg[:120]}", {}

        import json
        data = json.loads(stdout.decode(errors="replace"))
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        if not streams:
            return False, "No valid video stream detected in file (corrupted or empty)", {}

        v_stream = streams[0]
        w = v_stream.get("width")
        h = v_stream.get("height")
        codec = v_stream.get("codec_name")

        dur = (
            _parse_safe_float(format_info.get("duration"))
            or _parse_safe_float(v_stream.get("duration"))
            or _parse_safe_float(v_stream.get("tags", {}).get("DURATION"))
            or _parse_safe_float(format_info.get("tags", {}).get("DURATION"))
        )
        if dur == 0.0:
            for s in streams:
                s_dur = _parse_safe_float(s.get("duration"))
                if s_dur > 0:
                    dur = s_dur
                    break
        if dur == 0.0:
            bitrate = _parse_safe_float(format_info.get("bit_rate")) or _parse_safe_float(v_stream.get("bit_rate"))
            if bitrate > 0 and size > 0:
                dur = (size * 8.0) / bitrate

        info = {
            "width": int(w or 0),
            "height": int(h or 0),
            "codec": codec,
            "duration": dur,
            "size": size,
            "format": format_info.get("format_name", "mp4"),
        }
        return True, "", info

    except asyncio.TimeoutError:
        return False, "FFprobe validation timed out (corrupted stream/hung container)", {}
    except Exception as e:
        log.warning("Validation exception for %s: %s", file_path, e)
        if size > 5_000_000:
            return True, "", {"size": size}
        return False, f"Validation error: {e}", {}


async def fix_or_verify_video_resolution(
    file_path: str,
    requested_quality: str,
    meta: dict,
    progress_msg: Message | None = None,
) -> tuple[bool, str, dict]:
    """
    Validate that downloaded video resolution matches requested quality (Issue #20 - Bug 29).
    If a higher resolution stream was downloaded (e.g., 1080p stream for 480p/720p request),
    automatically transcode/downscale with FFmpeg to deliver the true requested resolution
    and expected lightweight file size.
    If the resolution is severely undersized (e.g., <=480p when 1080p was explicitly requested),
    reject so multi-source fallbacks can try for true HD sources.
    """
    clean_q = requested_quality.lower().strip()
    target_height = 0
    if "2160" in clean_q or "4k" in clean_q:
        target_height = 2160
    elif "1080" in clean_q or "fhd" in clean_q:
        target_height = 1080
    elif "720" in clean_q or "hd" in clean_q or "hdrip" in clean_q:
        target_height = 720
    elif "480" in clean_q or "sd" in clean_q or "dvdrip" in clean_q:
        target_height = 480
    elif "360" in clean_q:
        target_height = 360
    elif "240" in clean_q:
        target_height = 240

    if not target_height:
        return True, "", meta

    w = int(meta.get("width") or 0)
    h = int(meta.get("height") or 0)
    if not w or not h:
        # V3 #2: cannot verify → reject explicit requests (next source),
        # accept only for auto.
        if (requested_quality or "").strip().lower() == "auto":
            return True, "", meta
        return False, f"Resolution unverifiable for {target_height}p request", meta

    actual_res = min(w, h)

    # V3 #2 strict bands (±~12% tolerance for encoder variance).
    # Oversize → downscale to exact; undersize/out-of-band → reject so the
    # caller tries the next source instead of delivering the wrong quality.
    bands = {2160: (1900, 2300), 1080: (950, 1200), 720: (630, 810),
             480: (420, 530), 360: (310, 410), 240: (200, 280)}
    lo, hi = bands.get(target_height, (0, 0))

    needs_downscale = actual_res > hi

    if needs_downscale:
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            log.info(
                "Quality mismatch: requested %sp but got %dp (res %dx%d). Downscaling via FFmpeg...",
                target_height, actual_res, w, h
            )
            if progress_msg:
                try:
                    await progress_msg.edit_text(
                        f"⚙️ <b>Optimizing Resolution</b>\n"
                        f"┌ 🎬 Downscaling to requested {target_height}p...\n"
                        f"└ ⏳ Please wait...",
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    pass

            temp_scaled = str(Path(file_path).parent / f"scaled_{target_height}_{Path(file_path).name}")
            cmd = [
                ffmpeg_bin,
                "-y",
                "-i", file_path,
                "-vf", f"scale=-2:{target_height}",
                "-c:v", "libx264",
                "-crf", "23",
                "-preset", "veryfast",
                "-c:a", "copy",
                temp_scaled,
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
                if proc.returncode == 0 and os.path.exists(temp_scaled) and os.path.getsize(temp_scaled) > 100_000:
                    os.replace(temp_scaled, file_path)
                    valid, err, new_meta = await validate_video_file(file_path)
                    if valid:
                        log.info(
                            "Downscale successful: new size %s, resolution %sx%s",
                            _format_size(os.path.getsize(file_path)),
                            new_meta.get("width"),
                            new_meta.get("height"),
                        )
                        return True, "", new_meta
                else:
                    log.warning("FFmpeg downscale failed (exit %s): %s", proc.returncode, stderr.decode()[-200:])
            except Exception as e:
                log.warning("Downscale error: %s", e)
            finally:
                if os.path.exists(temp_scaled):
                    try:
                        os.remove(temp_scaled)
                    except Exception:
                        pass

    if lo and hi and not (lo <= actual_res <= hi):
        return False, f"Resolution mismatch: requested {target_height}p but got {actual_res}p", meta

    return True, "", meta


# ── Unified Media Downloader ──────────────────────────────────────────


async def _maybe_unzip_download(path: str, progress_msg=None, title="", job_id: str | None = None) -> str:
    """HubCloud/AnimeDrive sometimes serve ZIP-wrapped videos (HTTP header
    ``application/x-zip``, ``PK\\x03\\x04`` magic). ffprobe on a ZIP fails with
    "moov atom not found" and the bot wrongly rejected a perfectly good
    download (issue #30). Extract the contained video and return its path;
    returns the original path for non-ZIP files or on extraction failure.
    """
    try:
        if not os.path.exists(path):
            return path
        import zipfile
        if not zipfile.is_zipfile(path):
            return path
        log.info("Downloaded file is a ZIP archive — extracting: %s", os.path.basename(path))
        if progress_msg:
            try:
                await progress_msg.edit_text(
                    f"📦 <b>Extracting archive…</b>\n"
                    f"┌ 📺 {title}\n"
                    f"└ ⏳ Unpacking video file from ZIP",
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=cancel_markup_for(job_id),
                )
            except Exception:
                pass
        zf = zipfile.ZipFile(path)
        member = None
        for zi in zf.infolist():
            if zi.is_dir():
                continue
            if zi.filename.lower().endswith((".mkv", ".mp4", ".webm", ".avi", ".mov", ".ts")):
                member = zi
                break
        if member is None:
            for zi in zf.infolist():
                if not zi.is_dir():
                    member = zi
                    break
        if member is None:
            zf.close()
            log.warning("ZIP archive contains no files: %s", os.path.basename(path))
            return path

        ext = os.path.splitext(member.filename)[1] or ".mkv"
        out_path = os.path.splitext(path)[0] + ext
        tmp_path = os.path.splitext(path)[0] + ".extracting" + ext

        def _extract():
            with zf.open(member) as src, open(tmp_path, "wb") as dst:
                while True:
                    if download_job_manager.is_job_cancelled(job_id):
                        raise InterruptedError("cancelled during ZIP extraction")
                    buf = src.read(8 * 1024 * 1024)
                    if not buf:
                        break
                    dst.write(buf)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _extract)
        finally:
            zf.close()

        if os.path.exists(path):
            os.remove(path)
        os.replace(tmp_path, out_path)
        log.info("ZIP extraction complete: %s (%s)",
                 os.path.basename(out_path), _format_size(os.path.getsize(out_path)))
        return out_path
    except InterruptedError:
        try:
            for leftover in (path + ".extracting" + os.path.splitext(path)[1],):
                if os.path.exists(leftover):
                    os.remove(leftover)
        except Exception:
            pass
        return path
    except Exception as e:
        log.warning("ZIP extraction failed for %s: %s", os.path.basename(path), e)
        return path


async def download_media(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    variant_url: str = "",
    referer: str = "",
    job_id: str | None = None,
    refresh_url=None,
) -> bool:
    """
    Unified multi-engine downloader:
    1. If URL is MP4 / direct file: use direct HTTP stream download.
    2. If URL is M3U8: try N_m3u8DL-RE.
    3. If N_m3u8DL-RE fails or is unavailable: fallback to FFmpeg.

    refresh_url: optional async callable returning a FRESH direct URL for the
    same title/quality (fixes time-limited signed links that 403 between
    resolution and download). When provided, the URL is preflight-checked
    first; a dead link triggers exactly one fresh re-resolve before failing.
    """
    if refresh_url is not None:
        # Stale-URL preflight: signed/proxied links can 403 between resolve
        # and download. Verify reachability first; on failure pull ONE fresh
        # URL from the source instead of burning minutes on a dead link.
        try:
            from extractors.health_probe import probe_url_health
            pre = await probe_url_health(stream_url, referer or "")
            if not pre.get("ok"):
                log.warning("Preflight %s for [%s] %s — refreshing URL once",
                            pre.get("error", "failed"), quality, stream_url[:80])
                try:
                    fresh = refresh_url()
                    if asyncio.iscoroutine(fresh):
                        fresh = await fresh
                except Exception as re:
                    log.warning("URL refresh failed: %s", re)
                    fresh = None
                if fresh and isinstance(fresh, str) and fresh.startswith("http") and fresh != stream_url:
                    pre2 = await probe_url_health(fresh, referer or "")
                    if pre2.get("ok"):
                        log.info("Refresh recovered reachability (%s) — downloading fresh URL",
                                 pre2.get("latency_ms"))
                        if variant_url == stream_url:
                            variant_url = ""
                        stream_url = fresh
                    else:
                        log.warning("Fresh URL also unreachable (%s) — trying original anyway",
                                    pre2.get("error"))
                elif fresh and fresh != stream_url:
                    stream_url = fresh
        except Exception as pe:
            log.debug("Preflight check skipped: %s", pe)

    is_hls = (
        ".m3u8" in stream_url.lower()
        or ".m3u8" in variant_url.lower()
        or "proxyhls" in stream_url.lower()
        or "playlist" in stream_url.lower()
        or "manifest" in stream_url.lower()
    )
    is_mp4 = not is_hls and (
        ".mp4" in stream_url.lower()
        or ".mkv" in stream_url.lower()
        or "googleusercontent" in stream_url
        or ".googleapis.com" in stream_url          # storage.googleapis.com/... (extensionless)
        or "pixeldrain" in stream_url               # /api/file/<id> (extensionless)
        or "instant_dl" in stream_url
        or "drive.google" in stream_url
    )

    if is_mp4:
        log.info("Detected direct MP4/file URL, using direct HTTP downloader")
        ok = await direct_http_download(stream_url, output_path, progress_msg, title, quality, referer=referer, job_id=job_id)
        if ok:
            return True
        log.warning("Direct HTTP download failed, falling back to FFmpeg")
        return await ffmpeg_download(stream_url, output_path, progress_msg, title, quality, referer=referer, job_id=job_id)

    # M3U8 stream
    if shutil.which("N_m3u8DL-RE"):
        ok = await n_m3u8dl_re_download(stream_url, quality, output_path, progress_msg, title, variant_url=variant_url, job_id=job_id)
        if ok:
            return True
        log.warning("N_m3u8DL-RE failed for %s, falling back to FFmpeg", stream_url[:60])

    # Fallback to FFmpeg on resolved variant_url or stream_url
    # V3 #2: resolve_m3u8_variant returns "" when the master playlist has
    # no exact-quality variant — fail this source instead of downloading
    # the wrong quality from the master URL.
    target_url = variant_url or stream_url
    if not variant_url and ".m3u8" in stream_url.lower() and str(quality).strip().lower() != "auto":
        resolved = await resolve_m3u8_variant(stream_url, quality, referer=referer)
        if resolved == "":
            log.warning("No exact %s variant in M3U8 master — rejecting source (V3 #2)", quality)
            return False
        if resolved and resolved != stream_url:
            target_url = resolved

    ok = await ffmpeg_download(target_url, output_path, progress_msg, title, quality, referer=referer, job_id=job_id)
    if not ok and target_url != stream_url:
        ok = await ffmpeg_download(stream_url, output_path, progress_msg, title, quality, referer=referer, job_id=job_id)

    if not ok:
        # V3 #10: exact failure diagnostics in file log (DM block covers user side).
        log.warning("download_media failed: [%s] %s (target=%s)", quality, stream_url[:120], target_url[:120])
    return ok


def _worker_username_for_quality(q_label: str, fallback: str) -> str:
    """V3 #15: worker username for a quality label.

    Every fallback to the main bot is logged (no silent routing misses).
    Compound labels ("1080p HQ x265") are normalized inside
    ChildBotManager.get_bot_for_quality.
    """
    try:
        from bot.child_bots import child_bot_manager
        if child_bot_manager:
            w = child_bot_manager.get_bot_for_quality(q_label)
            if w:
                return w.lstrip("@")
            log.warning("V3 #15: no active worker for [%s] — falling back to main bot", q_label)
    except Exception as e:
        log.warning("V3 #15: worker lookup failed for [%s]: %s", q_label, e)
    return fallback


_GENRE_EMOJIS = {
    "Action": "👊",
    "Adventure": "🗺",
    "Fantasy": "🌓",
    "Drama": "🎭",
    "Comedy": "😂",
    "Romance": "❤️",
    "Sci-Fi": "🚀",
    "Supernatural": "⚔️",
    "Mystery": "🔎",
    "Suspense": "😱",
    "Horror": "👻",
    "Slice of Life": "🍃",
    "Sports": "⚽",
    "Thriller": "⚡",
    "Psychological": "🧠",
    "Mecha": "🤖",
    "Music": "🎵",
}


async def _build_episode_caption_and_markup(
    title: str,
    quality: str,
    series_slug: str,
    client: Client,
    target_chat: int,
    language: str = "",
    post_style: str = "modern",
) -> tuple[str, InlineKeyboardMarkup | None]:
    import html as htmlmod
    from bot.database import db

    # Parse Series Title, Season, Episode
    m = re.match(r"^(.*?)\s+[Ss](\d+)[Ee](\d+)", title)
    if m:
        s_title = m.group(1).strip()
        season_num = int(m.group(2))
        ep_num = int(m.group(3))
    else:
        s_title = title.split(" S")[0].strip() if " S" in title else title
        season_num = 1
        ep_num = 1

    # Fetch AniList metadata for genres, status, total episodes
    # V2 #5: only use AniList when it confidently matches this anime.
    # get_anilist_metadata does fuzzy search — a wrong match would post
    # wrong status/episode-count/genres, so verify title overlap first.
    meta = None
    try:
        from utils.anilist import get_anilist_metadata
        cand = await get_anilist_metadata(s_title)
        if cand:
            meta_title = str(cand.get("title") or cand.get("name") or "")
            q_toks = set(re.sub(r"[^a-z0-9 ]", " ", s_title.lower()).split()) - {"the", "a", "an"}
            m_toks = set(re.sub(r"[^a-z0-9 ]", " ", meta_title.lower()).split()) - {"the", "a", "an"}
            if q_toks and m_toks and (q_toks & m_toks):
                meta = cand
            else:
                log.debug("AniList match rejected for '%s' (got '%s')", s_title, meta_title)
    except Exception:
        pass

    status = meta.get("status") if meta else None
    total_eps = meta.get("episodes") if meta else None
    # V2 #5: sanity-filter AniList numbers — absurd counts are wrong matches.
    if total_eps is not None:
        try:
            total_eps = int(total_eps)
            if total_eps <= 0 or total_eps > 3000:
                total_eps = None
        except Exception:
            total_eps = None
    raw_genres = (meta.get("genres") if meta else None) or []

    formatted_genres = []
    for g in raw_genres[:3]:
        emoji = _GENRE_EMOJIS.get(g, "✨")
        clean_tag = re.sub(r'[^a-zA-Z0-9]', '', g)
        formatted_genres.append(f"{emoji} #{clean_tag}")
    genres_str = ", ".join(formatted_genres)

    # V2 #5: never hardcode audio. Explicit language param wins; title/slug
    # hints are next; otherwise omit the line instead of guessing "Multi Audio".
    audio_str = ""
    if language:
        audio_str = language if ("dub" in language.lower() or "sub" in language.lower()) else f"{language.title()} Dub"
    else:
        blob = f"{s_title} {series_slug or ''}".lower()
        found_langs = [L for L in ("hindi", "tamil", "telugu", "english", "japanese") if L in blob]
        if len(found_langs) == 1:
            audio_str = f"{found_langs[0].title()} Dub"
        elif len(found_langs) > 1:
            audio_str = "Multi Audio"
        elif meta and meta.get("audio"):
            audio_str = meta["audio"]

    bot_me = getattr(client, "me", None)
    bname = bot_me.username if bot_me and bot_me.username else "animedekho"

    def _worker_for(q_label: str) -> str:
        """V3 #15: per-quality worker username; every fallback is logged."""
        return _worker_username_for_quality(q_label, bname)

    caption_lines = [
        f"✦ <b>{htmlmod.escape(s_title)}</b> ✦",
        f"Season {season_num:02d} • Episode {ep_num:02d}",
        "━━━━━━━━━━━━━━━━━━",
    ]
    if audio_str:
        caption_lines.append(f"⬡ <b>Audio:</b> {audio_str}")
    if status:
        caption_lines.append(f"⬡ <b>Status:</b> {status}")
    if total_eps:
        caption_lines.append(f"⬡ <b>Total Episodes:</b> {total_eps}")
    if genres_str:
        caption_lines.append(f"\n✦ <b>Genres:</b> {genres_str}")
    caption_lines.append("━━━━━━━━━━━━━━━━━━")
    caption_lines.append(f"✦ <b>Powered By:</b> @{bname}")
    caption = "\n".join(caption_lines)

    slug = series_slug or re.sub(r'[^a-zA-Z0-9]+', '-', s_title).strip('-').lower()
    from bot.database import db
    from utils.helpers import encode_file_param

    available_qualities = set()
    if quality:
        available_qualities.add(quality.upper())
    if db:
        ep_key = f"S{season_num:01d}E{ep_num:02d}"
        try:
            cached_docs = await db.files.find({"series_slug": slug, "episode_key": ep_key}).to_list(length=10)
            for doc in cached_docs:
                if doc.get("quality"):
                    available_qualities.add(doc["quality"].upper())
        except Exception:
            pass

    order = ["480P", "720P", "1080P", "1080P HQ", "4K", "HDRIP"]
    sorted_q = sorted(available_qualities, key=lambda x: order.index(x) if x in order else 99)

    button_rows = []
    curr_row = []
    for q_item in sorted_q:
        q_code = q_item.lower().replace(" ", "")
        param = encode_file_param(f"get_{slug}_{q_code}_S{season_num:01d}E{ep_num:02d}")
        link = f"https://t.me/{_worker_for(q_item)}?start={param}"
        curr_row.append(InlineKeyboardButton(f"{q_item} ↗", url=link))
        if len(curr_row) == 2:
            button_rows.append(curr_row)
            curr_row = []
    if curr_row:
        button_rows.append(curr_row)

    markup = InlineKeyboardMarkup(button_rows) if button_rows else None
    return caption, markup


# ── Main download + upload ────────────────────────────────────────────


async def download_and_upload(
    chat_id: int,
    stream_url: str,
    quality: str,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
    variant_url: str = "",
    referer: str = "",
    poster_url: str = "",
    destination_channel_id: int | None = None,
    series_slug: str = "",
    language: str = "",
    is_movie: bool = False,
    job_id: str | None = None,
    refresh_url=None,
) -> tuple[bool, Message | None]:
    """Download video + upload via Pyrogram MTProto with progress, custom thumbnail, and dump channel.

    refresh_url: optional async callable returning a fresh direct URL
    (forwarded to download_media for stale-link recovery)."""
    output_path = str(_TEMP_BASE / filename)
    overall_start = time.time()
    thumb_path = None
    custom_thumb_path = None
    tracked_temp_files: set[str] = {output_path}
    download_job_manager.attach_temp_file(job_id, output_path)

    try:
        # 1. Custom Thumbnail System (Point 5 - OFF by default, falls back to AniList poster)
        from bot.database import db
        if db:
            try:
                # Detect language if not specified
                if not language and title:
                    t_low = title.lower()
                    for l_candidate in ("hindi", "tamil", "telugu", "multi", "english"):
                        if l_candidate in t_low:
                            language = l_candidate
                            break
                custom_thumb_id = await db.get_custom_thumbnail(series_slug=series_slug, language=language)
                if custom_thumb_id:
                    c_path = str(_TEMP_BASE / f"thumb_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg")
                    dl_res = await client.download_media(custom_thumb_id, file_name=c_path)
                    if dl_res and os.path.exists(str(dl_res)):
                        thumb_path = str(dl_res)
                        custom_thumb_path = thumb_path
                        tracked_temp_files.add(thumb_path)
                        log.info("Using custom thumbnail for %s (type: %s)", title, language or series_slug or "global")
                        try:
                            from bot.thumbnail import enhance_custom_thumbnail
                            enh_custom = str(_TEMP_BASE / f"enhanced_{os.path.basename(custom_thumb_path)}")
                            enh_res = enhance_custom_thumbnail(custom_thumb_path, enh_custom)
                            if enh_res and os.path.exists(enh_res):
                                thumb_path = enh_res
                                custom_thumb_path = enh_res
                                tracked_temp_files.add(enh_res)
                                log.info("Enhanced custom thumbnail applied for %s: %s", title, enh_res)
                        except Exception as eht:
                            log.warning("Custom thumbnail enhancement failed: %s", eht)
            except Exception as cte:
                log.debug("Custom thumbnail check failed: %s", cte)

        # 2. Poster Fallback (AniList as primary, then scraped, then default configured thumbnails)
        if not thumb_path:
            if not poster_url:
                try:
                    from utils.anilist import resolve_best_poster
                    poster_url = await resolve_best_poster(title, "", is_movie=is_movie)
                except Exception:
                    pass

            if poster_url:
                try:
                    from bot.library import _download_poster
                    thumb_path = await _download_poster(poster_url)
                    if thumb_path:
                        tracked_temp_files.add(thumb_path)
                except Exception as pe:
                    log.debug("Poster thumbnail download failed: %s", pe)

            # If poster thumbnail download failed or missing, use configured default thumbnail (Issue #9)
            if not thumb_path:
                from config import Config
                fallback_img = (
                    getattr(Config, "DEFAULT_MOVIE_THUMB", None)
                    if is_movie
                    else getattr(Config, "DEFAULT_ANIME_THUMB", None)
                )
                if fallback_img:
                    try:
                        from bot.library import _download_poster
                        thumb_path = await _download_poster(fallback_img)
                        if thumb_path:
                            tracked_temp_files.add(thumb_path)
                    except Exception as fe:
                        log.debug("Fallback thumbnail download failed: %s", fe)

        # Resolve M3U8 variant for exact quality (V3 #2).
        # "" means master playlist lacks the exact quality → fail this
        # source so the caller tries the next source instead of a wrong one.
        if (not variant_url or variant_url == stream_url) and ".m3u8" in stream_url.lower() \
                and str(quality).strip().lower() != "auto":
            resolved_var = await resolve_m3u8_variant(stream_url, quality, referer=referer)
            if resolved_var == "":
                from bot.database import db as _db2
                if _db2:
                    try:
                        await _db2.log_download_failure(
                            title=title, quality=quality, source=referer or "stream",
                            error=f"No exact {quality} variant in M3U8 master", user_id=chat_id,
                        )
                    except Exception:
                        pass
                await progress_msg.edit_text(
                    f"❌ <b>Quality Unavailable</b>\n"
                    f"┌ 📺 {title}\n"
                    f"└ 💔 No exact {quality} stream on this source — trying next source...",
                    parse_mode=enums.ParseMode.HTML)
                return False, None
            if resolved_var and resolved_var != stream_url:
                variant_url = resolved_var

        success = await download_media(
            stream_url, quality, output_path, progress_msg, title, variant_url=variant_url, referer=referer, job_id=job_id,
            refresh_url=refresh_url,
        )

        if not success:
            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error="Could not download from media server", user_id=chat_id,
                    )
                except Exception:
                    pass
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 Could not download from server",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error="File is empty (0 bytes)", user_id=chat_id,
                    )
                except Exception:
                    pass
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 File is empty",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        # ZIP-wrapped video (HubCloud application/x-zip) → extract before
        # validation, else ffprobe rejects the archive ("moov atom not found").
        extracted = await _maybe_unzip_download(output_path, progress_msg, title, job_id)
        if extracted != output_path:
            output_path = extracted

        # Video Integrity Validation (Issue #8 - Point 7)
        is_valid, val_err, meta = await validate_video_file(output_path)
        if not is_valid:
            log.warning("Downloaded video failed integrity validation: %s (error: %s)", filename, val_err)
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass

            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error=f"Corrupted video rejected: {val_err}", user_id=chat_id,
                    )
                except Exception:
                    pass

            await progress_msg.edit_text(
                f"❌ <b>Download Corrupted / Invalid</b>\n"
                f"┌ 📺 {title}\n"
                f"├ ⚠️ Reason: {val_err[:80]}\n"
                f"└ 🔄 Discarded corrupted file, retrying fallback...",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        # Resolution Validation & Optimization (Issue #20 - Bug 29)
        res_ok, res_err, meta = await fix_or_verify_video_resolution(
            output_path, quality, meta, progress_msg=progress_msg
        )
        if not res_ok:
            log.warning("Downloaded video failed resolution validation: %s (error: %s)", filename, res_err)
            try:
                if os.path.exists(output_path):
                    os.remove(output_path)
            except Exception:
                pass

            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error=f"Resolution rejected: {res_err}", user_id=chat_id,
                    )
                except Exception:
                    pass

            await progress_msg.edit_text(
                f"❌ <b>Incorrect Quality Downloaded</b>\n"
                f"┌ 📺 {title}\n"
                f"├ ⚠️ Reason: {res_err[:80]}\n"
                f"└ 🔄 Discarded wrong resolution, retrying fallback...",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        vid_duration = int(round(meta.get("duration") or 0))
        vid_width = int(meta.get("width") or 0)
        vid_height = int(meta.get("height") or 0)

        # Fallback duration probe if 0 to prevent 0.00 min on Telegram (Issue #22 & #23)
        if vid_duration == 0:
            try:
                ff_bin = shutil.which("ffprobe")
                if ff_bin and os.path.exists(output_path):
                    pr = await asyncio.create_subprocess_exec(
                        ff_bin, "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", output_path,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    out, _ = await asyncio.wait_for(pr.communicate(), timeout=8)
                    val = out.decode().strip()
                    if val and val.replace(".", "", 1).isdigit():
                        vid_duration = max(1, int(round(float(val))))
            except Exception as fe:
                log.debug("Fallback duration probe failed: %s", fe)

        # Auto Thumbnail Generator (Issue #8 - Point 9)
        # If user has not uploaded an explicit custom thumbnail, generate a branded 1280x720 HD thumbnail
        if not custom_thumb_path:
            auto_thumb_on = True
            if db:
                auto_thumb_on = await db.get_auto_thumb()
            if auto_thumb_on:
                try:
                    from bot.thumbnail import generate_auto_thumbnail
                    bot_uname = getattr(getattr(client, "me", None), "username", None) or "AnimeDekhoBot"
                    aud_tag = "Hindi Dub" if "hindi" in title.lower() or "hindi" in language.lower() else "Multi Audio"
                    ep_m = re.search(r"S(\d+)E(\d+)", filename, re.I)
                    ep_tag = f"Season {int(ep_m.group(1)):02d} • Episode {int(ep_m.group(2)):02d}" if ep_m else ""
                    is_mov = bool("movie" in title.lower() or "movie" in filename.lower() or not ep_m)
                    auto_thumb_file = str(_TEMP_BASE / f"autothumb_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg")
                    template_choice = await db.get_thumb_template() if db else None
                    random_choice = await db.get_random_thumb_template() if db else False
                    gen_thumb = generate_auto_thumbnail(
                        title=title,
                        episode_info=ep_tag,
                        quality=quality,
                        audio=aud_tag,
                        poster_path=thumb_path or "",
                        output_path=auto_thumb_file,
                        bot_username=bot_uname,
                        template_name="random" if random_choice else template_choice,
                        is_movie=is_mov,
                    )
                    if gen_thumb and os.path.exists(gen_thumb):
                        thumb_path = gen_thumb
                        tracked_temp_files.add(gen_thumb)
                        log.info("Applied generated Auto Thumbnail for %s", filename)
                except Exception as ate:
                    log.warning("Auto thumbnail generation failed: %s", ate)

        # If thumb_path is still a raw poster (not enhanced/auto-generated), enhance it for 16:9 sharpness
        if thumb_path and not custom_thumb_path and "autothumb" not in thumb_path and "enhanced" not in thumb_path:
            try:
                from bot.thumbnail import enhance_custom_thumbnail
                enh_poster_path = str(_TEMP_BASE / f"enhanced_poster_{os.path.basename(thumb_path)}")
                enh_poster_res = enhance_custom_thumbnail(thumb_path, enh_poster_path)
                if enh_poster_res and os.path.exists(enh_poster_res):
                    thumb_path = enh_poster_res
                    tracked_temp_files.add(enh_poster_res)
                    log.info("Applied enhanced poster thumbnail: %s", enh_poster_res)
            except Exception as epe:
                log.debug("Poster enhancement fallback skipped: %s", epe)

        file_size = os.path.getsize(output_path)

        if file_size > TG_UPLOAD_LIMIT:
            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error=f"File exceeds Telegram 2GB limit ({_format_size(file_size)})", user_id=chat_id,
                    )
                except Exception:
                    pass
            await progress_msg.edit_text(
                f"⚠️ <b>File Too Large</b>\n"
                f"┌ 📺 {title}\n"
                f"├ 💾 {_format_size(file_size)} (max 2 GB)\n"
                f"└ 💡 Try a lower quality",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        # Upload to Telegram
        upload_last_edit = [0.0]
        upload_start = [0.0]
        upload_last_bytes = [0]
        upload_last_time = [0.0]
        upload_speed = [0.0]

        async def _upload_progress(current: int, total: int):
            if download_job_manager.is_job_cancelled(job_id):
                raise asyncio.CancelledError("Upload cancelled by user")
            if upload_start[0] == 0:
                upload_start[0] = time.time()
                upload_last_time[0] = time.time()
            now = time.time()
            dt = now - upload_last_time[0]
            if dt > 0.5:
                upload_speed[0] = (current - upload_last_bytes[0]) / dt
                upload_last_bytes[0] = current
                upload_last_time[0] = now
            await _update_progress(
                progress_msg,
                _upload_progress_text(title, quality, current, total, upload_speed[0], time.time() - upload_start[0]),
                upload_last_edit,
                interval=3.0,
                job_id=job_id,
            )

        # V3 #14 + issue #30: mapped channel → mapped channel; otherwise the
        # MAIN channel still gets the post (never silently skip channel
        # posting for private-chat downloads). chat_id itself only when no
        # main channel is configured (or chat_id IS a group/channel).
        target_upload_chat = destination_channel_id
        if not target_upload_chat:
            if chat_id > 0:
                try:
                    from bot.database import db as _db_main
                    _main = await _db_main.get_main_channel() if _db_main else None
                except Exception:
                    _main = None
                target_upload_chat = int(_main) if _main else chat_id
            else:
                target_upload_chat = chat_id
        await progress_msg.edit_text(
            f"📤 <b>Uploading to Telegram</b>\n"
            f"┌ 📺 {title}\n"
            f"├ 🎬 Quality: {quality}\n"
            f"├ 💾 Size: {_format_size(file_size)}\n"
            f"└ 🔄 Starting upload...",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=cancel_markup_for(job_id))

        # Dump / Storage Channel and Upload Mode
        from bot.database import db
        dump_channel_id = await db.get_dump_channel() if db else None
        upload_mode = await db.get_upload_mode() if db else "video"

        post_style = await db.get_post_style() if db else "modern"
        # Build style-aware episode caption and quality buttons
        caption_text, markup_obj = await _build_episode_caption_and_markup(
            title=title, quality=quality, series_slug=series_slug, client=client, target_chat=target_upload_chat,
            language=language, post_style=post_style,
        )

        sent_msg = None
        dump_msg = None
        if dump_channel_id and target_upload_chat != dump_channel_id:
            try:
                await progress_msg.edit_text(
                    f"📤 <b>Uploading to Dump Channel</b>\n"
                    f"┌ 📺 {title}\n"
                    f"├ 🎬 Quality: {quality} ({upload_mode.upper()})\n"
                    f"├ 💾 Size: {_format_size(file_size)}\n"
                    f"└ 🔄 Storing media in cache...",
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=cancel_markup_for(job_id))
                if upload_mode == "document":
                    dump_msg = await client.send_document(
                        chat_id=dump_channel_id,
                        document=output_path,
                        thumb=thumb_path,
                        file_name=filename,
                        caption=f"📺 {title} [{quality}] #dump",
                        progress=_upload_progress,
                        force_document=True,
                    )
                else:
                    try:
                        dump_msg = await client.send_video(
                            chat_id=dump_channel_id,
                            video=output_path,
                            thumb=thumb_path,
                            file_name=filename,
                            caption=f"📺 {title} [{quality}] #dump",
                            duration=vid_duration,
                            width=vid_width,
                            height=vid_height,
                            supports_streaming=True,
                            progress=_upload_progress,
                        )
                    except Exception:
                        dump_msg = await client.send_document(
                            chat_id=dump_channel_id,
                            document=output_path,
                            thumb=thumb_path,
                            file_name=filename,
                            caption=f"📺 {title} [{quality}] #dump",
                            progress=_upload_progress,
                        )
                if dump_msg:
                    fid = dump_msg.video.file_id if dump_msg.video else (dump_msg.document.file_id if dump_msg.document else None)
                    if fid:
                        if upload_mode == "document":
                            sent_msg = await client.send_document(
                                chat_id=target_upload_chat,
                                document=fid,
                                caption=caption_text,
                                reply_markup=markup_obj,
                                force_document=True,
                            )
                        else:
                            try:
                                sent_msg = await client.send_video(
                                    chat_id=target_upload_chat,
                                    video=fid,
                                    caption=caption_text,
                                    reply_markup=markup_obj,
                                    duration=vid_duration,
                                    width=vid_width,
                                    height=vid_height,
                                    supports_streaming=True,
                                )
                            except Exception:
                                sent_msg = await client.send_document(
                                    chat_id=target_upload_chat,
                                    document=fid,
                                    caption=caption_text,
                                    reply_markup=markup_obj,
                                )
            except Exception as de:
                log.warning("Dump channel upload failed, falling back to direct upload: %s", de)

        if not sent_msg:
            if upload_mode == "document":
                try:
                    sent_msg = await client.send_document(
                        chat_id=target_upload_chat,
                        document=output_path,
                        thumb=thumb_path,
                        file_name=filename,
                        caption=caption_text,
                        reply_markup=markup_obj,
                        progress=_upload_progress,
                        force_document=True,
                    )
                except Exception as te:
                    if thumb_path:
                        log.warning("Upload with thumb failed, retrying without thumb: %s", te)
                        sent_msg = await client.send_document(
                            chat_id=target_upload_chat,
                            document=output_path,
                            file_name=filename,
                            caption=caption_text,
                            reply_markup=markup_obj,
                            progress=_upload_progress,
                            force_document=True,
                        )
                    else:
                        raise
            else:
                try:
                    sent_msg = await client.send_video(
                        chat_id=target_upload_chat,
                        video=output_path,
                        thumb=thumb_path,
                        file_name=filename,
                        caption=caption_text,
                        reply_markup=markup_obj,
                        duration=vid_duration,
                        width=vid_width,
                        height=vid_height,
                        supports_streaming=True,
                        progress=_upload_progress,
                    )
                except Exception:
                    sent_msg = await client.send_document(
                        chat_id=target_upload_chat,
                        document=output_path,
                        thumb=thumb_path,
                        file_name=filename,
                        caption=caption_text,
                        reply_markup=markup_obj,
                        progress=_upload_progress,
                    )

        user_file_msg = None
        # If uploaded somewhere other than the user's own chat (mapped/main
        # channel), send the file to the user's PM too.
        if target_upload_chat and chat_id != target_upload_chat and chat_id > 0:
            try:
                fid = sent_msg.video.file_id if sent_msg.video else (sent_msg.document.file_id if sent_msg.document else None)
                if fid:
                    if upload_mode == "document":
                        user_file_msg = await client.send_document(
                            chat_id=chat_id,
                            document=fid,
                            caption=f"📺 {title} [{quality}]",
                            force_document=True,
                        )
                    else:
                        try:
                            user_file_msg = await client.send_video(
                                chat_id=chat_id,
                                video=fid,
                                caption=f"📺 {title} [{quality}]",
                                duration=vid_duration,
                                width=vid_width,
                                height=vid_height,
                                supports_streaming=True,
                            )
                        except Exception:
                            user_file_msg = await client.send_document(
                                chat_id=chat_id,
                                document=fid,
                                caption=f"📺 {title} [{quality}]",
                            )
            except Exception as ue:
                log.warning("Forward/send to user chat %d failed: %s", chat_id, ue)
        elif target_upload_chat == chat_id and chat_id > 0:
            user_file_msg = sent_msg

        # Auto-delete scheduling if delivered in user PM
        if user_file_msg and chat_id > 0:
            try:
                from bot.auto_delete import auto_delete_service
                from utils.helpers import encode_file_param
                bot_user = getattr(client, "me", None)
                bname = bot_user.username if bot_user else ""
                m_ep = re.search(r"S(\d+)E(\d+)", title, re.IGNORECASE)
                ep_k = f"S{int(m_ep.group(1)):01d}E{int(m_ep.group(2)):02d}" if m_ep else ("movie" if is_movie else "all")
                q_slug = quality.lower().replace(" ", "")
                sec_param = encode_file_param(f"get_{series_slug}_{q_slug}_{ep_k}")
                # V3 #15: recovery link must hit the quality's worker, not
                # always the main bot (users were bounced to main here).
                link_bot = _worker_username_for_quality(quality, bname) if bname else ""
                get_link = f"https://t.me/{link_bot}?start={sec_param}" if link_bot else ""
                await auto_delete_service.schedule_deletion(
                    client=client,
                    chat_id=chat_id,
                    message_id=user_file_msg.id,
                    get_file_link=get_link,
                    file_title=title,
                )

                # Send auto-delete notification note (Issue #15)
                dlt_seconds = await db.get_dlt_time() if db else 0
                if dlt_seconds > 0:
                    dlt_mins = max(1, round(dlt_seconds / 60))
                    notice_text = (
                        f"<blockquote>‣ <b>ɴᴏᴛᴇ:</b> ᴛʜɪs ғɪʟᴇ ᴡɪʟʟ ʙᴇ ᴅᴇʟᴇᴛᴇᴅ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ɪɴ "
                        f"<b>{dlt_mins} mins</b>. ꜰᴏʀᴡᴀʀᴅ ɪᴛ ᴛᴏ ʏᴏᴜʀ sᴀᴠᴇᴅ ᴍᴇssᴀɢᴇs ɴᴏᴡ..!</blockquote>"
                    )
                    notice_msg = await client.send_message(chat_id=chat_id, text=notice_text, parse_mode=enums.ParseMode.HTML)
                    await auto_delete_service.schedule_deletion(
                        client=client,
                        chat_id=chat_id,
                        message_id=notice_msg.id,
                        custom_seconds=dlt_seconds,
                    )
            except Exception as ade:
                log.debug("Auto-delete scheduling in downloader failed: %s", ade)

        total_time = time.time() - overall_start

        # Deliver upload completion notification directly to Dump Channel (not owner DM)
        if dump_channel_id:
            try:
                await client.send_message(
                    chat_id=dump_channel_id,
                    text=(
                        f"✅ <b>Upload Complete Notice</b>\n"
                        f"┌ 📺 <b>Title:</b> {html.escape(title)}\n"
                        f"├ 🎬 <b>Quality:</b> <code>{quality}</code>\n"
                        f"├ 📦 <b>Size:</b> {_format_size(file_size)}\n"
                        f"├ ⏱ <b>Total Time:</b> {_format_time(total_time)}\n"
                        f"└ 📁 <b>Mode:</b> <code>{upload_mode.upper()}</code>"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception as ce:
                log.warning("Could not send dump channel completion notice: %s", ce)

        await progress_msg.edit_text(
            _done_text(title, quality, file_size, total_time),
            parse_mode=enums.ParseMode.HTML)
        return True, sent_msg

    except Exception as e:
        log.exception("Download/upload error for %s", title)
        from bot.database import db
        if db:
            try:
                await db.log_download_failure(
                    title=title, quality=quality, source=referer or "stream",
                    error=str(e), user_id=chat_id,
                )
            except Exception:
                pass
        try:
            await progress_msg.edit_text(
                f"❌ <b>Error</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 {str(e)[:200]}",
                parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return False, None
    finally:
        # Guarantee deletion of all tracked temporary files (video, thumbs, generated files)
        for tf in tracked_temp_files:
            try:
                if tf and os.path.exists(tf):
                    os.remove(tf)
            except Exception:
                pass
        try:
            stem = Path(output_path).stem
            for f in _TEMP_BASE.glob(f"*{glob_escape(stem)}*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
        except Exception:
            pass
        download_job_manager.remove_job(job_id)


def cleanup_vps_temp_files(max_age_seconds: int = 1800) -> int:
    """
    Clean up orphaned temporary files and directories from VPS storage (Issue #9, V2 #1).
    Removes downloaded media, partial chunks, and generated thumbnails older than max_age_seconds.
    V2 #1: never deletes files belonging to actively running download jobs.
    Returns the count of cleaned items.
    """
    now = time.time()
    cleaned_count = 0
    # V2 #1: collect in-use paths from active jobs so the sweeper can't
    # delete a file whose mtime merely looks stale mid-download.
    active_paths: set[str] = set()
    try:
        for job in list(download_job_manager._jobs.values()):
            active_paths.update(job.temp_files)
    except Exception:
        pass
    try:
        if _TEMP_BASE.exists():
            for item in _TEMP_BASE.iterdir():
                try:
                    resolved = str(item.resolve())
                    if resolved in active_paths or str(item) in active_paths:
                        continue
                    # Also skip any re_* job temp dirs still tracked
                    if item.is_dir() and item.name.startswith("re_"):
                        dir_str = str(item)
                        if any(dir_str in p or p.startswith(dir_str) for p in active_paths):
                            continue
                    mtime = item.stat().st_mtime
                    if now - mtime > max_age_seconds:
                        if item.is_file() or item.is_symlink():
                            item.unlink(missing_ok=True)
                            cleaned_count += 1
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                            cleaned_count += 1
                except Exception:
                    pass
    except Exception as e:
        log.warning("VPS temp file cleanup error: %s", e)
    if cleaned_count > 0:
        log.info("VPS Cleanup: Purged %d stale temporary items from %s", cleaned_count, _TEMP_BASE)
    return cleaned_count
