import unittest

from video_ranking import BilibiliCrawler, PUBLIC_VIDEO_FIELDS


class StubCrawler(BilibiliCrawler):
    def __init__(self, samples):
        super().__init__(
            max_workers=1,
            samples_per_video=1,
            anomaly_extra_samples=2,
        )
        self.stub_samples = iter(samples)

    def fetch_online_sample(self, bvid, cid):
        return next(self.stub_samples, None)


class MetadataCrawler(BilibiliCrawler):
    def request_json(self, url, *, params=None, referer=None):
        return {
            'code': 0,
            'data': {
                'title': '单集标题',
                'pic': 'https://example.test/episode.jpg',
                'owner': {'name': '单集UP', 'mid': 456},
                'stat': {'view': 1234, 'danmaku': 56},
            },
        }


class PageCrawler(BilibiliCrawler):
    def __init__(self):
        super().__init__(max_workers=1)
        self.pagelist_calls = 0

    def fetch_page_cids(self, bvid):
        self.pagelist_calls += 1
        return [11, 22, 33]


class SumCrawler(PageCrawler):
    def get_online_decision(
        self,
        bvid,
        cid,
        previous_info=None,
        allow_total_fallback=True,
        use_total_hint=True,
    ):
        return {
            'online_count': str(cid),
            'count_num': cid,
            'online_total': '9999+',
            'count_source': 'sample',
            'confidence': 'high',
            'samples': [{'count': cid, 'total': '9999+'}],
            'used_previous': False,
        }


class BilibiliCrawlerTests(unittest.TestCase):
    def test_parse_total_bounds(self):
        self.assertEqual(
            BilibiliCrawler.parse_total_bounds('3000+'),
            (3000, 3999),
        )
        self.assertEqual(
            BilibiliCrawler.parse_total_bounds('2.3万+'),
            (23000, 23999),
        )
        self.assertEqual(
            BilibiliCrawler.parse_total_bounds('460'),
            (460, 460),
        )

    def test_single_count_is_actual_even_when_total_is_much_higher(self):
        crawler = BilibiliCrawler()
        selected, total, used_previous = crawler.select_online_count(
            [{'count': 2, 'total': '3000+'}]
        )
        self.assertEqual(selected, '2')
        self.assertEqual(total, '3000+')
        self.assertFalse(used_previous)

    def test_selects_stable_count_cluster_without_using_total(self):
        crawler = BilibiliCrawler()
        selected, _, used_previous = crawler.select_online_count(
            [
                {'count': 1, 'total': '3000+'},
                {'count': 636, 'total': '3000+'},
                {'count': 640, 'total': '3000+'},
            ]
        )
        self.assertEqual(selected, '638')
        self.assertFalse(used_previous)

    def test_stable_low_count_is_not_replaced_by_total(self):
        crawler = StubCrawler(
            [
                {'count': 2, 'total': '76'},
                {'count': 2, 'total': '76'},
                {'count': 2, 'total': '76'},
            ]
        )
        decision = crawler.get_online_decision(
            'BV1test',
            123,
            use_total_hint=False,
        )
        self.assertEqual(decision['online_count'], '2')
        self.assertEqual(decision['count_num'], 2)
        self.assertEqual(decision['count_source'], 'sample')

    def test_previous_value_is_used_only_when_all_samples_fail(self):
        crawler = BilibiliCrawler()
        selected, _, used_previous = crawler.select_online_count(
            [],
            {'online_count': '3250'},
        )
        self.assertEqual(selected, '3250')
        self.assertTrue(used_previous)

    def test_deduplicates_and_merges_candidates(self):
        candidates = BilibiliCrawler.deduplicate_candidates(
            [
                {
                    'bvid': 'BV1same',
                    'cid': None,
                    'videos': 2,
                    'title': '标题',
                    'sources': ['rank_all'],
                },
                {
                    'bvid': 'BV1same',
                    'cid': 123,
                    'title': '',
                    'sources': ['popular_all'],
                },
            ]
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates['BV1same']['cid'], 123)
        self.assertEqual(candidates['BV1same']['title'], '标题')
        self.assertEqual(
            candidates['BV1same']['sources'],
            ['rank_all', 'popular_all'],
        )

    def test_single_part_uses_existing_cid_without_pagelist(self):
        crawler = PageCrawler()
        cids = crawler.resolve_candidate_cids(
            {'bvid': 'BV1test', 'videos': 1, 'cid': 99}
        )
        self.assertEqual(cids, [99])
        self.assertEqual(crawler.pagelist_calls, 0)

    def test_multi_part_uses_all_pagelist_cids(self):
        crawler = PageCrawler()
        cids = crawler.resolve_candidate_cids(
            {'bvid': 'BV1test', 'videos': 3, 'cid': 11}
        )
        self.assertEqual(cids, [11, 22, 33])
        self.assertEqual(crawler.pagelist_calls, 1)

    def test_multi_part_online_count_is_sum_of_all_cids(self):
        crawler = SumCrawler()
        bvid, result = crawler.collect_video(
            {
                'bvid': 'BV1test',
                'videos': 3,
                'cid': 11,
                'title': '标题',
                'sources': ['rank_all'],
            }
        )
        self.assertEqual(bvid, 'BV1test')
        self.assertEqual(result['cids'], [11, 22, 33])
        self.assertEqual(result['page_count'], 3)
        self.assertEqual(result['online_count'], '66')
        self.assertEqual(result['count_num'], 66)
        self.assertEqual(result['count_source'], 'page_count_sum')

    def test_public_record_uses_explicit_allowlist(self):
        record = BilibiliCrawler.build_public_record(
            {
                'bvid': 'BV1test',
                'cid': 123,
                'title': '标题',
                'pic': 'http://example.test/cover.jpg',
                'owner': 'UP',
                'mid': '456',
                'view': 100,
                'danmaku': 10,
                'online_count': '3000',
                'count_num': 3000,
                'sources': ['private-source'],
                'samples': [{'count': 1}],
                'confidence': 'low',
            },
            '2026-07-28T12:00:00+08:00',
        )
        self.assertEqual(tuple(record), PUBLIC_VIDEO_FIELDS)
        self.assertNotIn('sources', record)
        self.assertNotIn('samples', record)
        self.assertNotIn('confidence', record)
        self.assertEqual(record['pic'], 'https://example.test/cover.jpg')

    def test_pgc_metadata_is_hydrated_from_episode_bvid(self):
        crawler = MetadataCrawler()
        hydrated = crawler.hydrate_video_metadata(
            {
                'bvid': 'BV1test',
                'season_id': 123,
                'title': '季度标题',
                'pic': 'https://example.test/season.jpg',
                'owner': '季度UP',
                'mid': '1',
                'view': 9999,
                'danmaku': 999,
            }
        )
        self.assertEqual(hydrated['title'], '单集标题')
        self.assertEqual(hydrated['pic'], 'https://example.test/episode.jpg')
        self.assertEqual(hydrated['owner'], '单集UP')
        self.assertEqual(hydrated['mid'], '456')
        self.assertEqual(hydrated['view'], 1234)
        self.assertEqual(hydrated['danmaku'], 56)


if __name__ == '__main__':
    unittest.main()
