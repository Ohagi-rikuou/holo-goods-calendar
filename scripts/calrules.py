"""カレンダー登録・Discordお知らせで共通に使う決まりごと。

data.js（公開中のカレンダーと同じデータ）を読み、
「どの日付をカレンダーに登録できるか」を決める。
ページ側（index.html の calEvent）も同じ決まりで動く。
"""
import datetime
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = datetime.timezone(datetime.timedelta(hours=9))
SITE_URL = 'https://ohagi-rikuou.github.io/holo-goods-calendar/'

# 「2026年11月3週」「冬以降」のように日付がはっきりしないものは登録しない
EXACT_DAY = re.compile(r'\d{1,2}/\d{1,2}|\d{1,2}月\d{1,2}日')


def today_jst():
    return datetime.datetime.now(JST).date()


def load_items(path=None):
    path = path or os.path.join(ROOT, 'data.js')
    text = open(path, encoding='utf-8').read()
    data = json.loads(text[text.index('{'):text.rindex('}') + 1])
    return data['items']


def d(s):
    return datetime.date.fromisoformat(s) if s else None


def has_exact_start(it):
    label = it.get('startLabel') or ''
    return bool(it.get('start')) and (not label or bool(EXACT_DAY.search(label)))


def cal_event(it, today):
    """カレンダーに登録する予定を返す。登録できないものは None。

    kind='period'：販売・受注期間（開始日〜締切日）
    kind='day'   ：発売日・販売開始日の1日だけ
    """
    if it.get('status'):            # 売り切れ・再入荷予定などの在庫のお知らせは登録しない
        return None
    start, end = d(it.get('start')), d(it.get('end'))
    if end and not it.get('release'):
        if end < today:
            return None
        return {'kind': 'period', 'start': start or end, 'end': end}
    if start and start >= today and has_exact_start(it):
        return {'kind': 'day', 'start': start, 'end': start}
    return None


def short_date(x):
    wd = '月火水木金土日'[x.weekday()]
    return f'{x.month}/{x.day}（{wd}）'
