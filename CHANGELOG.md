# Changelog

All notable changes to this project are documented here.

## [1.0.0] - 2026-09-14

First stable release.

### Added

- Top 250 movies (`/top250`) and Top 250 TV shows (`/toptv`)
- Search, filters (`year`, `min_rating`), sorting, and pagination on both lists
- Single-title lookup (`/movie/:id`, `/tv/:id`) and `/random`
- Public request counters (`/stats`) backed by D1
- Edge caching with stale-while-revalidate fallback plus bundled seed data
- Python (FastAPI) version with Docker support and importable functions
- Unit tests and CI
