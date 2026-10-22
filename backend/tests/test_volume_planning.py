import pytest

from app.services.volume_planning import normalize_outline_batch, normalize_volume_plan


def volume(start, end, title='卷'):
    return {'title': title, 'summary': '主线冲突与收束', 'start_chapter': start, 'end_chapter': end}


@pytest.mark.parametrize('total,raw,expected', [
    (10, [volume(1, 5), volume(6, 10)], [(1, 10)]),
    (20, [volume(1, 20)], [(1, 20)]),
    (120, [volume(1, 120)], [(1, 120)]),
    (121, [volume(1, 60), volume(61, 121)], [(1, 60), (61, 121)]),
    (80, [volume(3, 30), volume(35, 90)], [(1, 40), (41, 80)]),
    (80, [volume(1, 60), volume(50, 80)], [(1, 40), (41, 80)]),
    (40, [volume(1, 10), volume(11, 20), volume(21, 30), volume(31, 40)], [(1, 20), (21, 40)]),
])
def test_normalize_volume_plan_repairs_ranges_and_short_story(total, raw, expected):
    result = normalize_volume_plan({'volumes': raw}, total)
    assert [(row['start_chapter'], row['end_chapter']) for row in result] == expected
    assert [row['volume_number'] for row in result] == list(range(1, len(expected) + 1))
    assert all(row['outline_cursor'] == row['start_chapter'] - 1 for row in result)
    if len(raw) > len(result):
        assert sum(row['summary'].count('主线冲突与收束') for row in result) == len(raw)


@pytest.mark.parametrize('raw,total', [({'volumes': []}, 20), ({'volumes': [volume(1, 200)]}, 200),
                                      ({'volumes': [volume(1, 20)]}, 0), ({'volumes': [volume(1, 20)]}, True)])
def test_normalize_volume_plan_rejects_missing_or_insufficient_story_plan(raw, total):
    with pytest.raises(ValueError):
        normalize_volume_plan(raw, total)


def test_outline_batch_orders_deduplicates_and_ignores_outside_range():
    result = normalize_outline_batch({'chapters': [
        {'chapter_number': 18, 'title': '后', 'summary': '后章摘要'},
        {'chapter_number': 16, 'title': '前', 'summary': '前章摘要'},
        {'chapter_number': 17, 'title': '中', 'summary': '中章摘要'},
        {'chapter_number': 17, 'title': '重复不能覆盖', 'summary': '重复'},
        {'chapter_number': 19, 'title': '越界', 'summary': '越界'},
    ]}, 16, 18)
    assert [row['chapter_number'] for row in result] == [16, 17, 18]
    assert result[1]['title'] == '中'


@pytest.mark.parametrize('chapters', [[], [{'chapter_number': 1, 'title': '错位', 'summary': '不能擅自改编号'}],
                                    [{'chapter_number': 16, 'title': '完整', 'summary': '摘要'}],
                                    [{'chapter_number': 16, 'title': '', 'summary': '缺标题'}]])
def test_outline_batch_missing_or_misaligned_is_rejected(chapters):
    with pytest.raises(ValueError):
        normalize_outline_batch({'chapters': chapters}, 16, 17)
