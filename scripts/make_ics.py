"""商品ごとのカレンダー登録用ファイル（.ics）を作る。

使い方: python3 scripts/make_ics.py <出力先フォルダ>
GitHub Actions が公開用フォルダ（_site/ics）に作る。リポジトリには保存しない。
iPhone・Mac・Outlook などは、この .ics を開くと「カレンダーに追加」できる。
締切（発売日・販売開始日）の前日 9:00（日本時間）に通知が入るようにしてある。
"""
import datetime
import os
import re
import sys

from calrules import SITE_URL, cal_event, load_items, short_date, today_jst


def esc(s):
    return (s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def fold(line):
    """1行75バイトまでに折り返す（.ics の決まり）。"""
    out, cur = [], b''
    for ch in line:
        b = ch.encode('utf-8')
        if len(cur) + len(b) > 74:
            out.append(cur.decode('utf-8'))
            cur = b' ' + b
        else:
            cur += b
    out.append(cur.decode('utf-8'))
    return '\r\n'.join(out)


def safe_name(item_id):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', item_id)


def summary(it, ev):
    if ev['kind'] == 'day':
        return ('【発売】' if it.get('release') else '【販売開始】') + it['title']
    return it['title']


def description(it, ev):
    if ev['kind'] == 'period':
        when = f"販売・受注期間：{short_date(ev['start'])}〜{short_date(ev['end'])}（締切 {short_date(ev['end'])}）"
    else:
        when = ('発売日：' if it.get('release') else '販売開始：') + short_date(ev['start'])
    lines = [when]
    if it.get('price'):
        lines.append('価格：' + it['price'])
    if it.get('url'):
        lines.append('商品ページ：' + it['url'])
    lines.append('ホロライブ グッズカレンダー（非公式）より：' + SITE_URL)
    lines.append('※購入前に販売ページで最新情報をご確認ください。')
    return '\n'.join(lines)


def location(it):
    v = it.get('venues') or []
    if len(v) == 1:
        return f"{v[0].get('place', '')}（{v[0].get('pref', '')}）"
    if len(v) > 1:
        return f"{v[0].get('place', '')} ほか{len(v) - 1}か所"
    return ''


def ics_text(it, ev, now_utc):
    alarm_day = ev['end'] - datetime.timedelta(days=1)       # 締切・発売日の前日
    alarm = datetime.datetime(alarm_day.year, alarm_day.month, alarm_day.day, 0, 0)  # 0:00 UTC = 9:00 日本時間
    label = '締切' if ev['kind'] == 'period' else ('発売日' if it.get('release') else '販売開始日')
    rows = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//holo-goods-calendar//JA',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f"UID:{safe_name(it['id'])}@ohagi-rikuou.github.io",
        'DTSTAMP:' + now_utc.strftime('%Y%m%dT%H%M%SZ'),
        'DTSTART;VALUE=DATE:' + ev['start'].strftime('%Y%m%d'),
        'DTEND;VALUE=DATE:' + (ev['end'] + datetime.timedelta(days=1)).strftime('%Y%m%d'),
        'SUMMARY:' + esc(summary(it, ev)),
        'DESCRIPTION:' + esc(description(it, ev)),
    ]
    if location(it):
        rows.append('LOCATION:' + esc(location(it)))
    if it.get('url'):
        rows.append('URL:' + it['url'])
    rows += [
        'TRANSP:TRANSPARENT',
        'BEGIN:VALARM',
        'ACTION:DISPLAY',
        'DESCRIPTION:' + esc(f"明日が{label}：{it['title']}"),
        'TRIGGER;VALUE=DATE-TIME:' + alarm.strftime('%Y%m%dT%H%M%SZ'),
        'END:VALARM',
        'END:VEVENT',
        'END:VCALENDAR',
    ]
    return '\r\n'.join(fold(r) for r in rows) + '\r\n'


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else '_site/ics'
    os.makedirs(out, exist_ok=True)
    today = today_jst()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    n = 0
    for it in load_items():
        ev = cal_event(it, today)
        if not ev:
            continue
        with open(os.path.join(out, safe_name(it['id']) + '.ics'), 'w', encoding='utf-8', newline='') as f:
            f.write(ics_text(it, ev, now_utc))
        n += 1
    print(f'カレンダー登録用ファイル {n} 件を {out} に作成')


if __name__ == '__main__':
    main()
