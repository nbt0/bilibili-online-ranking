import json
import tempfile
import unittest
import time
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import call, patch

from video_ranking import (
    BilibiliCrawler,
    DEFAULT_USER_AGENT,
    PUBLIC_RANKING_SOURCES,
    PUBLIC_VIDEO_FIELDS,
    PublicSourceUnavailableError,
    main,
    parse_arguments,
)
from validate_public_data import (
    PublicDataValidationError,
    validate_public_data,
)


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


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SequenceSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return next(self.responses)


class BilibiliCrawlerTests(unittest.TestCase):
    @staticmethod
    def make_public_record():
        return {
            'cid': 123,
            'title': '标题',
            'pic': 'https://example.test/cover.jpg',
            'owner': 'UP',
            'mid': '456',
            'view': 100,
            'danmaku': 10,
            'online_count': '3000',
            'count_num': 3000,
            'url': 'https://www.bilibili.com/video/BV1test',
            'updated_at': '2026-07-28T12:00:00+08:00',
        }

    def test_cli_separates_output_and_previous_data_paths(self):
        arguments = parse_arguments(
            [
                '--output',
                '_work/public-ranking.json',
                '--previous-data',
                'data.json',
            ]
        )
        crawler = BilibiliCrawler(
            output_path=arguments.output,
            previous_data_path=arguments.previous_data,
        )
        self.assertEqual(
            crawler.output_path,
            Path('_work/public-ranking.json'),
        )
        self.assertEqual(crawler.previous_data_path, Path('data.json'))

    def test_loads_previous_data_from_path_separate_from_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous_path = root / 'previous.json'
            output_path = root / '_work' / 'public-ranking.json'
            previous_data = {'BV1test': self.make_public_record()}
            previous_path.write_text(
                json.dumps(previous_data, ensure_ascii=False),
                encoding='utf-8',
            )

            crawler = BilibiliCrawler(
                output_path=output_path,
                previous_data_path=previous_path,
            )
            crawler.load_previous_results()

            self.assertEqual(crawler.previous_results, previous_data)
            self.assertFalse(output_path.exists())

    def test_writes_public_ranking_to_work_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = (
                Path(directory) / '_work' / 'public-ranking.json'
            )
            data = {'BV1test': self.make_public_record()}
            crawler = BilibiliCrawler(output_path=output_path)

            crawler.write_public_data(data)

            self.assertTrue(output_path.is_file())
            self.assertEqual(
                json.loads(output_path.read_text(encoding='utf-8')),
                data,
            )

    def test_public_validator_rejects_internal_fields(self):
        for field in ('source', 'samples', 'online_total'):
            with self.subTest(field=field):
                record = self.make_public_record()
                record[field] = 'internal'
                with self.assertRaisesRegex(
                    PublicDataValidationError,
                    field,
                ):
                    validate_public_data({'BV1test': record})

    def test_anonymous_session_headers_do_not_inject_fixed_cookie(self):
        crawler = BilibiliCrawler()
        self.assertNotIn('Cookie', crawler.headers)
        self.assertEqual(crawler.headers['User-Agent'], DEFAULT_USER_AGENT)
        self.assertIn('Chrome/150.0.0.0', DEFAULT_USER_AGENT)

    def test_risk_control_uses_longer_backoff_then_recovers(self):
        crawler = BilibiliCrawler()
        crawler.thread_local.session = SequenceSession(
            [
                FakeResponse(
                    {
                        'code': -352,
                        'message': '风控校验失败',
                        'data': {'v_voucher': 'not-logged'},
                    }
                ),
                FakeResponse(
                    {
                        'code': -352,
                        'message': '风控校验失败',
                        'data': {},
                    }
                ),
                FakeResponse({'code': 0, 'data': {'list': []}}),
            ]
        )

        with patch('video_ranking.time.sleep') as sleep:
            with redirect_stdout(StringIO()) as output:
                data = crawler.request_json(
                    crawler.popular_api,
                    required_source='popular_all',
                )

        self.assertEqual(data['code'], 0)
        self.assertEqual(
            sleep.call_args_list,
            [call(2.0), call(5.0)],
        )
        self.assertIn('voucher=有', output.getvalue())
        self.assertNotIn('not-logged', output.getvalue())

    def test_required_source_failure_is_not_returned_as_empty_data(self):
        crawler = BilibiliCrawler()
        crawler.thread_local.session = SequenceSession(
            [
                FakeResponse(
                    {'code': -352, 'message': '风控校验失败'}
                )
                for _ in range(3)
            ]
        )

        with patch('video_ranking.time.sleep'):
            with redirect_stdout(StringIO()):
                with self.assertRaisesRegex(
                    PublicSourceUnavailableError,
                    'popular_all',
                ):
                    crawler.request_json(
                        crawler.popular_api,
                        required_source='popular_all',
                    )

    def test_cli_returns_failure_when_required_source_is_unavailable(self):
        with patch.object(
            BilibiliCrawler,
            'run',
            side_effect=PublicSourceUnavailableError('popular_all unavailable'),
        ):
            with redirect_stdout(StringIO()) as output:
                exit_code = main([])

        self.assertEqual(exit_code, 1)
        self.assertIn('未写入本轮结果', output.getvalue())

    def test_public_pgc_source_limits(self):
        limits = {
            source['key']: source['limit']
            for source in PUBLIC_RANKING_SOURCES
        }
        self.assertEqual(limits['anime'], 5)
        self.assertEqual(limits['guochuang'], 5)
        self.assertEqual(limits['movie'], 5)
        self.assertEqual(limits['tv'], 5)
        self.assertEqual(limits['variety'], 5)

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

    def test_anime_selects_latest_two_first_two_and_latest_trailer(self):
        now = int(time.time())
        season_data = {
            'new_ep': {'id': 4},
            'episodes': [
                {
                    'id': 1,
                    'bvid': 'BV1mainFirst',
                    'cid': 11,
                    'pub_time': now - 400,
                },
                {
                    'id': 2,
                    'bvid': 'BV1mainSecond',
                    'cid': 22,
                    'pub_time': now - 300,
                },
                {
                    'id': 3,
                    'bvid': 'BV1mainPrevious',
                    'cid': 33,
                    'pub_time': now - 200,
                },
                {
                    'id': 4,
                    'bvid': 'BV1mainLatest',
                    'cid': 44,
                    'pub_time': now - 100,
                },
                {
                    'id': 5,
                    'bvid': 'BV1mainFuture',
                    'cid': 55,
                    'pub_time': now + 3600,
                },
            ],
            'section': [
                {
                    'title': 'PV花絮',
                    'type': 1,
                    'episodes': [
                        {
                            'id': 10,
                            'bvid': 'BV1trailerOld',
                            'cid': 101,
                            'pub_time': now - 250,
                            'title': '第1集预告',
                        },
                        {
                            'id': 11,
                            'bvid': 'BV1trailerLatest',
                            'cid': 102,
                            'pub_time': now - 50,
                            'title': '正式宣传片',
                        },
                        {
                            'id': 12,
                            'bvid': 'BV1event',
                            'cid': 103,
                            'pub_time': now - 10,
                            'title': '线下漫展活动',
                        },
                    ],
                },
                {
                    'title': '高能看点',
                    'type': 2,
                    'episodes': [
                        {
                            'id': 13,
                            'bvid': 'BV1bonus',
                            'cid': 104,
                            'pub_time': now - 10,
                        },
                    ],
                },
            ],
        }

        selected = BilibiliCrawler.select_pgc_episodes(
            season_data,
            'anime',
        )

        self.assertEqual(
            [episode['bvid'] for episode in selected],
            [
                'BV1mainLatest',
                'BV1mainPrevious',
                'BV1mainFirst',
                'BV1mainSecond',
                'BV1trailerLatest',
            ],
        )

    def test_movie_keeps_only_latest_episode_without_trailer(self):
        now = int(time.time())
        selected = BilibiliCrawler.select_pgc_episodes(
            {
                'episodes': [
                    {
                        'id': 1,
                        'bvid': 'BV1movieOld',
                        'cid': 11,
                        'pub_time': now - 200,
                    },
                    {
                        'id': 2,
                        'bvid': 'BV1movieLatest',
                        'cid': 22,
                        'pub_time': now - 100,
                    },
                ],
                'section': [
                    {
                        'title': 'PV',
                        'type': 1,
                        'episodes': [
                            {
                                'id': 3,
                                'bvid': 'BV1movieTrailer',
                                'cid': 33,
                                'pub_time': now - 50,
                            },
                        ],
                    },
                ],
            },
            'movie',
        )

        self.assertEqual(
            [episode['bvid'] for episode in selected],
            ['BV1movieLatest'],
        )

    def test_tv_selects_latest_two_episodes_without_trailer(self):
        now = int(time.time())
        selected = BilibiliCrawler.select_pgc_episodes(
            {
                'episodes': [
                    {
                        'id': 1,
                        'bvid': 'BV1tvFirst',
                        'cid': 11,
                        'pub_time': now - 400,
                    },
                    {
                        'id': 2,
                        'bvid': 'BV1tvSecond',
                        'cid': 22,
                        'pub_time': now - 300,
                    },
                    {
                        'id': 3,
                        'bvid': 'BV1tvPrevious',
                        'cid': 33,
                        'pub_time': now - 200,
                    },
                    {
                        'id': 4,
                        'bvid': 'BV1tvLatest',
                        'cid': 44,
                        'pub_time': now - 100,
                    },
                ],
                'section': [
                    {
                        'title': '预告',
                        'type': 1,
                        'episodes': [
                            {
                                'id': 5,
                                'bvid': 'BV1tvTrailer',
                                'cid': 55,
                                'pub_time': now - 50,
                            },
                        ],
                    },
                ],
            },
            'tv',
        )

        self.assertEqual(
            [episode['bvid'] for episode in selected],
            [
                'BV1tvLatest',
                'BV1tvPrevious',
                'BV1tvFirst',
                'BV1tvSecond',
            ],
        )


if __name__ == '__main__':
    unittest.main()
