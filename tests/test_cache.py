import pytest
from gabechoice.cache import GameCache


@pytest.fixture
async def cache(tmp_path):
    c = GameCache(str(tmp_path / "test.sqlite"))
    await c.init_db()
    return c


async def test_get_miss(cache):
    assert await cache.get(99999) is None


async def test_set_then_get(cache):
    await cache.set(570, {"name": "Dota 2", "genres": ["Free to Play"]})
    result = await cache.get(570)
    assert result is not None
    assert result["name"] == "Dota 2"
    assert result["genres"] == ["Free to Play"]
    assert "cached_at" in result


async def test_get_many_partial(cache):
    await cache.set(570, {"name": "Dota 2"})
    await cache.set(730, {"name": "CS2"})
    result = await cache.get_many([570, 730, 99999])
    assert set(result.keys()) == {570, 730}
    assert result[570]["name"] == "Dota 2"


async def test_get_many_empty(cache):
    assert await cache.get_many([]) == {}


async def test_set_overwrites(cache):
    await cache.set(570, {"name": "Old Name"})
    await cache.set(570, {"name": "New Name"})
    result = await cache.get(570)
    assert result["name"] == "New Name"


async def test_prune_stale_removes_old(cache):
    # Insert a record then prune with ttl=0 (everything is stale).
    await cache.set(570, {"name": "Dota 2"})
    pruned = await cache.prune_stale(ttl_days=0)
    assert pruned >= 1
    assert await cache.get(570) is None


async def test_prune_stale_keeps_fresh(cache):
    await cache.set(570, {"name": "Dota 2"})
    pruned = await cache.prune_stale(ttl_days=30)
    # A record written seconds ago should survive a 30-day TTL.
    assert pruned == 0
    assert await cache.get(570) is not None
