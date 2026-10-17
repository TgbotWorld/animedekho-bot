"""Test episode buttons and callback slugs for long anime titles (e.g. Trapped in a Dating Sim)."""

import pytest
from bot.slug_store import (
    pack_episode_slug, pack_series_slug, pack_slug,
    resolve_slug, parse_season_episode,
)
from bot.keyboards import episode_picker, quality_picker, season_picker
from api.models import Episode, Series, Season


def test_long_slug_tokenization_and_resolution():
    series_slug = "trapped-in-a-dating-sim-the-world-of-otome-games-is-tough-for-mobs"
    ep1_slug = f"{series_slug}-2x1"
    ep6_slug = f"{series_slug}-2x6"

    tok1 = pack_episode_slug(ep1_slug, season=2, ep_num=1)
    tok6 = pack_episode_slug(ep6_slug, season=2, ep_num=6)

    # 1. Tokens must be distinct
    assert tok1 != tok6, "Tokens for episode 1 and episode 6 must be different"

    # 2. Both tokens must resolve back to their full original slugs
    assert resolve_slug(tok1) == ep1_slug
    assert resolve_slug(tok6) == ep6_slug

    # 3. Parsing season/episode from token must yield (2, 1) and (2, 6)
    assert parse_season_episode(tok1) == (2, 1)
    assert parse_season_episode(tok6) == (2, 6)

    # 4. Parsing season/episode from full slug must yield (2, 1) and (2, 6)
    assert parse_season_episode(ep1_slug) == (2, 1)
    assert parse_season_episode(ep6_slug) == (2, 6)


def test_keyboard_callback_data_within_64_bytes():
    series_slug = "trapped-in-a-dating-sim-the-world-of-otome-games-is-tough-for-mobs"
    episodes = [
        Episode(number=i, slug=f"{series_slug}-2x{i}", season=2)
        for i in range(1, 13)
    ]

    kb = episode_picker(series_slug, season=2, episodes=episodes)

    ep_callbacks = {}
    for row in kb.inline_keyboard:
        for btn in row:
            cb = btn.callback_data
            assert len(cb.encode("utf-8")) <= 64, f"Callback data too long: {cb}"
            if btn.text.startswith("Ep "):
                ep_callbacks[btn.text] = cb

    # Check Ep 1 vs Ep 6 callbacks are different
    assert "Ep 1" in ep_callbacks
    assert "Ep 6" in ep_callbacks
    assert ep_callbacks["Ep 1"] != ep_callbacks["Ep 6"], "Ep 1 and Ep 6 buttons must have distinct callback data!"

    # Verify that quality picker for Ep 6 stays within 64 bytes
    ep6_slug = f"{series_slug}-2x6"
    q_kb = quality_picker({"480p", "720p", "1080p", "4K"}, ep6_slug, back_cb="se:test:2")
    for row in q_kb.inline_keyboard:
        for btn in row:
            cb = btn.callback_data
            assert len(cb.encode("utf-8")) <= 64, f"Quality callback data too long: {cb}"


def test_long_file_param_within_64_chars():
    from utils.helpers import encode_file_param, decode_file_param
    long_slug = "i-was-reincarnated-as-the-7th-prince-so-i-can-take-my-time-perfecting-my-magical-ability"

    # 1. get_ deep link parameter
    raw_param = f"get_{long_slug}_1080p_HQ_HEVC_s1e01"
    encoded = encode_file_param(raw_param)
    assert len(encoded) <= 64, f"Encoded param exceeds 64 chars ({len(encoded)}): {encoded}"
    decoded = decode_file_param(encoded)
    assert decoded == raw_param, f"Decoded param mismatch: expected {raw_param}, got {decoded}"

    # 2. join_ deep link parameter
    join_param = f"join_{long_slug}"
    enc_join = encode_file_param(join_param)
    assert len(enc_join) <= 64, f"Encoded join param exceeds 64 chars ({len(enc_join)}): {enc_join}"
    dec_join = decode_file_param(enc_join)
    assert dec_join == join_param, f"Decoded join mismatch: expected {join_param}, got {dec_join}"

    # 3. dl_ deep link parameter
    dl_param = f"dl_{long_slug}_2"
    enc_dl = encode_file_param(dl_param)
    assert len(enc_dl) <= 64, f"Encoded dl param exceeds 64 chars ({len(enc_dl)}): {enc_dl}"
    dec_dl = decode_file_param(enc_dl)
    assert dec_dl == dl_param, f"Decoded dl mismatch: expected {dl_param}, got {dec_dl}"

    # 4. Standard short parameter maintains standard base64 behavior
    short_param = "get_naruto_720p_s1e01"
    enc_short = encode_file_param(short_param)
    assert len(enc_short) <= 64
    assert decode_file_param(enc_short) == short_param


def test_bot_username_sanitization():
    from bot.library import bot_username_for_quality
    uname = bot_username_for_quality("720p", "@TestBot")
    assert not uname.startswith("@")
    assert uname == "TestBot"

