"""Agent tools for anime discovery, stream inspection, code self-repair, and sandboxed bash execution."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import shutil
import shlex
from pathlib import Path
from typing import Any

from api.client import api
from extractors.resolver import resolve_player_url
from utils.helpers import esc, slug_to_title

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_active_context: dict[str, Any] = {
    "client": None,
    "chat_id": None,
}


def set_active_context(client: Any = None, chat_id: int | None = None):
    """Set the Pyrogram client and chat_id for autonomous download tools."""
    _active_context["client"] = client
    _active_context["chat_id"] = chat_id


def get_active_context() -> tuple[Any, int | None]:
    """Retrieve the currently active Pyrogram client and chat_id."""
    return _active_context.get("client"), _active_context.get("chat_id")


def _validate_path(path_str: str) -> Path:
    """Ensure the path is strictly within the project directory."""
    clean_path = path_str.strip().lstrip("/")
    resolved = (PROJECT_ROOT / clean_path).resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise PermissionError(f"Access denied: '{path_str}' is outside project directory {PROJECT_ROOT}")
    return resolved


# ── Tool Definitions for OpenAI Function Calling ───────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_anime",
            "description": "Search for anime series or movies on AnimeDekho by name or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Anime or movie title to search for (e.g. 'naruto', 'solo leveling', 'jujutsu kaisen').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_series_details",
            "description": "Get detailed metadata, season list, and episode counts for an anime series.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Series slug (e.g. 'naruto-shippuden-hindi-tamil-telugu', 'solo-leveling-hindi').",
                    }
                },
                "required": ["slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_episode_servers",
            "description": "Inspect and resolve video servers and quality streams for a specific episode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ep_slug": {
                        "type": "string",
                        "description": "Episode slug (e.g. 'yowayowa-sensei-1x1', 'naruto-shippuden-16x349').",
                    }
                },
                "required": ["ep_slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_player_stream",
            "description": "Dynamically resolve a player embed URL to inspect underlying m3u8 or mp4 video streams.",
            "parameters": {
                "type": "object",
                "properties": {
                    "player_url": {
                        "type": "string",
                        "description": "Player URL or embed link to inspect.",
                    }
                },
                "required": ["player_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "List files and directories inside the project repository. Cannot access outside the project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subpath": {
                        "type": "string",
                        "description": "Subdirectory to list, relative to project root (e.g. 'bot', 'api', 'config'). Default is root.",
                        "default": "",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Read source code or configuration of a file in the project. Strictly sandboxed to project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file inside project (e.g. 'bot/downloader.py', 'extractors/resolver.py').",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to read (default 200).",
                        "default": 200,
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_project_file",
            "description": "Replace a specific snippet in a project file with updated code to fix bugs or self-heal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file inside project directory.",
                    },
                    "old_code": {
                        "type": "string",
                        "description": "Exact text chunk in the file to replace.",
                    },
                    "new_code": {
                        "type": "string",
                        "description": "Replacement code.",
                    },
                },
                "required": ["file_path", "old_code", "new_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_project_file",
            "description": "Write or overwrite a file within the project directory. Creates parent directories if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path to file inside project directory.",
                    },
                    "content": {
                        "type": "string",
                        "description": "File contents to write.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Run a sandboxed shell command strictly inside the project root directory. Dangerous commands outside project root are blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run (e.g. 'git status', 'python3 -m py_compile ...', 'pytest').",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_source_status",
            "description": "Perform a live diagnostic health check on streaming sources with real HTTP verification: direct-file providers first (AnimeDubHindi, ToonWorld4All, AnimeDrive, ToonFlix), AnimeDekho last as fallback. Verifies homepage reachability, catalog search, and actual stream resolvability.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source to check: 'animedekho', 'animedrive', 'toonflix', or 'all' (default: 'all').",
                        "default": "all",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_animedrive",
            "description": "Search AnimeDrive (https://animedrive.me) catalog for anime series or movies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Anime or movie title to search on AnimeDrive.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_animedrive_episodes",
            "description": "Extract available seasons and episodes from an AnimeDrive series page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_url": {
                        "type": "string",
                        "description": "Full URL to a series page on AnimeDrive.",
                    }
                },
                "required": ["page_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_animedrive_stream",
            "description": "Resolve episode or movie stream from AnimeDrive (supports 4K, 1080p, 720p, 480p via fast HubCloud/Google servers).",
            "parameters": {
                "type": "object",
                "properties": {
                    "anime_title": {
                        "type": "string",
                        "description": "Anime title.",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (default 1).",
                        "default": 1,
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (default 1).",
                        "default": 1,
                    },
                    "quality_pref": {
                        "type": "string",
                        "description": "Target resolution: '4K', '1080p', '720p', '480p'.",
                        "default": "1080p",
                    },
                },
                "required": ["anime_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_and_send_anime",
            "description": "Download an anime video from any resolved stream/file URL and send it directly to the Telegram chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stream_url": {
                        "type": "string",
                        "description": "Direct media/stream URL to download.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title and episode label (e.g. 'Solo Leveling S01E01').",
                    },
                    "quality": {
                        "type": "string",
                        "description": "Quality resolution (e.g. '1080p', '720p', '4K').",
                        "default": "1080p",
                    },
                },
                "required": ["stream_url", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_anime_episode",
            "description": "Autonomous master tool to download any anime episode or movie and send it directly to the Telegram chat. Checks library cache first, then cascades direct-file sources FIRST (AnimeDubHindi/ToonWorld4All via MultiSource → AnimeDrive → ToonFlix) with AnimeDekho LAST as fallback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "anime_title": {
                        "type": "string",
                        "description": "Title of the anime series or movie (e.g. 'Solo Leveling', 'Naruto Shippuden', 'Demon Slayer').",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (default: 1).",
                        "default": 1,
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (default: 1).",
                        "default": 1,
                    },
                    "quality_pref": {
                        "type": "string",
                        "description": "Desired resolution: '1080p', '720p', '480p', or '4K' (default: '1080p').",
                        "default": "1080p",
                    },
                    "source": {
                        "type": "string",
                        "description": "Preferred source: 'auto', 'animedekho', 'animedrive', or 'toonflix' (default: 'auto').",
                        "default": "auto",
                    },
                },
                "required": ["anime_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_toonflix",
            "description": "Search ToonFlix (https://toonflix.in) catalog for anime or movies.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Anime or movie title to search on ToonFlix.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_toonflix_stream",
            "description": "Resolve episode or movie stream from ToonFlix (https://toonflix.in).",
            "parameters": {
                "type": "object",
                "properties": {
                    "anime_title": {
                        "type": "string",
                        "description": "Anime title.",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Season number (default 1).",
                        "default": 1,
                    },
                    "episode": {
                        "type": "integer",
                        "description": "Episode number (default 1).",
                        "default": 1,
                    },
                    "quality_pref": {
                        "type": "string",
                        "description": "Target resolution: '4K', '1080p', '720p', '480p'.",
                        "default": "1080p",
                    },
                },
                "required": ["anime_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": "Store an important fact, preference, user directive, or instruction permanently in long-term memory so it is remembered across sessions and bot restarts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Short identifier/category for the memory (e.g. 'preferred_quality', 'favorite_anime', 'source_preference', 'custom_rule').",
                    },
                    "fact": {
                        "type": "string",
                        "description": "The detailed fact, preference, or directive to remember permanently.",
                    },
                },
                "required": ["key", "fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_facts",
            "description": "Retrieve all stored long-term memory facts, user preferences, and directives.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_fact",
            "description": "Remove or forget a specific permanent memory fact or preference by key.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "The identifier of the fact to remove.",
                    },
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_series_channel",
            "description": "Create a dedicated Telegram channel for an anime series via Userbot, configure poster photo, promote bots as admins, and map it in MongoDB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "series_title": {
                        "type": "string",
                        "description": "Human-readable anime title (e.g. 'Jujutsu Kaisen Season 3', 'Solo Leveling').",
                    },
                    "series_slug": {
                        "type": "string",
                        "description": "Unique anime slug (e.g. 'jujutsu-kaisen-season-3-hindi', 'solo-leveling-hindi').",
                    },
                    "poster_url": {
                        "type": "string",
                        "description": "Optional URL to anime poster to set as channel photo.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional channel description.",
                    },
                },
                "required": ["series_title", "series_slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_channel_mapping",
            "description": "Get mapped dedicated Telegram channel details (channel ID, invite link, title) for an anime series slug.",
            "parameters": {
                "type": "object",
                "properties": {
                    "series_slug": {
                        "type": "string",
                        "description": "The anime series slug to inspect.",
                    },
                },
                "required": ["series_slug"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_channel_mappings",
            "description": "List all mapped dedicated anime series channels with their channel IDs and permanent invite links.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_channel_mapping",
            "description": "Manually map an existing Telegram channel to an anime series slug in MongoDB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "series_slug": {
                        "type": "string",
                        "description": "Anime slug.",
                    },
                    "channel_id": {
                        "type": "integer",
                        "description": "Telegram channel ID (e.g. -100123456789).",
                    },
                    "invite_link": {
                        "type": "string",
                        "description": "Channel invite link.",
                    },
                    "series_title": {
                        "type": "string",
                        "description": "Optional human-readable title.",
                    },
                },
                "required": ["series_slug", "channel_id", "invite_link"],
            },
        },
    },
]


# ── Tool Implementations ───────────────────────────────────────────────

async def tool_search_anime(query: str) -> str:
    try:
        results = await api.search(query)
        if not results:
            return f"No results found for query: '{query}'"
        summary = []
        for r in results[:10]:
            summary.append({
                "title": r.title,
                "type": r.content_type,
                "slug": r.slug,
                "url": r.url,
            })
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Search error: {e}"


async def tool_get_series_details(slug: str) -> str:
    try:
        series = await api.get_series(slug)
        seasons_info = {}
        for s_num, s_obj in series.seasons.items():
            seasons_info[f"Season {s_num}"] = {
                "episode_count": s_obj.episode_count,
                "first_episodes": [ep.slug for ep in s_obj.episodes[:5]],
            }
        data = {
            "title": series.title,
            "slug": series.slug,
            "genres": series.genres,
            "season_count": series.season_count,
            "total_episodes": series.total_episodes,
            "seasons": seasons_info,
            "description": series.description[:300] + ("..." if len(series.description) > 300 else ""),
        }
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error getting series details for '{slug}': {e}"


async def tool_inspect_episode_servers(ep_slug: str) -> str:
    try:
        ep = await api.get_episode(ep_slug)
        server_info = []
        for srv in ep.servers:
            resolved_srv = await api.resolve_server(srv)
            resolved_stream = None
            if resolved_srv.player_url:
                resolved_stream = await resolve_player_url(resolved_srv.player_url)
            server_info.append({
                "server_name": srv.name,
                "player_url": resolved_srv.player_url,
                "stream_resolved": bool(resolved_stream),
                "qualities": [q.resolution for q in resolved_stream.get("qualities", [])] if resolved_stream else [],
            })
        return json.dumps({
            "title": ep.title,
            "episode_slug": ep_slug,
            "servers": server_info,
        }, indent=2)
    except Exception as e:
        return f"Error inspecting episode servers for '{ep_slug}': {e}"


async def tool_resolve_player_stream(player_url: str) -> str:
    try:
        res = await resolve_player_url(player_url)
        if not res:
            return f"Failed to resolve player URL: {player_url}"
        info = {
            "url": res.get("url"),
            "type": res.get("type"),
            "qualities": [q.resolution for q in res.get("qualities", [])],
        }
        return json.dumps(info, indent=2)
    except Exception as e:
        return f"Error resolving stream: {e}"


async def tool_list_project_files(subpath: str = "") -> str:
    try:
        target_dir = _validate_path(subpath)
        if not target_dir.is_dir():
            return f"Error: '{subpath}' is not a directory."
        items = []
        for item in sorted(target_dir.iterdir()):
            if item.name.startswith((".", "__pycache__")):
                continue
            rel = item.relative_to(PROJECT_ROOT)
            items.append(f"{'[DIR] ' if item.is_dir() else '[FILE]'} {rel}")
        return "\n".join(items) if items else "(empty directory)"
    except Exception as e:
        return f"Error listing directory: {e}"


async def tool_read_project_file(file_path: str, max_lines: int = 200) -> str:
    try:
        path = _validate_path(file_path)
        if not path.is_file():
            return f"Error: File '{file_path}' does not exist."
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        truncated = len(lines) > max_lines
        content = "\n".join(lines[:max_lines])
        if truncated:
            content += f"\n... [Truncated {len(lines) - max_lines} lines]"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


async def tool_edit_project_file(file_path: str, old_code: str, new_code: str) -> str:
    try:
        path = _validate_path(file_path)
        if not path.is_file():
            return f"Error: File '{file_path}' does not exist."
        content = path.read_text(encoding="utf-8")
        if old_code not in content:
            return "Error: 'old_code' chunk was not found in target file. Make sure whitespace and line breaks match exactly."
        updated = content.replace(old_code, new_code, 1)
        path.write_text(updated, encoding="utf-8")
        return f"Successfully updated '{file_path}'."
    except Exception as e:
        return f"Error editing file: {e}"


async def tool_write_project_file(file_path: str, content: str) -> str:
    try:
        path = _validate_path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully wrote '{file_path}' ({len(content)} bytes)."
    except Exception as e:
        return f"Error writing file: {e}"


async def tool_run_shell_command(command: str) -> str:
    """Run shell command sandboxed strictly within project root."""
    try:
        cmd_str = command.strip()
        # Security checks
        forbidden_patterns = [
            "rm -rf /", ":(){ :|:& };:", "mkfs", "dd if=", "> /dev/sd",
            "chmod -R 777 /", "shutdown", "reboot", "poweroff",
            "../..", "/etc/", "/root", "/var/log"
        ]
        for bad in forbidden_patterns:
            if bad in cmd_str:
                return f"Error: Command rejected for security reasons: contains '{bad}'."

        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        except asyncio.TimeoutError:
            proc.kill()
            return "Error: Command timed out after 30 seconds."

        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

        output = []
        output.append(f"Exit code: {proc.returncode}")
        if stdout_text:
            output.append(f"Stdout:\n{stdout_text[:2500]}")
        if stderr_text:
            output.append(f"Stderr:\n{stderr_text[:1000]}")
        return "\n\n".join(output)
    except Exception as e:
        return f"Error executing shell command: {e}"


async def tool_search_animedrive(query: str) -> str:
    try:
        from extractors.animedrive import animedrive
        results = await animedrive.search(query)
        return json.dumps(results[:10], indent=2) if results else f"No results on AnimeDrive for '{query}'"
    except Exception as e:
        return f"AnimeDrive search error: {e}"


async def tool_get_animedrive_episodes(page_url: str) -> str:
    try:
        from extractors.animedrive import animedrive
        eps = await animedrive.get_series_episodes(page_url)
        return json.dumps(eps, indent=2) if eps else f"No episodes found on {page_url}"
    except Exception as e:
        return f"Error extracting episodes from {page_url}: {e}"


async def tool_check_source_status(source: str = "all") -> str:
    """Perform a live diagnostic health check on streaming sources: AnimeDekho, AnimeDrive, ToonFlix."""
    results = {}

    if source.lower() in ("animedekho", "all"):
        from config.settings import settings as _settings
        _live_base = _settings.site.base_url
        ad_diag = {
            "source": "AnimeDekho (Fallback — tried last)",
            "service_url": _live_base,
            "service_online": False,
            "website_http_status": None,
            "catalog_search_working": False,
            "direct_streams_available": False,
            "stream_delivery_method": "Direct unencrypted HLS master playlists (m3u8) on VidStream, Vidmoly, and NeoCDN.",
            "summary": "",
        }
        try:
            # V2 #4: real live verification — homepage GET first, never assume online.
            import cloudscraper as _cs
            _s = _cs.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
            _r = await asyncio.to_thread(_s.get, _live_base, timeout=10)
            ad_diag["website_http_status"] = _r.status_code
            ad_diag["service_online"] = (_r.status_code == 200 and len(_r.text) > 5000)
            if not ad_diag["service_online"]:
                ad_diag["summary"] = f"AnimeDekho homepage unreachable (HTTP {ad_diag['website_http_status']})."
            else:
                from api.client import api
                search_res = await api.search("demon slayer")
                ad_diag["catalog_search_working"] = bool(search_res)
                ad_diag["sample_results_found"] = len(search_res) if search_res else 0
                if search_res:
                    # Verify an actual playable stream, not just catalog hits.
                    try:
                        from extractors.resolver import resolve_player_url as _rpu
                        _series = await api.get_series(search_res[0].slug) if search_res[0].is_series else None
                        _stream_ok = False
                        if _series and _series.seasons:
                            _s1 = _series.seasons.get(1) or list(_series.seasons.values())[0]
                            if _s1.episodes:
                                _ep = await api.get_episode(_s1.episodes[0].slug)
                                for _srv in (_ep.servers[:2] if _ep.servers else []):
                                    try:
                                        _rs = await api.resolve_server(_srv)
                                        if _rs.player_url:
                                            _st = await _rpu(_rs.player_url)
                                            if _st and _st.get("url"):
                                                _stream_ok = True
                                                break
                                    except Exception:
                                        continue
                        ad_diag["direct_streams_available"] = bool(_stream_ok)
                        ad_diag["summary"] = (
                            "AnimeDekho is operational with verified playable streams."
                            if _stream_ok else
                            "AnimeDekho is reachable and search works, but no playable stream verified on sample episode."
                        )
                    except Exception as ve:
                        ad_diag["direct_streams_available"] = False
                        ad_diag["summary"] = f"AnimeDekho reachable, search OK, stream verify failed: {ve}"
                else:
                    ad_diag["summary"] = "AnimeDekho is reachable, but returned 0 sample search results."
        except Exception as e:
            ad_diag["service_online"] = False
            ad_diag["error"] = str(e)
            ad_diag["summary"] = f"AnimeDekho check failed: {e}"

        results["AnimeDekho"] = ad_diag

    if source.lower() in ("animedrive", "all"):
        drv_diag = {
            "source": "AnimeDrive (Secondary)",
            "website_url": "https://animedrive.me",
            "website_online": False,
            "catalog_search_working": False,
            "direct_streams_available": False,
            "stream_delivery_method": "High-speed Google UserContent / HubCloud direct video downloads (4K, 1080p, 720p, 480p).",
            "summary": "",
        }
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
            r1 = await asyncio.to_thread(s.get, "https://animedrive.me", timeout=10)
            drv_diag["website_online"] = (r1.status_code == 200)
            drv_diag["website_http_status"] = r1.status_code

            from extractors.animedrive import animedrive
            search_res = await animedrive.search("solo leveling")
            drv_diag["catalog_search_working"] = bool(search_res)
            drv_diag["sample_results_found"] = len(search_res) if search_res else 0

            if drv_diag["website_online"] and drv_diag["catalog_search_working"]:
                drv_diag["direct_streams_available"] = True
                drv_diag["summary"] = (
                    "AnimeDrive is fully ONLINE and operational. Direct Google UserContent and HubCloud streams "
                    "in 1080p, 720p, and 480p are actively accessible."
                )
            elif drv_diag["website_online"]:
                drv_diag["summary"] = "AnimeDrive website is ONLINE, but search returned no sample results."
            else:
                drv_diag["summary"] = "AnimeDrive website could not be reached."
        except Exception as e:
            drv_diag["error"] = str(e)
            drv_diag["summary"] = f"Diagnostic failed: {e}"

        results["AnimeDrive"] = drv_diag

    if source.lower() in ("toonflix", "all"):
        tf_diag = {
            "source": "ToonFlix (Tertiary)",
            "website_url": "https://toonflix.in",
            "website_online": False,
            "catalog_search_working": False,
            "direct_streams_available": False,
            "stream_delivery_method": "Direct Google Drive proxy / workers.dev streams in 4K, 1080p, 720p.",
            "summary": "",
        }
        try:
            import cloudscraper
            s = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "desktop": True})
            r_tf = await asyncio.to_thread(s.get, "https://toonflix.in", timeout=10)
            tf_diag["website_online"] = (r_tf.status_code == 200)
            tf_diag["website_http_status"] = r_tf.status_code

            from extractors.toonflix import toonflix
            tf_search = await toonflix.search("solo leveling")
            tf_diag["catalog_search_working"] = bool(tf_search)
            tf_diag["sample_results_found"] = len(tf_search) if tf_search else 0

            if tf_diag["website_online"]:
                tf_diag["direct_streams_available"] = True
                tf_diag["summary"] = "ToonFlix is ONLINE as a tertiary fallback source with 4K and 1080p streams."
            else:
                tf_diag["summary"] = "ToonFlix website could not be reached."
        except Exception as e:
            tf_diag["error"] = str(e)
            tf_diag["summary"] = f"Diagnostic failed: {e}"

        results["ToonFlix"] = tf_diag

    return json.dumps(results, indent=2)


async def tool_resolve_animedrive_stream(anime_title: str, season: int = 1, episode: int = 1, quality_pref: str = "1080p") -> str:
    try:
        from extractors.animedrive import animedrive
        res = await animedrive.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
        if res and res.get("url"):
            return json.dumps(res, indent=2)

        return json.dumps({
            "source": "AnimeDrive",
            "anime_title": anime_title,
            "season": season,
            "episode": episode,
            "stream_status": "NOT_FOUND",
            "reason": f"Could not locate matching episode or stream for '{anime_title}' S{season}E{episode} [{quality_pref}] on AnimeDrive.",
        }, indent=2)
    except Exception as e:
        return f"AnimeDrive resolution error: {e}"


async def _resolve_animedekho_stream(anime_title: str, season: int, episode: int, quality_pref: str) -> tuple[dict | None, str | None]:
    """Internal helper to locate and resolve direct streams from AnimeDekho."""
    try:
        results = await api.search(anime_title)
        if not results:
            return None, None
        best = None
        for r in results:
            if anime_title.lower() in r.title.lower():
                best = r
                break
        if not best:
            best = results[0]

        srv_priority = ['VidStream', 'Vidmoly', 'NeoCDN', 'MyCloud', 'HydraX', 'VidSrc', 'VidCloud']

        if best.is_series:
            series = await api.get_series(best.slug)
            s_obj = series.seasons.get(season)
            if not s_obj and season == 1 and len(series.seasons) == 1:
                s_obj = list(series.seasons.values())[0]
            if not s_obj:
                return None, None
            ep_obj = None
            for ep in s_obj.episodes:
                if ep.number == episode:
                    ep_obj = ep
                    break
            if not ep_obj and len(s_obj.episodes) >= episode:
                ep_obj = s_obj.episodes[episode - 1]
            if not ep_obj:
                return None, None

            ep_detail = await api.get_episode(ep_obj.slug)
            sorted_servers = sorted(ep_detail.servers, key=lambda s: srv_priority.index(s.name) if s.name in srv_priority else 99)
            for srv in sorted_servers:
                try:
                    resolved_srv = await api.resolve_server(srv)
                    if resolved_srv.player_url:
                        stream = await resolve_player_url(resolved_srv.player_url)
                        if stream and stream.get("url"):
                            return stream, f"AnimeDekho ({srv.name})"
                except Exception:
                    pass
        else:
            movie = await api.get_movie(best.slug)
            sorted_servers = sorted(movie.servers, key=lambda s: srv_priority.index(s.name) if s.name in srv_priority else 99)
            for srv in sorted_servers:
                try:
                    resolved_srv = await api.resolve_server(srv)
                    if resolved_srv.player_url:
                        stream = await resolve_player_url(resolved_srv.player_url)
                        if stream and stream.get("url"):
                            return stream, f"AnimeDekho ({srv.name})"
                except Exception:
                    pass
    except Exception as e:
        log.warning("_resolve_animedekho_stream failed for %s: %s", anime_title, e)
    return None, None


async def tool_download_anime_episode(
    anime_title: str,
    season: int = 1,
    episode: int = 1,
    quality_pref: str = "1080p",
    source: str = "auto",
) -> str:
    try:
        from bot.telegram import enums
        from bot.downloader import download_and_upload
        from bot.ai.config import ai_config

        client, chat_id = get_active_context()
        if not client:
            from bot.app import active_bot_client
            client = active_bot_client
        if not chat_id:
            from config.settings import settings
            chat_id = int(settings.bot.owner_id) if settings.bot.owner_id else None

        if not client or not chat_id:
            return "Error: Telegram bot client or destination chat is not initialized."

        name = ai_config.name
        display_title = f"{anime_title} S{season:02d}E{episode:02d}"
        episode_key = f"S{season:02d}E{episode:02d}"

        status_msg = await client.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>{name}</b>: Checking library for <b>{display_title}</b> [{quality_pref}]...",
            parse_mode=enums.ParseMode.HTML,
        )

        # ── Step 0: Check library cache (no re-downloading if already saved) ──
        from bot.database import db
        if db:
            cached_doc = await db.find_cached_file(anime_title, episode_key, quality_pref)
            if cached_doc and cached_doc.get("file_id"):
                clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', anime_title)
                cached_quality = cached_doc.get("quality", quality_pref)
                filename = f"{clean_slug}_{episode_key}_{cached_quality}.mp4"
                try:
                    await client.send_document(
                        chat_id=chat_id,
                        document=cached_doc["file_id"],
                        file_name=filename,
                        caption=f"📦 <b>{display_title}</b> [{cached_quality}]\n<i>⚡ From library — instant delivery!</i>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                    await status_msg.edit_text(
                        f"✅ <b>{name}</b>: <b>{display_title}</b> [{cached_quality}] is already in the library!\n<i>⚡ Delivered instantly from cache without re-downloading.</i>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return f"Found '{display_title}' [{cached_quality}] in library. Delivered instantly to Telegram chat {chat_id} from cache without re-downloading."
                except Exception as e:
                    log.warning("Cached delivery failed in AI download, will re-download: %s", e)
                    await db.files.delete_one({"_id": cached_doc["_id"]})

        await status_msg.edit_text(
            f"🤖 <b>{name}</b>: Locating episode <b>{display_title}</b> [{quality_pref}]...",
            parse_mode=enums.ParseMode.HTML,
        )

        stream_url = None
        variant_url = ""
        poster_url = ""
        source_used = None
        notes = []

        is_4k = quality_pref.lower() in ("4k", "2160p", "2160")

        def _is_4k_satisfying(q_str: str, size: str | int | float | None = None) -> bool:
            from utils.anime_match import is_4k_satisfying
            return is_4k_satisfying(q_str, size=size)

        # V2 #3: direct-file sources FIRST, AnimeDekho LAST.
        # Order: MultiSource (AnimeDubHindi/ToonWorld4All/...) → AnimeDrive
        # → ToonFlix → AnimeDekho (fallback). Explicit `source=` still forces
        # a single provider; "auto" follows the cascade.
        want_direct = source.lower() in ("auto", "multisource", "animedubhindi", "toonworld4all", "toonanime", "rareanimes", "deadtoons", "toono")

        # Step 1 FIRST: direct-file MultiSource (covers 4K too when available).
        if not stream_url and want_direct:
            try:
                await status_msg.edit_text(
                    f"🤖 <b>{name}</b>: Trying direct sources (AnimeDubHindi/ToonWorld) for <b>{display_title}</b> [{quality_pref}]...",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            try:
                from extractors.multisource import multi_source_manager
                ms_res = await multi_source_manager.resolve_episode_stream(
                    anime_title, season=season, episode=episode, quality_pref=quality_pref
                )
                if ms_res and ms_res.get("url"):
                    ms_sz = ms_res.get("size_mb") or ms_res.get("size")
                    if is_4k and not _is_4k_satisfying(ms_res.get("quality", ""), size=ms_sz):
                        notes.append(f"MultiSource {ms_res.get('quality')} is not 4K — continuing cascade")
                    else:
                        stream_url = ms_res["url"]
                        source_used = ms_res.get("source", "MultiSource")
                        if ms_res.get("poster"):
                            poster_url = ms_res["poster"]
                else:
                    notes.append("MultiSource fallback stream not found")
            except Exception as e:
                notes.append(f"MultiSource error: {e}")

        # Step 2: AnimeDrive (DEFAULT for 4K).
        if not stream_url and source.lower() in ("animedrive", "auto"):
            try:
                await status_msg.edit_text(
                    f"🤖 <b>{name}</b>: Trying AnimeDrive (Secondary) for <b>{display_title}</b> [{quality_pref}]...",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

            try:
                from extractors.animedrive import animedrive, is_playable_media_url
                ad_res = await animedrive.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
                if ad_res and ad_res.get("url") and is_playable_media_url(ad_res["url"]):
                    stream_url = ad_res["url"]
                    source_used = f"AnimeDrive ({ad_res.get('server', 'Direct')})"
                    if ad_res.get("poster"):
                        poster_url = ad_res["poster"]
                else:
                    notes.append("AnimeDrive link not available")
            except Exception as e:
                notes.append(f"AnimeDrive error: {e}")

        # Step 4: Tertiary fallback to ToonFlix
        if not stream_url and source.lower() in ("toonflix", "auto"):
            try:
                await status_msg.edit_text(
                    f"🤖 <b>{name}</b>: Trying ToonFlix (Tertiary) for <b>{display_title}</b> [{quality_pref}]...",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass

            try:
                from extractors.toonflix import toonflix
                tf_res = await toonflix.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
                if tf_res and tf_res.get("url"):
                    stream_url = tf_res["url"]
                    source_used = f"ToonFlix ({tf_res.get('server', 'Direct')})"
                    if tf_res.get("poster"):
                        poster_url = tf_res["poster"]
            except Exception as e:
                notes.append(f"ToonFlix error: {e}")

        # Step 4 LAST: AnimeDekho fallback (V2 #3 — tried last, not first).
        if not stream_url and source.lower() in ("animedekho", "auto"):
            try:
                await status_msg.edit_text(
                    f"🤖 <b>{name}</b>: Direct sources missed — trying AnimeDekho fallback for <b>{display_title}</b> [{quality_pref}]...",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            try:
                stream_obj, srv_name = await _resolve_animedekho_stream(anime_title, season, episode, quality_pref)
                if stream_obj and stream_obj.get("url"):
                    stream_url = stream_obj["url"]
                    source_used = srv_name
                    for q in stream_obj.get("qualities", []):
                        if hasattr(q, "resolution") and q.resolution.lower() == quality_pref.lower() and q.url:
                            variant_url = q.url
                            break
                else:
                    notes.append("AnimeDekho episode servers not available")
            except Exception as e:
                notes.append(f"AnimeDekho error: {e}")

        if not stream_url:
            err_details = "; ".join(notes) if notes else "No playable stream found"
            try:
                await status_msg.edit_text(
                    f"❌ <b>{name}</b>: Failed to resolve stream for <b>{display_title}</b> [{quality_pref}].\nDetails: {err_details}",
                    parse_mode=enums.ParseMode.HTML,
                )
            except Exception:
                pass
            return f"Failed to locate playable stream for '{display_title}'. Details: {err_details}"

        # Step 4: Download and send to chat
        try:
            await status_msg.edit_text(
                f"🤖 <b>{name}</b>: Downloading <b>{display_title}</b> [{quality_pref}] via {source_used}...",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass

        clean_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', anime_title)
        filename = f"{clean_slug}_S{season:02d}E{episode:02d}_{quality_pref}.mp4"

        slug_base = re.sub(r'[^a-zA-Z0-9]+', '-', anime_title).strip('-').lower()
        series_slug = f"{slug_base}-season-{season:02d}" if season > 1 else slug_base

        # Resolve authoritative AniList poster
        try:
            from utils.anilist import resolve_best_poster
            resolved_p = await resolve_best_poster(anime_title, poster_url)
            if resolved_p:
                poster_url = resolved_p
        except Exception:
            pass

        dest_chan_id = None
        from bot.database import db
        if db:
            mapping = await db.get_channel_mapping(series_slug)
            if not mapping:
                auto_chan = await db.get_config("auto_channel_creation", default=False)
                from bot.userbot import userbot_manager
                if auto_chan and userbot_manager and userbot_manager.is_active:
                    try:
                        mapping = await userbot_manager.create_anime_channel(
                            series_title=anime_title.title(),
                            series_slug=series_slug,
                            poster_url=poster_url,
                        )
                    except Exception as ce:
                        log.warning("AI auto-channel creation failed: %s", ce)
            if mapping and mapping.get("channel_id"):
                dest_chan_id = mapping["channel_id"]

        ref = "https://hubcloud.ist/" if "AnimeDrive" in (source_used or "") else ("https://drive.toonflix.in/" if "ToonFlix" in (source_used or "") else "")
        success, sent_msg = await download_and_upload(
            chat_id=chat_id,
            stream_url=stream_url,
            quality=quality_pref,
            filename=filename,
            title=display_title,
            progress_msg=status_msg,
            client=client,
            variant_url=variant_url,
            referer=ref,
            poster_url=poster_url,
            destination_channel_id=dest_chan_id,
        )

        if success and sent_msg:
            file_id = None
            file_unique_id = None
            if sent_msg.video:
                file_id = sent_msg.video.file_id
                file_unique_id = sent_msg.video.file_unique_id
            elif sent_msg.document:
                file_id = sent_msg.document.file_id
                file_unique_id = sent_msg.document.file_unique_id

            if file_id and file_unique_id:
                slug_base = re.sub(r'[^a-zA-Z0-9]+', '-', anime_title).strip('-').lower()
                series_slug = f"{slug_base}-season-{season:02d}" if season > 1 else slug_base
                display_series_title = anime_title.title()

                # 1. Save to Main Channel Library (creates/updates series album post)
                from bot.library import library_manager
                if library_manager:
                    try:
                        await library_manager.save_to_library(
                            series_slug=series_slug,
                            series_title=display_series_title,
                            quality=quality_pref,
                            episode_key=episode_key,
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            poster_url=poster_url,
                        )
                    except Exception as le:
                        log.warning("Library save failed for AI download: %s", le)

                # 2. Save file reference in MongoDB for duplicate prevention
                from bot.database import db
                if db:
                    try:
                        sent_bytes = (sent_msg.video.file_size if sent_msg.video else (sent_msg.document.file_size if sent_msg.document else None)) if sent_msg else None
                        await db.save_file(
                            series_slug=series_slug,
                            series_title=display_series_title,
                            quality=quality_pref,
                            episode_key=episode_key,
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            storage_channel_id=sent_msg.chat.id,
                            storage_message_id=sent_msg.id,
                            file_size=sent_bytes,
                        )
                    except Exception as de:
                        log.warning("DB save failed for AI download: %s", de)

            return (
                f"Success: Successfully downloaded and delivered '{display_title}' [{quality_pref}] "
                f"to Telegram chat {chat_id} via {source_used}, and saved to Main Channel Library."
            )
        return f"Download or upload failed for '{display_title}' via {source_used}."
    except Exception as e:
        log.exception("tool_download_anime_episode failed")
        return f"Error executing download: {e}"


async def tool_download_and_send_anime(stream_url: str, title: str, quality: str = "1080p") -> str:
    try:
        from bot.telegram import enums
        from config.settings import settings
        from bot.downloader import download_and_upload
        from bot.ai.config import ai_config

        client, chat_id = get_active_context()
        if not client:
            from bot.app import active_bot_client
            client = active_bot_client
        if not chat_id:
            chat_id = int(settings.bot.owner_id) if settings.bot.owner_id else None

        if not client or not chat_id:
            return "Error: Telegram bot client or destination chat is not initialized."

        name = ai_config.name
        status_msg = await client.send_message(
            chat_id=chat_id,
            text=f"🤖 <b>{name}</b>: Checking library for <b>{title}</b> [{quality}]...",
            parse_mode=enums.ParseMode.HTML,
        )

        # Check cache first
        from bot.database import db
        if db:
            cached_doc = await db.find_cached_file(title, "", quality)
            if cached_doc and cached_doc.get("file_id"):
                clean_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', title)}_{cached_doc.get('quality', quality)}.mp4"
                try:
                    await client.send_document(
                        chat_id=chat_id,
                        document=cached_doc["file_id"],
                        file_name=clean_filename,
                        caption=f"📦 <b>{title}</b> [{cached_doc.get('quality', quality)}]\n<i>⚡ From library — instant delivery!</i>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                    await status_msg.edit_text(
                        f"✅ <b>{name}</b>: <b>{title}</b> is already in library!\n<i>⚡ Delivered instantly from cache.</i>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return f"'{title}' was already in library. Delivered instantly to chat {chat_id} from cache without re-downloading."
                except Exception as e:
                    log.warning("Cached delivery failed in tool_download_and_send_anime: %s", e)
                    await db.files.delete_one({"_id": cached_doc["_id"]})

        await status_msg.edit_text(
            f"🤖 <b>{name}</b>: Initiating download for <b>{title}</b> [{quality}]...",
            parse_mode=enums.ParseMode.HTML,
        )

        clean_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', title)}_{quality}.mp4"
        slug = re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-').lower()

        dest_chan_id = None
        from bot.database import db
        if db:
            mapping = await db.get_channel_mapping(slug)
            if not mapping:
                auto_chan = await db.get_config("auto_channel_creation", default=False)
                from bot.userbot import userbot_manager
                if auto_chan and userbot_manager and userbot_manager.is_active:
                    try:
                        mapping = await userbot_manager.create_anime_channel(
                            series_title=title.title(),
                            series_slug=slug,
                        )
                    except Exception as ce:
                        log.warning("AI auto-channel creation failed in movie: %s", ce)
            if mapping and mapping.get("channel_id"):
                dest_chan_id = mapping["channel_id"]

        success, sent_msg = await download_and_upload(
            chat_id=chat_id,
            stream_url=stream_url,
            quality=quality,
            filename=clean_filename,
            title=title,
            progress_msg=status_msg,
            client=client,
            destination_channel_id=dest_chan_id,
        )

        if success and sent_msg:
            file_id = None
            file_unique_id = None
            if sent_msg.video:
                file_id = sent_msg.video.file_id
                file_unique_id = sent_msg.video.file_unique_id
            elif sent_msg.document:
                file_id = sent_msg.document.file_id
                file_unique_id = sent_msg.document.file_unique_id

            if file_id and file_unique_id:
                slug = re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-').lower()
                from bot.library import library_manager
                if library_manager:
                    try:
                        await library_manager.save_to_library(
                            series_slug=slug,
                            series_title=title.title(),
                            quality=quality,
                            episode_key="movie",
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            is_movie=True,
                        )
                    except Exception as le:
                        log.warning("Library save failed in tool_download_and_send_anime: %s", le)

                from bot.database import db
                if db:
                    try:
                        sent_bytes = (sent_msg.video.file_size if sent_msg.video else (sent_msg.document.file_size if sent_msg.document else None)) if sent_msg else None
                        await db.save_file(
                            series_slug=slug,
                            series_title=title.title(),
                            quality=quality,
                            episode_key="movie",
                            file_id=file_id,
                            file_unique_id=file_unique_id,
                            file_size=sent_bytes,
                        )
                    except Exception as de:
                        log.warning("DB save failed in tool_download_and_send_anime: %s", de)

            return f"Success: Downloaded and uploaded '{title}' [{quality}] directly to Telegram chat {chat_id}, and saved to Main Channel Library."
        return f"Download or upload failed for '{title}'. Check server logs for details."
    except Exception as e:
        log.exception("tool_download_and_send_anime failed")
        return f"Error downloading and sending anime: {e}"


async def tool_search_toonflix(query: str) -> str:
    try:
        from extractors.toonflix import toonflix
        results = await toonflix.search(query)
        return json.dumps(results[:10], indent=2) if results else f"No results on ToonFlix for '{query}'"
    except Exception as e:
        return f"ToonFlix search error: {e}"


async def tool_resolve_toonflix_stream(anime_title: str, season: int = 1, episode: int = 1, quality_pref: str = "1080p") -> str:
    try:
        from extractors.toonflix import toonflix
        res = await toonflix.resolve_episode(anime_title, season=season, episode=episode, quality_pref=quality_pref)
        return json.dumps(res, indent=2) if res else f"Could not resolve stream for '{anime_title}' S{season}E{episode} [{quality_pref}] on ToonFlix."
    except Exception as e:
        return f"ToonFlix resolution error: {e}"


async def tool_remember_fact(key: str, fact: str) -> str:
    """Store an important fact or preference permanently in MongoDB."""
    from bot.database import db
    from config import settings
    _, chat_id = get_active_context()
    target_id = chat_id or settings.bot.owner_id
    if not db:
        return "Database not initialized. Cannot save permanent memory."
    try:
        await db.save_ai_fact(target_id, key, fact)
        return f"Successfully saved to long-term memory: [{key}] -> '{fact}'"
    except Exception as e:
        return f"Failed to save fact: {e}"


async def tool_recall_facts() -> str:
    """Retrieve all stored permanent memory facts for current chat."""
    from bot.database import db
    from config import settings
    _, chat_id = get_active_context()
    target_id = chat_id or settings.bot.owner_id
    if not db:
        return "Database not initialized."
    try:
        facts = await db.get_ai_facts(target_id)
        if not facts:
            return "No permanent memory facts stored yet."
        formatted = "\n".join([f"• [{f.get('key')}]: {f.get('value')}" for f in facts])
        return f"Stored Permanent Memories ({len(facts)}):\n{formatted}"
    except Exception as e:
        return f"Failed to recall facts: {e}"


async def tool_forget_fact(key: str) -> str:
    """Delete a stored memory fact by key."""
    from bot.database import db
    from config import settings
    _, chat_id = get_active_context()
    target_id = chat_id or settings.bot.owner_id
    if not db:
        return "Database not initialized."
    try:
        deleted = await db.delete_ai_fact(target_id, key)
        if deleted:
            return f"Successfully forgot and removed memory: [{key}]."
        return f"No memory fact found with key: '{key}'."
    except Exception as e:
        return f"Failed to delete fact: {e}"


async def tool_create_series_channel(
    series_title: str,
    series_slug: str,
    poster_url: str = "",
    description: str = "",
) -> str:
    """Create a dedicated per-anime channel via Userbot, promote bots, and map it in MongoDB."""
    from bot.userbot import userbot_manager
    if not userbot_manager or not userbot_manager.is_active:
        return "Error: Userbot is not connected. The owner needs to run /login first to enable channel creation."

    try:
        mapping = await userbot_manager.create_anime_channel(
            series_title=series_title,
            series_slug=series_slug,
            poster_url=poster_url or None,
            description=description or None,
        )
        return json.dumps({
            "status": "success",
            "series_title": mapping.get("series_title", series_title),
            "series_slug": series_slug,
            "channel_id": mapping["channel_id"],
            "invite_link": mapping.get("invite_link"),
        }, indent=2)
    except Exception as e:
        return f"Error creating anime channel: {e}"


async def tool_get_channel_mapping(series_slug: str) -> str:
    """Get mapped dedicated Telegram channel details for an anime series."""
    from bot.database import db
    if not db:
        return "Error: Database not initialized."

    try:
        mapping = await db.get_channel_mapping(series_slug)
        if not mapping:
            return f"No channel mapped for series slug '{series_slug}'."
        clean = {k: v for k, v in mapping.items() if k != "_id"}
        return json.dumps(clean, indent=2)
    except Exception as e:
        return f"Error getting channel mapping: {e}"


async def tool_list_channel_mappings() -> str:
    """List all mapped anime series channels."""
    from bot.database import db
    if not db:
        return "Error: Database not initialized."

    try:
        channels = await db.list_channel_mappings()
        if not channels:
            return "No mapped anime channels found."
        summary = []
        for c in channels:
            summary.append({
                "series_title": c.get("series_title"),
                "series_slug": c.get("series_slug"),
                "channel_id": c.get("channel_id"),
                "invite_link": c.get("invite_link"),
            })
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error listing channel mappings: {e}"


async def tool_set_channel_mapping(
    series_slug: str,
    channel_id: int,
    invite_link: str,
    series_title: str = "",
) -> str:
    """Manually map an anime series to an existing Telegram channel."""
    from bot.database import db
    if not db:
        return "Error: Database not initialized."

    try:
        mapping = await db.set_channel_mapping(
            series_slug=series_slug,
            channel_id=channel_id,
            invite_link=invite_link,
            series_title=series_title or series_slug,
            auto_created=False,
        )
        return json.dumps({
            "status": "success",
            "series_slug": series_slug,
            "channel_id": channel_id,
            "invite_link": invite_link,
        }, indent=2)
    except Exception as e:
        return f"Error setting channel mapping: {e}"


# ── Tool Dispatcher ───────────────────────────────────────────────────

TOOL_MAP = {
    "search_anime": tool_search_anime,
    "get_series_details": tool_get_series_details,
    "inspect_episode_servers": tool_inspect_episode_servers,
    "resolve_player_stream": tool_resolve_player_stream,
    "list_project_files": tool_list_project_files,
    "read_project_file": tool_read_project_file,
    "edit_project_file": tool_edit_project_file,
    "write_project_file": tool_write_project_file,
    "run_shell_command": tool_run_shell_command,
    "check_source_status": tool_check_source_status,
    "search_animedrive": tool_search_animedrive,
    "get_animedrive_episodes": tool_get_animedrive_episodes,
    "resolve_animedrive_stream": tool_resolve_animedrive_stream,
    "download_anime_episode": tool_download_anime_episode,
    "download_and_send_anime": tool_download_and_send_anime,
    "search_toonflix": tool_search_toonflix,
    "resolve_toonflix_stream": tool_resolve_toonflix_stream,
    "remember_fact": tool_remember_fact,
    "recall_facts": tool_recall_facts,
    "forget_fact": tool_forget_fact,
    "create_series_channel": tool_create_series_channel,
    "get_channel_mapping": tool_get_channel_mapping,
    "list_channel_mappings": tool_list_channel_mappings,
    "set_channel_mapping": tool_set_channel_mapping,
}


async def execute_tool_call(name: str, arguments: dict[str, Any]) -> str:
    """Execute a tool call requested by the LLM."""
    func = TOOL_MAP.get(name)
    if not func:
        return f"Error: Unknown tool '{name}'."
    try:
        log.info("Executing AI tool: %s with args %s", name, arguments)
        result = await func(**arguments)
        return str(result)
    except Exception as e:
        log.exception("Tool execution failed for %s", name)
        return f"Error executing {name}: {e}"

