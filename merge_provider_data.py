"""Validate generic provider data and assemble the final public ranking."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from validate_public_data import (
    PublicDataValidationError,
    validate_public_data,
)
from video_ranking import PUBLIC_VIDEO_FIELDS


PROVIDER_SCHEMA_VERSION = 1
PROVIDER_FIELDS = frozenset(
    {"schema_version", "generated_at", "locked", "candidates"}
)
PUBLIC_VIDEO_FIELD_SET = frozenset(PUBLIC_VIDEO_FIELDS)
FINAL_SIZE = 100


class ProviderMergeError(ValueError):
    """Provider data or the resulting public ranking is invalid."""


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def validate_timestamp(value: Any) -> None:
    if not isinstance(value, str):
        raise ProviderMergeError("provider timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ProviderMergeError("provider timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderMergeError("provider timestamp is invalid")


def validate_provider_record(bvid: str, record: Any) -> None:
    if not isinstance(bvid, str) or not bvid.startswith("BV"):
        raise ProviderMergeError("provider BV is invalid")
    if (
        not isinstance(record, dict)
        or set(record) != PUBLIC_VIDEO_FIELD_SET
    ):
        raise ProviderMergeError("provider record fields are invalid")
    online_text = record.get("online_count")
    count_num = record.get("count_num")
    if (
        not isinstance(online_text, str)
        or not online_text.isdigit()
        or not isinstance(count_num, int)
        or isinstance(count_num, bool)
        or count_num <= 0
        or int(online_text) != count_num
    ):
        raise ProviderMergeError("provider online count is invalid")
    if record.get("cid") in (None, ""):
        raise ProviderMergeError("provider cid is invalid")
    for field in ("title", "owner", "mid"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise ProviderMergeError("provider metadata is incomplete")
    pic = record.get("pic")
    if not isinstance(pic, str) or not pic.startswith("https://"):
        raise ProviderMergeError("provider picture is invalid")
    if record.get("url") != f"https://www.bilibili.com/video/{bvid}":
        raise ProviderMergeError("provider url is invalid")
    validate_timestamp(record.get("updated_at"))


def validate_provider_data(data: Any) -> dict[str, Any]:
    """Validate the provider independently of private implementation code."""
    if not isinstance(data, dict) or set(data) != PROVIDER_FIELDS:
        raise ProviderMergeError("provider payload fields are invalid")
    if data.get("schema_version") != PROVIDER_SCHEMA_VERSION:
        raise ProviderMergeError("provider schema version is invalid")
    validate_timestamp(data.get("generated_at"))

    locked = data.get("locked")
    candidates = data.get("candidates")
    if not isinstance(locked, dict) or len(locked) not in (0, 20):
        raise ProviderMergeError("provider locked size is invalid")
    if not isinstance(candidates, dict) or len(candidates) > 500:
        raise ProviderMergeError("provider candidates size is invalid")
    if set(locked).intersection(candidates):
        raise ProviderMergeError("provider BV is duplicated")

    previous_count = None
    for bvid, record in locked.items():
        validate_provider_record(bvid, record)
        if (
            previous_count is not None
            and record["count_num"] > previous_count
        ):
            raise ProviderMergeError("provider locked order is invalid")
        previous_count = record["count_num"]
    for bvid, record in candidates.items():
        validate_provider_record(bvid, record)
    return data


def rebuild_public_record(
    bvid: str,
    record: Mapping[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    values = {
        "cid": record.get("cid"),
        "title": str(record.get("title") or ""),
        "pic": str(record.get("pic") or "").replace(
            "http://",
            "https://",
            1,
        ),
        "owner": str(record.get("owner") or ""),
        "mid": str(record.get("mid") or ""),
        "view": max(0, int(record.get("view") or 0)),
        "danmaku": max(0, int(record.get("danmaku") or 0)),
        "online_count": str(int(record.get("count_num") or 0)),
        "count_num": int(record.get("count_num") or 0),
        "url": f"https://www.bilibili.com/video/{bvid}",
        "updated_at": updated_at,
    }
    return {field: values[field] for field in PUBLIC_VIDEO_FIELDS}


def merge_provider_data(
    public_data: Any,
    provider_data: Any,
    updated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Lock a trusted prefix, then sort all remaining generic candidates."""
    try:
        public_records = validate_public_data(public_data, "public draft")
    except PublicDataValidationError as error:
        raise ProviderMergeError("public draft is invalid") from error
    provider = validate_provider_data(provider_data)
    merged_at = (
        updated_at
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )
    validate_timestamp(merged_at)

    locked = provider["locked"]
    pool = dict(public_records)
    pool.update(provider["candidates"])
    for bvid in locked:
        pool.pop(bvid, None)

    if locked:
        locked_floor = next(reversed(locked.values()))["count_num"]
        pool = {
            bvid: record
            for bvid, record in pool.items()
            if record["count_num"] <= locked_floor
        }

    sorted_pool = sorted(
        pool.items(),
        key=lambda item: (
            item[1]["count_num"],
            int(item[1].get("view") or 0),
            item[0],
        ),
        reverse=True,
    )
    selected = [*locked.items()]
    selected.extend(sorted_pool[: FINAL_SIZE - len(selected)])
    if len(selected) != FINAL_SIZE:
        raise ProviderMergeError("final ranking is incomplete")

    final_data = {
        bvid: rebuild_public_record(bvid, record, merged_at)
        for bvid, record in selected
    }
    try:
        validate_public_data(final_data, "final data")
    except PublicDataValidationError as error:
        raise ProviderMergeError("final ranking is invalid") from error
    return final_data


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge public ranking with generic provider data"
    )
    parser.add_argument("--public-input", type=Path, required=True)
    parser.add_argument("--provider-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        final_data = merge_provider_data(
            load_json(args.public_input),
            load_json(args.provider_input),
        )
        atomic_write_json(args.output, final_data)
    except (OSError, ValueError, ProviderMergeError):
        print("Provider merge failed", file=sys.stderr)
        return 1
    print(f"Provider merge completed: {len(final_data)} videos")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
