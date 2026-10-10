import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from forecast_cache import ReadCache


class CacheTests(unittest.TestCase):
    def test_hit_expiry_and_no_stale_fallback(self):
        clock = Mock(return_value=0)
        loader = Mock(side_effect=['fresh', RuntimeError('NWS down'), 'new'])
        cache = ReadCache(clock)
        self.assertEqual(cache.get('hourly', 300, loader), 'fresh')
        clock.return_value = 299
        self.assertEqual(cache.get('hourly', 300, loader), 'fresh')
        loader.assert_called_once()
        clock.return_value = 300
        with self.assertRaises(RuntimeError):
            cache.get('hourly', 300, loader)
        self.assertEqual(cache.get('hourly', 300, loader), 'new')

    def test_concurrent_requests_share_fetch(self):
        cache, loader = ReadCache(), Mock(return_value='forecast')
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: cache.get('hourly', 300, loader), range(20)))
        self.assertEqual(results, ['forecast'] * 20)
        loader.assert_called_once()

    def test_restart_refetches_and_location_keys_are_distinct(self):
        loader = Mock(return_value='forecast')
        a = ReadCache()
        a.get('kent', 300, loader)
        a.get('other', 300, loader)
        ReadCache().get('kent', 300, loader)
        self.assertEqual(loader.call_count, 3)
