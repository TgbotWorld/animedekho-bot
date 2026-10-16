"""Data models for AnimeDekho entities."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Quality:
    resolution: str          # "720p", "1080p" etc
    url: str
    bandwidth: int = 0
    label: str = ""          # human-readable like "720p (HD)"
    master_url: str = ""     # master m3u8 URL (for multi-audio/subtitle)

    def __post_init__(self):
        if not self.label:
            self.label = self.resolution


@dataclass
class VideoServer:
    name: str
    server_id: int
    proxy_url: str = ""
    player_url: str = ""        # real CDN player
    direct_url: str = ""        # direct .m3u8 / .mp4 if extracted
    video_type: str = ""        # m3u8 / mp4
    qualities: list[Quality] = field(default_factory=list)

    @property
    def is_available(self) -> bool:
        return bool(self.player_url or self.direct_url)


@dataclass
class SearchResult:
    title: str
    slug: str
    url: str
    content_type: str           # "series" | "movie"
    poster: str = ""
    source: str = "AnimeDekho"

    @property
    def is_series(self) -> bool:
        return self.content_type == "series"


@dataclass
class Episode:
    number: int
    slug: str
    season: int = 0
    title: str = ""
    page_url: str = ""
    post_id: int = 0
    dgt_post_id: int = 0
    dgt_episode_id: int = 0
    servers: list[VideoServer] = field(default_factory=list)


@dataclass
class Season:
    number: int
    episodes: list[Episode] = field(default_factory=list)

    @property
    def episode_count(self) -> int:
        return len(self.episodes)


@dataclass
class Series:
    title: str
    slug: str
    url: str
    description: str = ""
    poster: str | None = None
    genres: list[str] = field(default_factory=list)
    seasons: dict[int, Season] = field(default_factory=dict)
    source: str = "AnimeDekho"
    dgt_series_id: int = 0
    dgt_seasons: list[int] = field(default_factory=list)

    @property
    def season_count(self) -> int:
        return len(self.seasons)

    @property
    def total_episodes(self) -> int:
        return sum(s.episode_count for s in self.seasons.values())


@dataclass
class Movie:
    title: str
    slug: str
    url: str
    description: str = ""
    poster: str | None = None
    genres: list[str] = field(default_factory=list)
    post_id: int = 0
    dgt_post_id: int = 0
    servers: list[VideoServer] = field(default_factory=list)


@dataclass
class Category:
    id: int
    name: str
    slug: str
    count: int = 0


@dataclass
class PaginatedResult:
    items: list
    current_page: int
    max_page: int

    @property
    def has_next(self) -> bool:
        return self.current_page < self.max_page

    @property
    def has_prev(self) -> bool:
        return self.current_page > 1
