"""Tests for backend.app.taxonomy — controlled vocabulary loading and lookup."""
from __future__ import annotations

from backend.app.taxonomy import get_options, load_taxonomy


class TestLoadTaxonomy:
    def test_loads_dict(self):
        data = load_taxonomy()
        assert isinstance(data, dict)

    def test_has_countries(self):
        assert "countries" in load_taxonomy()

    def test_has_sectors(self):
        assert "sectors" in load_taxonomy()

    def test_has_sub_sectors_by_sector(self):
        assert "sub_sectors_by_sector" in load_taxonomy()

    def test_sub_sectors_flat_computed(self):
        data = load_taxonomy()
        assert "sub_sectors_flat" in data
        assert isinstance(data["sub_sectors_flat"], list)
        assert len(data["sub_sectors_flat"]) > 0

    def test_sub_sectors_flat_is_sorted_unique(self):
        flat = load_taxonomy()["sub_sectors_flat"]
        assert flat == sorted(set(flat))

    def test_cached(self):
        # lru_cache should return the same object
        assert load_taxonomy() is load_taxonomy()


class TestGetOptions:
    def test_countries(self):
        opts = get_options("countries")
        assert isinstance(opts, list)
        assert "United States" in opts
        assert "Brazil" in opts

    def test_sectors(self):
        opts = get_options("sectors")
        assert "Health" in opts
        assert "Education" in opts
        assert len(opts) == 11

    def test_sub_sectors_flat(self):
        opts = get_options("sub_sectors_flat")
        assert "Health" in opts  # from the Health sector
        assert len(opts) > 11  # more sub-sectors than sectors

    def test_unknown_key_returns_empty(self):
        assert get_options("nonexistent") == []

    def test_empty_string_key(self):
        assert get_options("") == []
