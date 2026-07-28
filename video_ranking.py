import json
import math
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from statistics import median

import requests


PUBLIC_VIDEO_FIELDS = (
    'cid',
    'title',
    'pic',
    'owner',
    'mid',
    'view',
    'danmaku',
    'online_count',
    'count_num',
    'url',
    'updated_at',
)

PUBLIC_RANKING_SOURCES = (
    {
        'key': 'rank_all',
        'kind': 'ugc_rank',
        'limit': 100,
        'referer': 'https://www.bilibili.com/v/popular/rank/all',
    },
    {
        'key': 'popular_all',
        'kind': 'popular',
        'limit': 100,
        'referer': 'https://www.bilibili.com/v/popular/all',
    },
    {
        'key': 'anime',
        'kind': 'pgc_rank',
        'limit': 30,
        'season_type': 1,
        'endpoint': '/pgc/web/rank/list',
        'referer': 'https://www.bilibili.com/v/popular/rank/anime',
    },
    {
        'key': 'guochuang',
        'kind': 'pgc_rank',
        'limit': 30,
        'season_type': 4,
        'endpoint': '/pgc/season/rank/web/list',
        'referer': 'https://www.bilibili.com/v/popular/rank/guochuang',
    },
    {
        'key': 'movie',
        'kind': 'pgc_rank',
        'limit': 30,
        'season_type': 2,
        'endpoint': '/pgc/season/rank/web/list',
        'referer': 'https://www.bilibili.com/v/popular/rank/movie',
    },
    {
        'key': 'variety',
        'kind': 'pgc_rank',
        'limit': 30,
        'season_type': 7,
        'endpoint': '/pgc/season/rank/web/list',
        'referer': 'https://www.bilibili.com/v/popular/rank/variety',
    },
)


class BilibiliCrawler:
    def __init__(
        self,
        max_workers=4,
        samples_per_video=1,
        anomaly_extra_samples=2,
        request_interval=0.0,
        output_path='data.json',
    ):
        # 设置请求头，模拟浏览器访问
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Referer': 'https://www.bilibili.com/v/popular/rank/all',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Origin': 'https://www.bilibili.com',
            'Cookie': 'buvid3=2D4B09A5-0E5F-4537-9F7C-E293CE7324F7167646infoc'
        }
        # API接口地址
        self.api_root = 'https://api.bilibili.com'
        self.online_count_api = 'https://api.bilibili.com/x/player/online/total'  # 在线人数API
        self.ranking_api = 'https://api.bilibili.com/x/web-interface/ranking/v2'
        self.popular_api = 'https://api.bilibili.com/x/web-interface/popular'
        self.pagelist_api = 'https://api.bilibili.com/x/player/pagelist'
        self.video_info_api = 'https://api.bilibili.com/x/web-interface/view'
        self.pgc_season_api = 'https://api.bilibili.com/pgc/view/web/season'
        self.max_workers = max(1, min(max_workers, 16))
        self.samples_per_video = max(1, samples_per_video)
        self.anomaly_extra_samples = max(0, anomaly_extra_samples)
        self.request_interval = max(0.0, request_interval)
        self.request_timeout = (5, 10)
        self.output_path = Path(output_path)
        self.thread_local = threading.local()
        self.request_lock = threading.Lock()
        self.next_request_at = 0.0
        self.results = {}  # 存储结果
        self.previous_results = {}

    def get_session(self):
        """为每个工作线程创建并复用独立的HTTP会话"""
        if not hasattr(self.thread_local, 'session'):
            session = requests.Session()
            session.headers.update(self.headers)
            self.thread_local.session = session
        return self.thread_local.session

    def wait_for_request_slot(self):
        """限制整个爬虫的请求发起速率，减少瞬时请求峰值"""
        if self.request_interval <= 0:
            return

        with self.request_lock:
            now = time.monotonic()
            delay = max(0.0, self.next_request_at - now)
            self.next_request_at = (
                max(now, self.next_request_at) + self.request_interval
            )
        if delay:
            time.sleep(delay)

    def request_json(self, url, *, params=None, referer=None):
        """请求公开 JSON API；失败两次后返回 None"""
        headers = {'Referer': referer} if referer else None
        last_error = None
        for attempt in range(2):
            self.wait_for_request_slot()
            try:
                response = self.get_session().get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.request_timeout,
                )
                response.raise_for_status()
                data = response.json()
                if data.get('code') != 0:
                    raise ValueError(
                        f"code={data.get('code')}, "
                        f"message={data.get('message')}"
                    )
                return data
            except (requests.RequestException, ValueError) as error:
                last_error = error
                if attempt == 0:
                    time.sleep(0.3)

        print(f"请求失败: {url}: {last_error}")
        return None

    def get_ranking_videos(self):
        """兼容旧调用：获取全站排行榜前100"""
        data = self.request_json(
            self.ranking_api,
            params={'rid': 0, 'type': 'all'},
            referer='https://www.bilibili.com/v/popular/rank/all',
        )
        return ((data or {}).get('data') or {}).get('list', [])

    @staticmethod
    def value_is_missing(value):
        return value is None or value == ''

    @staticmethod
    def safe_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def normalize_ugc_candidate(cls, item, source):
        """将普通排行榜/热门视频统一为内部候选结构"""
        bvid = str(item.get('bvid') or '').strip()
        if not bvid:
            return None

        owner = item.get('owner') or {}
        stat = item.get('stat') or {}
        return {
            'bvid': bvid,
            'aid': item.get('aid'),
            'cid': item.get('cid'),
            'videos': max(1, cls.safe_int(item.get('videos'))),
            'season_id': None,
            'ep_id': None,
            'title': str(item.get('title') or ''),
            'pic': str(item.get('pic') or ''),
            'owner': str(owner.get('name') or ''),
            'mid': str(owner.get('mid') or ''),
            'view': cls.safe_int(stat.get('view')),
            'danmaku': cls.safe_int(stat.get('danmaku')),
            'sources': [source],
        }

    @classmethod
    def merge_candidate(cls, existing, incoming):
        """合并同一 BV 的候选，优先保留已经存在的完整字段"""
        if not existing:
            merged = dict(incoming)
            merged['sources'] = list(dict.fromkeys(incoming.get('sources', [])))
            return merged

        merged = dict(existing)
        for key, value in incoming.items():
            if key == 'sources':
                merged[key] = list(
                    dict.fromkeys(
                        list(merged.get(key, [])) + list(value or [])
                    )
                )
            elif cls.value_is_missing(merged.get(key)) and not cls.value_is_missing(value):
                merged[key] = value
        return merged

    @classmethod
    def deduplicate_candidates(cls, candidates):
        """按 BV 去重，并合并不同排行榜提供的字段"""
        deduplicated = {}
        for candidate in candidates:
            if not candidate:
                continue
            bvid = str(candidate.get('bvid') or '').strip()
            if not bvid:
                continue
            deduplicated[bvid] = cls.merge_candidate(
                deduplicated.get(bvid),
                candidate,
            )
        return deduplicated

    def fetch_popular_videos(self, limit=100):
        """分页获取热门综合视频，当前接口单页最多使用50条"""
        videos = []
        page_size = min(50, limit)
        page_number = 1
        while len(videos) < limit:
            data = self.request_json(
                self.popular_api,
                params={'pn': page_number, 'ps': page_size},
                referer='https://www.bilibili.com/v/popular/all',
            )
            payload = (data or {}).get('data') or {}
            page = payload.get('list') or []
            if not page:
                break
            videos.extend(page)
            if payload.get('no_more') or len(page) < page_size:
                break
            page_number += 1
        return videos[:limit]

    def fetch_pgc_rank_items(self, source):
        """获取番剧、国创、电影或综艺的 season 排行"""
        data = self.request_json(
            f"{self.api_root}{source['endpoint']}",
            params={'day': 3, 'season_type': source['season_type']},
            referer=source['referer'],
        )
        payload = (data or {}).get('data') or (data or {}).get('result') or {}
        return (payload.get('list') or [])[:source['limit']]

    @staticmethod
    def select_pgc_episode(season_data):
        """优先选择 season 标记的最新 episode"""
        episodes = [
            episode
            for episode in season_data.get('episodes') or []
            if episode.get('bvid') and episode.get('cid')
        ]
        if not episodes:
            return None

        newest_ep_id = (season_data.get('new_ep') or {}).get('id')
        if newest_ep_id:
            for episode in episodes:
                if episode.get('id') == newest_ep_id:
                    return episode

        now = int(time.time())
        released = [
            episode
            for episode in episodes
            if BilibiliCrawler.safe_int(episode.get('pub_time')) <= now + 300
        ]
        candidates = released or episodes
        return max(
            candidates,
            key=lambda episode: (
                BilibiliCrawler.safe_int(episode.get('pub_time')),
                BilibiliCrawler.safe_int(episode.get('id')),
            ),
        )

    def resolve_pgc_candidate(self, task):
        """把 PGC season 排行项解析为可以查询在线人数的 episode"""
        source, rank_item = task
        season_id = rank_item.get('season_id')
        if not season_id:
            return None

        data = self.request_json(
            self.pgc_season_api,
            params={'season_id': season_id},
            referer=source['referer'],
        )
        season_data = (data or {}).get('result') or {}
        episode = self.select_pgc_episode(season_data)
        if not episode:
            print(f"跳过 season {season_id}: 没有可用 episode")
            return None

        stat = rank_item.get('stat') or season_data.get('stat') or {}
        up_info = season_data.get('up_info') or {}
        return {
            'bvid': str(episode.get('bvid') or ''),
            'aid': episode.get('aid'),
            'cid': episode.get('cid'),
            'videos': 1,
            'season_id': season_id,
            'ep_id': episode.get('id') or episode.get('ep_id'),
            'title': str(
                rank_item.get('title')
                or season_data.get('title')
                or episode.get('long_title')
                or episode.get('title')
                or ''
            ),
            'pic': str(
                rank_item.get('cover')
                or season_data.get('cover')
                or episode.get('cover')
                or ''
            ),
            'owner': str(up_info.get('uname') or ''),
            'mid': str(up_info.get('mid') or ''),
            'view': self.safe_int(stat.get('view')),
            'danmaku': self.safe_int(stat.get('danmaku')),
            'sources': [source['key']],
        }

    def get_public_candidates(self):
        """从六个公开排行榜组装并去重候选"""
        candidates = []
        pgc_tasks = []

        for source in PUBLIC_RANKING_SOURCES:
            if source['kind'] == 'ugc_rank':
                items = self.get_ranking_videos()[:source['limit']]
                candidates.extend(
                    self.normalize_ugc_candidate(item, source['key'])
                    for item in items
                )
                print(f"公开来源 {source['key']}: {len(items)} 条")
            elif source['kind'] == 'popular':
                items = self.fetch_popular_videos(source['limit'])
                candidates.extend(
                    self.normalize_ugc_candidate(item, source['key'])
                    for item in items
                )
                print(f"公开来源 {source['key']}: {len(items)} 条")
            elif source['kind'] == 'pgc_rank':
                items = self.fetch_pgc_rank_items(source)
                pgc_tasks.extend((source, item) for item in items)
                print(f"公开来源 {source['key']}: {len(items)} 个 season")

        if pgc_tasks:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                for candidate in executor.map(
                    self.resolve_pgc_candidate,
                    pgc_tasks,
                ):
                    if candidate:
                        candidates.append(candidate)

        deduplicated = self.deduplicate_candidates(candidates)
        print(
            f"公开候选原始 {len([item for item in candidates if item])} 条，"
            f"按 BV 去重后 {len(deduplicated)} 条"
        )
        return deduplicated

    def load_previous_results(self):
        """读取上一轮结果，用于在接口异常时保留可靠值"""
        try:
            with self.output_path.open('r', encoding='utf-8') as file:
                data = json.load(file)
            if isinstance(data, dict):
                self.previous_results = data
                print(f"读取到上一轮 {len(data)} 条数据")
                return
        except (OSError, ValueError) as error:
            print(f"读取上一轮数据失败: {error}")

        self.previous_results = {}

    @staticmethod
    def parse_total_bounds(total_text):
        """将总人数转换为闭区间，兼容 460、2000+、2.3万+"""
        text = str(total_text or '').replace('人在看', '').strip()
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(万)?(\+)?', text)
        if not match:
            return None

        number_text, unit, has_plus = match.groups()
        if unit == '万':
            lower = int(float(number_text) * 10000)
            decimal_places = len(number_text.split('.')[1]) if '.' in number_text else 0
            step = max(1, int(10000 / (10 ** decimal_places)))
        else:
            lower = int(float(number_text))
            step = 10 ** max(len(str(lower)) - 1, 0)

        if not has_plus:
            return lower, lower
        return lower, lower + step - 1

    @staticmethod
    def count_distance_to_bounds(count, bounds):
        """计算精确值到 total 区间的距离"""
        if not bounds:
            return 0
        if count < bounds[0]:
            return bounds[0] - count
        if count > bounds[1]:
            return count - bounds[1]
        return 0

    @staticmethod
    def total_distance_tolerance(bounds):
        """允许 count 在 total 区间外有少量波动"""
        if not bounds:
            return None
        return max(10, math.ceil(bounds[0] * 0.20))

    @staticmethod
    def totals_are_compatible(current_bounds, previous_bounds):
        """判断两轮模糊总人数区间是否重叠"""
        if not current_bounds or not previous_bounds:
            return True
        return not (
            current_bounds[1] < previous_bounds[0]
            or previous_bounds[1] < current_bounds[0]
        )

    def select_online_count(self, samples, previous_info=None):
        """仅根据 count 的稳定性和上一轮值选择实际在线人数"""
        previous_info = previous_info or {}
        totals = [sample['total'] for sample in samples if sample.get('total')]
        total_text = (
            Counter(totals).most_common(1)[0][0]
            if totals
            else str(previous_info.get('online_total') or '')
        )

        counts = [
            sample['count']
            for sample in samples
            if isinstance(sample.get('count'), int) and sample['count'] > 0
        ]

        previous_count = self.convert_count_to_number(
            previous_info.get('online_count')
        )
        if not counts:
            if previous_count > 0:
                return str(previous_count), total_text, True
            return None, total_text, False

        if len(counts) == 1:
            return str(counts[0]), total_text, False

        clusters = []
        for anchor in counts:
            cluster = [
                count
                for count in counts
                if abs(count - anchor)
                <= max(5, math.ceil(max(count, anchor) * 0.15))
            ]
            if len(cluster) >= 2:
                clusters.append(cluster)

        if clusters:
            cluster = max(
                clusters,
                key=lambda values: (
                    len(values),
                    -max(values) + min(values),
                ),
            )
            candidate = round(median(cluster))
        elif previous_count > 0:
            candidate = min(
                counts,
                key=lambda count: abs(count - previous_count),
            )
        else:
            candidate = round(median(counts))

        return str(candidate), total_text, False

    def sample_is_suspicious(
        self,
        sample,
        previous_info=None,
        use_total_hint=True,
    ):
        """判断首个样本是否需要追加采样"""
        if not sample:
            return True

        previous_info = previous_info or {}
        count = sample.get('count')
        if not isinstance(count, int) or count <= 0:
            return True

        # total 不是实际在线人数，只用于发现疑似上游节点返回的极端低值。
        if use_total_hint:
            total_bounds = self.parse_total_bounds(sample.get('total'))
            if (
                total_bounds
                and total_bounds[0] >= 100
                and count < max(1, math.ceil(total_bounds[0] * 0.02))
            ):
                return True

        previous_count = self.convert_count_to_number(
            previous_info.get('online_count')
        )
        if previous_count > 0:
            change_ratio = abs(count - previous_count) / previous_count
            if change_ratio > 0.60:
                return True

        return False

    def fetch_online_sample(self, bvid, cid):
        """请求一次在线人数样本；失败时返回 None"""
        params = {
            'bvid': bvid,
            'cid': cid,
            '_': time.time_ns()
        }

        for attempt in range(2):
            self.wait_for_request_slot()
            try:
                response = self.get_session().get(
                    self.online_count_api,
                    params=params,
                    timeout=self.request_timeout,
                    headers={'Cache-Control': 'no-cache'}
                )
                response.raise_for_status()
                data = response.json()

                if data.get('code') != 0:
                    raise ValueError(f"B站接口返回 code={data.get('code')}")

                response_data = data.get('data') or {}
                online_count = str(response_data.get('count', ''))
                if not online_count.isdigit():
                    raise ValueError("接口未返回精确在线人数")

                return {
                    'count': int(online_count),
                    'total': str(response_data.get('total', ''))
                }
            except (requests.RequestException, ValueError) as error:
                if attempt == 1:
                    print(f"获取 {bvid} 单次样本失败: {error}")
                time.sleep(0.15 * (attempt + 1))

        return None

    def get_online_decision(
        self,
        bvid,
        cid,
        previous_info=None,
        allow_total_fallback=True,
        use_total_hint=True,
    ):
        """采样并返回指定 CID 的实际 count 决策"""
        samples = []
        for sample_index in range(self.samples_per_video):
            sample = self.fetch_online_sample(bvid, cid)
            if sample:
                samples.append(sample)
            if sample_index + 1 < self.samples_per_video:
                time.sleep(0.05)

        first_sample = samples[0] if samples else None
        if self.sample_is_suspicious(
            first_sample,
            previous_info,
            use_total_hint=use_total_hint,
        ):
            for extra_index in range(self.anomaly_extra_samples):
                sample = self.fetch_online_sample(bvid, cid)
                if sample:
                    samples.append(sample)
                if extra_index + 1 < self.anomaly_extra_samples:
                    time.sleep(0.05)

        online_count, total_text, used_previous = self.select_online_count(
            samples,
            previous_info
        )
        raw_counts = [sample['count'] for sample in samples]

        count_source = 'previous_reliable' if used_previous else 'sample'
        confidence = 'medium' if used_previous else 'high'
        count_num = self.convert_count_to_number(online_count)

        if online_count is None:
            print(f"放弃 {bvid} CID {cid}: 无 count 样本或上一轮值")
        elif used_previous:
            print(
                f"保留 {bvid} CID {cid} 上一轮值 {online_count}: "
                f"本轮样本={raw_counts}, total={total_text or '未知'}"
            )
        elif len(set(raw_counts)) > 1 and any(
            abs(count - count_num) > max(10, count_num * 0.15)
            for count in raw_counts
        ):
            print(
                f"过滤 {bvid} CID {cid} 异常样本: "
                f"{raw_counts} -> {online_count}, total={total_text or '未知'}"
            )

        return {
            'online_count': online_count,
            'count_num': count_num,
            'online_total': total_text,
            'count_source': count_source if online_count is not None else None,
            'confidence': confidence if online_count is not None else 'none',
            'samples': samples,
            'used_previous': used_previous,
        }

    def get_online_count(self, bvid, cid, previous_info=None):
        """兼容私有实验脚本的旧返回格式，不在此层启用 total 兜底"""
        decision = self.get_online_decision(
            bvid,
            cid,
            previous_info,
            allow_total_fallback=False,
        )
        return decision['online_count'], decision['online_total']

    def convert_count_to_number(self, count_str):
        """将精确在线人数字符串转换为整数
        
        Args:
            count_str: 精确在线人数字符串，如 "2389"
        
        Returns:
            int: 转换后的具体数字
        """
        try:
            return int(count_str)
        except (TypeError, ValueError):
            return 0

    def fetch_first_cid(self, bvid):
        """只在候选缺少 CID 时查询第一分P"""
        cids = self.fetch_page_cids(bvid)
        return cids[0] if cids else None

    def fetch_page_cids(self, bvid):
        """获取 BV 的完整分P CID列表"""
        data = self.request_json(
            self.pagelist_api,
            params={'bvid': bvid},
            referer=f'https://www.bilibili.com/video/{bvid}',
        )
        pages = (data or {}).get('data') or []
        return list(
            dict.fromkeys(
                page['cid']
                for page in pages
                if page.get('cid')
            )
        )

    def resolve_candidate_cids(self, candidate):
        """单P走榜单快速路径，多P或未知P数读取完整 pagelist"""
        first_cid = candidate.get('cid')
        video_count = self.safe_int(candidate.get('videos'))
        if video_count == 1 and first_cid:
            return [first_cid]

        cids = self.fetch_page_cids(candidate['bvid'])
        if cids:
            return cids
        return [first_cid] if first_cid else []

    def collect_video(self, candidate):
        """查询所有分P的实际 count，并按 BV 求和"""
        bvid = candidate['bvid']
        cids = self.resolve_candidate_cids(candidate)
        if not cids:
            print(f"跳过 {bvid}: 没有可用 CID")
            return bvid, None

        previous_info = self.previous_results.get(bvid) or {}
        page_decisions = []
        for cid in cids:
            decision = self.get_online_decision(
                bvid,
                cid,
                previous_info if len(cids) == 1 else None,
                allow_total_fallback=False,
                use_total_hint=len(cids) == 1,
            )
            if decision['online_count'] is not None:
                page_decisions.append((cid, decision))

        if len(page_decisions) != len(cids):
            previous_count = self.convert_count_to_number(
                previous_info.get('online_count')
            )
            if previous_count <= 0:
                print(
                    f"跳过 {bvid}: "
                    f"仅获得 {len(page_decisions)}/{len(cids)} 个分P人数"
                )
                return bvid, None
            online_count = previous_count
            count_source = 'previous_reliable'
            confidence = 'low'
            used_previous = True
        else:
            online_count = sum(
                decision['count_num']
                for _, decision in page_decisions
            )
            count_source = (
                page_decisions[0][1]['count_source']
                if len(page_decisions) == 1
                else 'page_count_sum'
            )
            confidence = (
                'high'
                if all(
                    decision['confidence'] == 'high'
                    for _, decision in page_decisions
                )
                else 'medium'
            )
            used_previous = any(
                decision['used_previous']
                for _, decision in page_decisions
            )

        result = dict(candidate)
        result.update({
            'cid': cids[0],
            'cids': cids,
            'page_count': len(cids),
            'online_count': str(online_count),
            'count_num': online_count,
            'online_total': (
                page_decisions[0][1]['online_total']
                if page_decisions
                else ''
            ),
            'count_source': count_source,
            'confidence': confidence,
            'samples': [
                {
                    'cid': cid,
                    'samples': decision['samples'],
                }
                for cid, decision in page_decisions
            ],
            'used_previous': used_previous,
        })
        return bvid, result

    def hydrate_video_metadata(self, candidate):
        """只为前100中字段不完整的候选补充视频详情"""
        required = ('title', 'pic', 'owner', 'mid')
        is_pgc_episode = bool(candidate.get('season_id'))
        if (
            not is_pgc_episode
            and all(
                not self.value_is_missing(candidate.get(key))
                for key in required
            )
        ):
            return candidate

        bvid = candidate['bvid']
        data = self.request_json(
            self.video_info_api,
            params={'bvid': bvid},
            referer=f'https://www.bilibili.com/video/{bvid}',
        )
        item = (data or {}).get('data') or {}
        if not item:
            return candidate

        hydrated = dict(candidate)
        owner = item.get('owner') or {}
        stat = item.get('stat') or {}
        additions = {
            'title': item.get('title'),
            'pic': item.get('pic'),
            'owner': owner.get('name'),
            'mid': str(owner.get('mid') or ''),
            'view': self.safe_int(stat.get('view')),
            'danmaku': self.safe_int(stat.get('danmaku')),
        }
        for key, value in additions.items():
            if (
                not self.value_is_missing(value)
                and (
                    is_pgc_episode
                    or self.value_is_missing(hydrated.get(key))
                )
            ):
                hydrated[key] = value
        return hydrated

    @classmethod
    def build_public_record(cls, candidate, updated_at):
        """显式白名单生成单条公开数据"""
        values = {
            'cid': candidate.get('cid'),
            'title': str(candidate.get('title') or ''),
            'pic': str(candidate.get('pic') or '').replace('http://', 'https://', 1),
            'owner': str(candidate.get('owner') or ''),
            'mid': str(candidate.get('mid') or ''),
            'view': cls.safe_int(candidate.get('view')),
            'danmaku': cls.safe_int(candidate.get('danmaku')),
            'online_count': str(candidate.get('online_count') or ''),
            'count_num': cls.safe_int(candidate.get('count_num')),
            'url': f"https://www.bilibili.com/video/{candidate['bvid']}",
            'updated_at': updated_at,
        }
        return {key: values[key] for key in PUBLIC_VIDEO_FIELDS}

    def write_public_data(self, data):
        """原子写入公开 JSON，避免中途失败留下半文件"""
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.output_path.with_suffix(
            self.output_path.suffix + '.tmp'
        )
        with temp_path.open('w', encoding='utf-8', newline='\n') as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write('\n')
        temp_path.replace(self.output_path)

    def display_ranking(self):
        # 按在线人数排序
        sorted_videos = sorted(self.results.items(), key=lambda x: x[1]['count_num'], reverse=True)
        
        print("\n=== B站视频实时在线人数排行榜 ===")
        print(f"更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        for i, (bvid, info) in enumerate(sorted_videos[:20], 1):  # 只显示前20个
            print(f"{i:2d}. {info['online_count']:>8} | {info['title'][:30]:30} | UP主: {info['owner']}")
        
        print("\n" + "="*50)

    def run(self):
        """主运行函数"""
        started_at = time.perf_counter()
        self.load_previous_results()
        candidates = self.get_public_candidates()
        videos = list(candidates.values())
        print(f"获取到 {len(videos)} 个去重候选")

        if not videos:
            print("没有可更新的视频，保留现有 data.json")
            return False

        worker_count = min(self.max_workers, len(videos))
        print(f"使用 {worker_count} 个并发任务获取精确在线人数")

        self.results = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for completed, (bvid, info) in enumerate(
                executor.map(self.collect_video, videos),
                1
            ):
                if info is not None:
                    self.results[bvid] = info
                if completed % 10 == 0 or completed == len(videos):
                    print(f"采集进度: {completed}/{len(videos)}")

        if not self.results:
            print("没有获得可靠数据，保留现有 data.json")
            return False

        ranked = sorted(
            self.results.values(),
            key=lambda item: (
                item.get('count_num', 0),
                item.get('view', 0),
                item.get('bvid', ''),
            ),
            reverse=True,
        )[:100]

        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, len(ranked))
        ) as executor:
            ranked = list(executor.map(self.hydrate_video_metadata, ranked))

        updated_at = datetime.now().astimezone().isoformat(timespec='seconds')
        self.results = {
            item['bvid']: self.build_public_record(item, updated_at)
            for item in ranked
        }
        self.write_public_data(self.results)

        elapsed = time.perf_counter() - started_at
        print(f"数据采集完成，耗时 {elapsed:.2f} 秒")
        self.display_ranking()
        return True

if __name__ == '__main__':
    crawler = BilibiliCrawler(request_interval=0.08)
    crawler.run()
