from pathlib import Path

from PIL import Image

from bot.library import LibraryManager, _album_episode_label, _album_thumbnail_episode_line
from bot.thumbnail import StreamingCardTemplate, enhance_custom_thumbnail, generate_auto_thumbnail


def test_album_episode_label_formats_seasons_and_movies():
    assert _album_episode_label(["S01E01", "S01E02", "S01E12"]) == "S01 · EPISODES 01–02, 12"
    assert _album_episode_label(["S01E02", "S02E01"]) == "S01 · EPISODE 02 / S02 · EPISODE 01"
    assert _album_episode_label(["movie"], is_movie=True) == "FEATURE FILM"
    assert _album_thumbnail_episode_line(["S01E01", "S01E02", "S01E12"]) == "SEASON 01 · 03 EPISODES"
    assert _album_thumbnail_episode_line(["movie"], is_movie=True) == "FEATURE FILM"


def test_album_caption_has_reference_metadata_and_safe_synopsis():
    manager = LibraryManager(None, None, -100123, "AnimeDekhoBot")
    caption = manager._format_album_caption(
        "The Cold Sato-san & Me",
        ["S01E01", "S01E02"],
        ["480p", "720p", "1080p"],
        False,
        None,
        channel_mapping={"language": "Japanese [English-Sub]"},
        synopsis="<i>A quiet school life & a surprising smile.</i>",
    )

    assert "The Cold Sato-san &amp; Me" in caption
    assert "<b>Language</b> · Japanese [English-Sub]" in caption
    assert "<b>Quality</b> · 480p · 720p · 1080p" in caption
    assert "<b>Episode</b> · S01 · EPISODES 01–02" in caption
    assert "A quiet school life &amp; a surprising smile." in caption
    assert "<i>" not in caption


def test_streaming_thumbnail_renders_a_mobile_widescreen_card(tmp_path: Path):
    poster_path = tmp_path / "poster.png"
    output_path = tmp_path / "anime-card.jpg"
    Image.new("RGB", (640, 900), (120, 38, 72)).save(poster_path)

    result = generate_auto_thumbnail(
        title="The Cold Sato-san Is Only Sweet to Me",
        episode_info="SEASON 01 · 12 EPISODES",
        quality="1080p",
        audio="Japanese [English-Sub]",
        poster_path=str(poster_path),
        output_path=str(output_path),
        template_name="streaming",
        brand_username="@AnimeDekho",
        synopsis="A quiet school life is changed by one unexpected friendship.",
    )

    assert result == str(output_path)
    with Image.open(output_path) as card:
        assert card.size == (1280, 720)
        assert card.format == "JPEG"
    assert output_path.stat().st_size > 20_000


def test_detailed_album_card_is_not_forced_under_the_old_285kb_cap(tmp_path: Path):
    poster_path = tmp_path / "detailed-poster.png"
    output_path = tmp_path / "detailed-card.jpg"
    Image.effect_noise((1280, 720), 100).convert("RGB").save(poster_path)

    result = generate_auto_thumbnail(
        title="A Detailed Anime Poster",
        quality="1080p",
        poster_path=str(poster_path),
        output_path=str(output_path),
        template_name="streaming",
    )

    assert result == str(output_path)
    assert output_path.stat().st_size > 285 * 1024
    assert output_path.stat().st_size <= 1800 * 1024


def test_video_thumbnail_is_telegram_sized_and_preserves_valid_jpeg(tmp_path: Path):
    poster_path = tmp_path / "poster.png"
    video_thumb_path = tmp_path / "video-thumb.jpg"
    Image.new("RGB", (640, 900), (120, 38, 72)).save(poster_path)

    result = enhance_custom_thumbnail(str(poster_path), str(video_thumb_path))

    assert result == str(video_thumb_path)
    with Image.open(video_thumb_path) as video_thumb:
        assert video_thumb.size == (320, 180)
        assert video_thumb.format == "JPEG"
    assert video_thumb_path.stat().st_size < 200 * 1024

    valid_thumb_path = tmp_path / "valid-thumb.jpg"
    unchanged_path = tmp_path / "unchanged-thumb.jpg"
    Image.new("RGB", (320, 180), (70, 110, 160)).save(valid_thumb_path, "JPEG", quality=92)
    original_bytes = valid_thumb_path.read_bytes()

    assert enhance_custom_thumbnail(str(valid_thumb_path), str(unchanged_path)) == str(unchanged_path)
    assert unchanged_path.read_bytes() == original_bytes


def test_thumbnail_layout_reserves_more_space_for_poster_art():
    assert StreamingCardTemplate.RAIL_W == 520
    assert StreamingCardTemplate.ART_BOX_RATIO == 0.72
    assert StreamingCardTemplate.TITLE_BASE_SIZE == 52
    assert StreamingCardTemplate.SYNOPSIS_MAX_LINES == 2
