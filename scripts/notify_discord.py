"""Discordのチャンネルへ、毎日1回お知らせを投稿する。

- 投稿先は Discord の「ウェブフック」。URL は GitHub の非公開の設定
  （Secrets の DISCORD_WEBHOOK_URL）に入れておく。設定がなければ何もしない。
- ウェブフックはそのチャンネルに投稿することしかできない（読み取りは不可）。
- 毎日の自動実行（schedule）では、知らせる商品がなければ投稿しない。
  Actions 画面から手動で実行したとき（workflow_dispatch）は、テストとして必ず投稿する。

お知らせの中身
  🆕 新しく載った商品 … 公式ショップ分は今日見つかったもの、手入力分は前日に追加したもの
  ⏰ 締切が近い商品   … 締切が今日〜3日後
  🛒 今日から         … 今日が販売開始日・発売日

試すとき: python3 scripts/notify_discord.py --dry-run   （投稿せずに中身を表示）
"""
import datetime
import json
import os
import sys
import urllib.request

from calrules import SITE_URL, d, has_exact_start, load_items, short_date, today_jst

SOON_DAYS = 3
MAX_LINES = 12          # 1つの欄に載せる最大件数（残りは「ほか○件」）
TITLE_MAX = 60


def cut(s, n=TITLE_MAX):
    return s if len(s) <= n else s[:n - 1] + '…'


def line(it, extra=''):
    title = cut(it['title']).replace('[', '［').replace(']', '］')
    head = f"[{title}]({it['url']})" if it.get('url') else title
    return f'・{head}{extra}'


def period_text(it):
    s, e = d(it.get('start')), d(it.get('end'))
    if it.get('status'):
        return {'soldout': '（売り切れ）', 'restock': '（再入荷予定）', 'resumed': '（販売再開）'}.get(it['status'], '')
    if it.get('startLabel'):
        return f"（{it['startLabel']}）"
    if it.get('release') and s:
        return f'（{short_date(s)} 発売）'
    if e:
        return f'（〜{short_date(e)}）'
    return '（終了日未定）'


def sections(items, today):
    yday = today - datetime.timedelta(days=1)
    new, soon, start = [], [], []
    for it in items:
        first = d(it.get('firstSeen'))
        end, st = d(it.get('end')), d(it.get('start'))
        is_shop = it.get('origin') == 'shop'
        if first and ((is_shop and first == today) or (not is_shop and first == yday)):
            if not (end and end < today):
                new.append(it)
        if end and not it.get('release') and not it.get('status') and not it.get('soldOut') \
                and today <= end <= today + datetime.timedelta(days=SOON_DAYS):
            soon.append(it)
        if st == today and has_exact_start(it) and not it.get('status'):
            start.append(it)
    soon.sort(key=lambda x: x['end'])
    out = []
    if new:
        out.append(('🆕 新しく載った商品', [line(i, period_text(i)) for i in new]))
    if soon:
        def left(i):
            n = (d(i['end']) - today).days
            return '（今日まで）' if n == 0 else f"（あと{n}日・{short_date(d(i['end']))}まで）"
        out.append((f'⏰ 締切が近い商品（{SOON_DAYS}日以内）', [line(i, left(i)) for i in soon]))
    if start:
        out.append(('🛒 今日から', [line(i, '（発売日）' if i.get('release') else '（販売開始）') for i in start]))
    return out


def build_payload(secs, today, test=False):
    embeds = []
    colors = {'🆕': 0x2E86DE, '⏰': 0xE67E22, '🛒': 0x27AE60}
    for title, lines in secs:
        color = colors.get(title[0], 0x888888)
        shown = lines[:MAX_LINES]
        if len(lines) > MAX_LINES:
            shown.append(f'ほか{len(lines) - MAX_LINES}件はカレンダーで確認してください')
        desc = '\n'.join(shown)
        if len(desc) > 3900:
            desc = desc[:3900].rsplit('\n', 1)[0] + '\n…続きはカレンダーで確認してください'
        embeds.append({'title': f'{title}（{len(lines)}件）', 'description': desc, 'color': color})
    head = f"**📅 ホロライブ グッズカレンダー（{today.month}/{today.day}）**"
    if test:
        head = '【テスト送信】' + head
    if not embeds:
        body = head + '\n今日お知らせする新着・締切はありません。'
    else:
        body = head
    body += f'\n🔗 <{SITE_URL}>\n※購入前に販売ページで最新情報をご確認ください。'
    return {
        'username': 'ホログッズカレンダー',
        'avatar_url': SITE_URL + 'icons/icon-192.png',
        'content': body,
        'embeds': embeds,
        'allowed_mentions': {'parse': []},   # @everyone などの呼び出しは絶対にしない
    }


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), method='POST',
                                 headers={'Content-Type': 'application/json',
                                          'User-Agent': 'holo-goods-calendar (GitHub Actions)'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    dry = '--dry-run' in sys.argv
    test = os.environ.get('NOTIFY_MODE') == 'workflow_dispatch' or '--test' in sys.argv
    today = today_jst()
    secs = sections(load_items(), today)
    if not secs and not test:
        print('お知らせする商品がないので投稿しません')
        return
    payload = build_payload(secs, today, test)
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return
    url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not url:
        print('DISCORD_WEBHOOK_URL が設定されていないので投稿しません')
        return
    status = post(url, payload)
    print(f'Discordへ投稿しました（{status}）：' + '、'.join(f'{t} {len(l)}件' for t, l in secs))


if __name__ == '__main__':
    main()
