"""Owner/admin-only /bypass manual resolver (V2 #23)."""

from __future__ import annotations

import logging

from bot.telegram import Client, enums
from bot.telegram.types import Message
from bot.auth import require_owner
from utils.helpers import esc

log = logging.getLogger(__name__)


@require_owner
async def cmd_bypass(client: Client, message: Message):
    """Resolve a supported public page/episode URL into download info.

    Usage:
        /bypass https://toonworld4all.me/...
        /bypass https://archive.toonworld4all.me/episode/...
    """
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply_text(
            "🔗 <b>/bypass Manual Resolver</b>\n\n"
            "Usage: <code>/bypass &lt;public page/episode URL&gt;</code>\n\n"
            "Supported: AnimeDubHindi, ToonWorld4All, ToonAnime, RareAnimes, "
            "DeadToons, TOONo, AnimeDrive, ToonFlix.",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    target_url = parts[1].strip().split()[0]
    status = await message.reply_text(
        f"🔎 <b>Resolving source...</b>\n<code>{esc(target_url[:120])}</code>",
        parse_mode=enums.ParseMode.HTML,
    )
    try:
        from extractors.bypass import resolve_bypass_url
        res = await resolve_bypass_url(target_url)
    except Exception as e:
        log.exception("bypass resolve crashed for %s", target_url[:100])
        try:
            await status.edit_text(f"❌ <b>Bypass crashed:</b> {esc(str(e)[:200])}", parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return

    if not res.get("ok"):
        stage = res.get("stage", "?")
        err = res.get("error", "resolution failed")
        src = res.get("source") or "unknown"
        provider = res.get("provider") or "—"
        try:
            await status.edit_text(
                f"❌ <b>Automatic resolution failed</b>\n\n"
                f"🎬 Anime: {esc(str(res.get('anime') or '—'))}\n"
                f"🔗 Source: {esc(str(src))}\n"
                f"📡 Provider: {esc(str(provider))}\n"
                f"🎚️ Quality: requested —\n"
                f"🌐 Original: <code>{esc(target_url[:150])}</code>\n"
                f"🌐 Resolved: <code>{esc(str(res.get('resolved_url') or res.get('final_url') or '')[:150])}</code>\n"
                f"🧩 Resolver: {esc(str(res.get('resolver_stage') or stage))}\n"
                f"🔁 Fallback: {esc(str(res.get('fallback_stage') or '—'))}\n"
                f"📝 Failure reason: {esc(str(res.get('failure_reason') or err)[:400])}",
                parse_mode=enums.ParseMode.HTML,
            )
        except Exception:
            pass
        return

    # V3 #10 diagnostics + V3 #12 provider-grouped AnimeDubHindi format.
    lines = [
        f"🎬 <b>Anime:</b> {esc(str(res.get('anime')))}",
    ]
    if res.get("season") is not None:
        lines.append(f"📂 <b>Season:</b> {res.get('season')}")
    if res.get("episode") is not None:
        lines.append(f"🎞️ <b>Episode:</b> {res.get('episode')}")
    lines.append(f"🔗 <b>Source:</b> {esc(str(res.get('source')))}")
    if res.get("provider"):
        lines.append(f"📡 <b>Provider:</b> {esc(str(res.get('provider')))}")
    quals = res.get("qualities") or []
    if quals:
        lines.append(f"🎚️ <b>Quality:</b> {esc(' / '.join(quals))}")
    lines.append(f"🌐 <b>Original:</b> <code>{esc(target_url[:150])}</code>")
    lines.append(f"🌐 <b>Resolved:</b> <code>{esc(str(res.get('resolved_url') or res.get('final_url'))[:150])}</code>")
    lines.append(f"🧩 <b>Resolver:</b> {esc(str(res.get('resolver_stage') or res.get('stage')))}")
    if res.get("fallback_stage"):
        lines.append(f"🔁 <b>Fallback:</b> {esc(str(res.get('fallback_stage')))}")
    lines.append("")
    providers = res.get("providers") or {}
    if providers:
        # V3 #12 required grouped format.
        for prov, qmap in providers.items():
            lines.append(f"<b>{esc(str(prov))}</b>")
            for q in ("480p", "720p", "1080p", "4K", "Unknown"):
                urls = (qmap or {}).get(q) or []
                if urls:
                    lines.append(f"[{q}] ({len(urls)} link(s))")
                    for u in urls[:2]:
                        lines.append(f"   • <code>{esc(str(u)[:150])}</code>")
            lines.append("")
    else:
        lines.append("✅ <b>Resolved Download/Media Links:</b>")
        for m in (res.get("media_links") or [])[:10]:
            prov = esc(str(m.get("provider", "Direct")))
            # Label carries the variant + size ("720p x265 10bit · 134.94 MB").
            label = str(m.get("label") or "").strip()
            head = esc(label) if label else esc(str(m.get("quality", "?")))
            u = esc(str(m.get("url", ""))[:150])
            lines.append(f"   • [{prov}] {head} → <code>{u}</code>")
    if res.get("archive_links"):
        lines.append("")
        lines.append("📦 <b>ZIP/Archive Links:</b>")
        for u in (res.get("archive_links") or [])[:5]:
            lines.append(f"   • <code>{esc(str(u)[:200])}</code>")
    text = "\n".join(lines)[:4000]
    try:
        await status.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        try:
            await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
