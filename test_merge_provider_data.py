import unittest

from merge_provider_data import (
    ProviderMergeError,
    merge_provider_data,
    validate_provider_data,
)
from validate_public_data import validate_public_data


UPDATED_AT = "2026-07-29T12:00:00+08:00"


def make_record(bvid, count, index=0):
    return {
        "cid": 1000 + index,
        "title": f"标题{index}",
        "pic": f"https://example.test/{index}.jpg",
        "owner": f"UP{index}",
        "mid": str(2000 + index),
        "view": 3000 + index,
        "danmaku": 400 + index,
        "online_count": str(count),
        "count_num": count,
        "url": f"https://www.bilibili.com/video/{bvid}",
        "updated_at": UPDATED_AT,
    }


def make_public_data(first_count=10000):
    return {
        f"BVpublic{index:03d}": make_record(
            f"BVpublic{index:03d}",
            first_count - index,
            index,
        )
        for index in range(100)
    }


def make_locked(start_count=20000):
    return {
        f"BVlocked{index:03d}": make_record(
            f"BVlocked{index:03d}",
            start_count - index,
            200 + index,
        )
        for index in range(20)
    }


def make_provider(locked=None, candidates=None):
    return {
        "schema_version": 1,
        "generated_at": UPDATED_AT,
        "locked": locked or {},
        "candidates": candidates or {},
    }


class ProviderMergeTests(unittest.TestCase):
    def test_locks_provider_prefix_and_fills_from_public(self):
        final_data = merge_provider_data(
            make_public_data(),
            make_provider(locked=make_locked()),
            UPDATED_AT,
        )
        self.assertEqual(
            list(final_data)[:20],
            [f"BVlocked{index:03d}" for index in range(20)],
        )
        self.assertEqual(len(final_data), 100)
        self.assertIs(validate_public_data(final_data), final_data)

    def test_fallback_candidates_join_generic_sorting(self):
        bvid = "BVfallback000"
        final_data = merge_provider_data(
            make_public_data(),
            make_provider(
                candidates={
                    bvid: make_record(bvid, 15000, 300),
                }
            ),
            UPDATED_AT,
        )
        self.assertEqual(next(iter(final_data)), bvid)
        self.assertEqual(len(final_data), 100)
        self.assertNotIn("BVpublic099", final_data)

    def test_locked_floor_filters_conflicting_public_overcount(self):
        public_data = make_public_data(first_count=19985)
        locked = make_locked(start_count=20000)
        final_data = merge_provider_data(
            public_data,
            make_provider(locked=locked),
            UPDATED_AT,
        )
        self.assertNotIn("BVpublic000", final_data)
        self.assertEqual(len(final_data), 100)
        self.assertLessEqual(
            final_data["BVpublic004"]["count_num"],
            locked["BVlocked019"]["count_num"],
        )

    def test_rejects_internal_provider_fields(self):
        bvid = "BVfallback000"
        record = make_record(bvid, 15000)
        record["source"] = "private"
        with self.assertRaises(ProviderMergeError):
            validate_provider_data(
                make_provider(candidates={bvid: record})
            )


if __name__ == "__main__":
    unittest.main()
