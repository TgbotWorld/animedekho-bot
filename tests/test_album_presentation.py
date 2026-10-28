from pathlib import Path

from PIL import Image, ImageDraw

from bot.library import LibraryManager, _album_episode_label, _album_thumbnail_episode_line
from bot.presentation import format_episode_post_caption
from bot.thumbnail import (
    StreamingCardTemplate,
    _build_edge_vignette,
    _cinematic_color_grade,
    _horizontal_fade_mask,
    _particle_overlay,
    _resolve_poster_image,
    _wrap_and_fit_title,
    enhance_custom_thumbnail,
    generate_auto_thumbnail,
)


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
    assert "🌐 <b>LANGUAGE</b> · <code>Japanese [English-Sub]</code>" in caption
    assert "🎞 <b>QUALITY</b> · <code>480p · 720p · 1080p</code>" in caption
    assert "📺 <b>EPISODES</b> · <code>S01 · EPISODES 01–02</code>" in caption
    assert "📝 <b>ABOUT</b>" in caption
    assert "A quiet school life &amp; a surprising smile." in caption
    assert "📡 <b>More anime:</b>" in caption
    assert "<i>" not in caption


def test_individual_episode_caption_separates_audio_quality_and_episode():
    caption = format_episode_post_caption(
        "Smoking Behind the Supermarket with You & Friends (Hindi Dubbed)",
        "1080p",
        "S1E01",
    )

    assert "📺 <b>Smoking Behind the Supermarket with You &amp; Friends</b>" in caption
    assert "🌐 <b>Hindi Dubbed</b>" in caption
    assert "🎞 <b>1080P</b>" in caption
    assert "📺 <code>S01 · EP 01</code>" in caption


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


def test_portrait_poster_stays_source_sized_until_final_composition(tmp_path: Path):
    poster_path = tmp_path / "portrait-460x609.png"
    Image.new("RGB", (460, 609), (90, 55, 130)).save(poster_path)

    art = _resolve_poster_image(str(poster_path))

    assert art is not None
    assert art.size == (460, 609)


def test_portrait_card_keeps_top_and_bottom_poster_content(tmp_path: Path):
    poster_path = tmp_path / "portrait-with-edge-markers.png"
    output_path = tmp_path / "portrait-card.jpg"
    poster = Image.new("RGB", (460, 609), (35, 95, 75))
    draw = ImageDraw.Draw(poster)
    draw.rectangle((0, 0, 459, 39), fill=(235, 32, 28))
    draw.rectangle((0, 569, 459, 608), fill=(30, 60, 235))
    poster.save(poster_path)

    assert generate_auto_thumbnail(
        title="Portrait Fit Check",
        episode_info="SEASON 01 · 12 EPISODES",
        quality="1080p",
        poster_path=str(poster_path),
        output_path=str(output_path),
        template_name="streaming",
    ) == str(output_path)

    with Image.open(output_path) as card:
        top = card.convert("RGB").getpixel((1000, 8))
        bottom = card.convert("RGB").getpixel((1000, 711))
    assert top[0] > top[2] * 1.5
    assert bottom[2] > bottom[0] * 1.5


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


def test_portrait_video_thumbnail_preserves_full_source_edges(tmp_path: Path):
    poster_path = tmp_path / "portrait-video-source.png"
    thumb_path = tmp_path / "portrait-video-thumb.jpg"
    poster = Image.new("RGB", (460, 609), (35, 95, 75))
    draw = ImageDraw.Draw(poster)
    draw.rectangle((0, 0, 459, 39), fill=(235, 32, 28))
    draw.rectangle((0, 569, 459, 608), fill=(30, 60, 235))
    poster.save(poster_path)

    assert enhance_custom_thumbnail(str(poster_path), str(thumb_path)) == str(thumb_path)
    with Image.open(thumb_path) as thumb:
        top = thumb.convert("RGB").getpixel((160, 18))
        bottom = thumb.convert("RGB").getpixel((160, 163))
        assert thumb.size == (320, 180)
    assert top[0] > top[2] * 1.5
    assert bottom[2] > bottom[0] * 1.5


def test_thumbnail_layout_reserves_more_space_for_poster_art():
    assert StreamingCardTemplate.TEXT_AREA_RATIO == 0.45
    assert StreamingCardTemplate.POSTER_AREA_RATIO == 0.55
    assert StreamingCardTemplate.TEXT_AREA_WIDTH == 576
    assert StreamingCardTemplate.BLUR_AREA_RATIO == 0.28
    assert StreamingCardTemplate.BLUR_AREA_WIDTH == 358
    portrait_width = int(720 * 460 / 609)
    assert portrait_width > StreamingCardTemplate.BLUR_AREA_WIDTH
    poster_x = StreamingCardTemplate.POSTER_AREA_X + (
        StreamingCardTemplate.POSTER_AREA_WIDTH - portrait_width
    ) // 2
    assert poster_x == 656
    assert poster_x + portrait_width < 1280
    assert StreamingCardTemplate.RAIL_X + StreamingCardTemplate.RAIL_W <= StreamingCardTemplate.TEXT_AREA_WIDTH
    assert StreamingCardTemplate.ART_BOX_RATIO == 0.55
    assert StreamingCardTemplate.TITLE_BASE_SIZE == 60
    assert StreamingCardTemplate.EPISODE_COUNT_SIZE == 25
    assert StreamingCardTemplate.EPISODE_CHIP_HEIGHT == 44
    assert StreamingCardTemplate.SYNOPSIS_MAX_LINES == 2


def test_title_fits_completely_in_the_45_percent_text_area():
    title = "THE COLD SATO-SAN IS ONLY SWEET TO ME"
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines, font, _ = _wrap_and_fit_title(
        draw,
        title,
        max_w=StreamingCardTemplate.RAIL_W,
        base_size=StreamingCardTemplate.TITLE_BASE_SIZE,
        min_size=StreamingCardTemplate.TITLE_MIN_SIZE,
        max_lines=3,
    )

    assert " ".join(lines) == title
    assert font.size >= 40


def test_portrait_blur_fades_well_before_the_sharp_poster():
    blur_end = StreamingCardTemplate.BLUR_AREA_WIDTH
    mask = _horizontal_fade_mask((800, 10), fade_start=blur_end - 180, fade_end=blur_end)

    assert mask.getpixel((blur_end - 181, 5)) == 255
    assert mask.getpixel((blur_end - 180, 5)) == 255
    assert 120 <= mask.getpixel((blur_end - 90, 5)) <= 130
    assert mask.getpixel((blur_end, 5)) == 0
    assert mask.getpixel((StreamingCardTemplate.TEXT_AREA_WIDTH, 5)) == 0


def test_cinematic_effect_layers_apply_grade_vignette_and_repeatable_particles():
    source = Image.new("RGB", (96, 54), (120, 100, 140))
    graded = _cinematic_color_grade(source)
    assert graded.size == source.size
    assert graded.tobytes() != source.tobytes()

    vignette = _build_edge_vignette((128, 72), max_alpha=48).getchannel("A")
    assert vignette.getpixel((0, 0)) > vignette.getpixel((64, 36))

    particles_a = _particle_overlay((128, 72), seed=417).getchannel("A")
    particles_b = _particle_overlay((128, 72), seed=417).getchannel("A")
    assert particles_a.tobytes() == particles_b.tobytes()
    assert particles_a.getbbox() is not None
