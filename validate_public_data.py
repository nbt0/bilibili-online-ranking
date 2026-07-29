import argparse
import json
import re
from datetime import datetime
from pathlib import Path

from video_ranking import PUBLIC_VIDEO_FIELDS


class PublicDataValidationError(ValueError):
    """公开排行榜数据不符合公共合同时抛出。"""


def validate_public_data(data, source_name='data'):
    """校验一份只包含公开字段的排行榜数据。"""
    if not isinstance(data, dict) or not 1 <= len(data) <= 100:
        raise PublicDataValidationError(
            f'{source_name}: must contain 1 to 100 videos'
        )

    allowed = set(PUBLIC_VIDEO_FIELDS)
    online_values = []

    for bvid, record in data.items():
        if not isinstance(bvid, str) or not bvid:
            raise PublicDataValidationError(
                f'{source_name}: invalid video key {bvid!r}'
            )
        if not isinstance(record, dict):
            raise PublicDataValidationError(
                f'{source_name}: record for {bvid} must be an object'
            )

        actual = set(record)
        if actual != allowed:
            unexpected = sorted(actual - allowed)
            missing = sorted(allowed - actual)
            details = []
            if unexpected:
                details.append(f'unexpected fields: {", ".join(unexpected)}')
            if missing:
                details.append(f'missing fields: {", ".join(missing)}')
            raise PublicDataValidationError(
                f'{source_name}: invalid fields for {bvid} '
                f'({"; ".join(details)})'
            )

        online_count = record['online_count']
        if (
            not isinstance(online_count, str)
            or not re.fullmatch(r'[0-9]+', online_count)
        ):
            raise PublicDataValidationError(
                f'{source_name}: invalid online_count for {bvid}'
            )

        count_num = record['count_num']
        if (
            not isinstance(count_num, int)
            or isinstance(count_num, bool)
            or int(online_count) != count_num
        ):
            raise PublicDataValidationError(
                f'{source_name}: online count mismatch for {bvid}'
            )

        pic = record['pic']
        if not isinstance(pic, str) or not pic.startswith('https://'):
            raise PublicDataValidationError(
                f'{source_name}: pic must use HTTPS for {bvid}'
            )

        expected_url = f'https://www.bilibili.com/video/{bvid}'
        if record['url'] != expected_url:
            raise PublicDataValidationError(
                f'{source_name}: invalid url for {bvid}'
            )

        updated_at = record['updated_at']
        try:
            parsed_time = datetime.fromisoformat(updated_at)
        except (TypeError, ValueError) as error:
            raise PublicDataValidationError(
                f'{source_name}: invalid updated_at for {bvid}'
            ) from error
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise PublicDataValidationError(
                f'{source_name}: updated_at must include timezone for {bvid}'
            )

        online_values.append(count_num)

    if online_values != sorted(online_values, reverse=True):
        raise PublicDataValidationError(
            f'{source_name}: videos are not sorted by online count'
        )

    return data


def validate_public_data_file(path):
    """读取并校验一个公开排行榜 JSON 文件。"""
    data_path = Path(path)
    try:
        with data_path.open('r', encoding='utf-8') as file:
            data = json.load(file)
    except (OSError, ValueError) as error:
        raise PublicDataValidationError(
            f'{data_path}: unable to read JSON: {error}'
        ) from error

    return validate_public_data(data, str(data_path))


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(
        description='按同一公开字段合同校验一份或多份排行榜 JSON。',
    )
    parser.add_argument(
        'paths',
        nargs='+',
        help='按顺序校验的公开排行榜 JSON 文件',
    )
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_arguments(argv)
    for path in arguments.paths:
        try:
            data = validate_public_data_file(path)
        except PublicDataValidationError as error:
            print(f'Validation failed: {error}')
            return 1
        print(f'Validated {len(data)} public videos: {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
