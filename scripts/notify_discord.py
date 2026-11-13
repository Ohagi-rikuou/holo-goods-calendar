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

見やすさの工夫
  - 埋め込み（小さい文字）ではなく通常のメッセージ文字で、見出し（###）を使う
  - 日付・残り日数を商品名より先に書き、同じ日付ごとにまとめる
  - 同じシリーズ（商品名の最初の2語が同じ）の商品は1行にまとめる
  - 1通2,000文字を超えるときは、欄の切れ目で複数のメッセージに分ける
  - リンクのプレビュー（大きなカード）は出さない

試すとき: python3 scripts/notify_discord.py --dry-run --test   （投稿せずに中身を表示）
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.request

from calrules import SITE_URL, d, has_exact_start, load_items, short_date, today_jst

SOON_DAYS = 3
MAX_ROWS = 12           # 1つの欄に載せる最大行数（残りは「ほか○件」）
TITLE_MAX = 60
MSG_MAX = 1900          # Discordの1通の上限は2,000文字。少し余裕を持たせる
SUPPRESS_EMBEDS = 1 << 2


def cut(s, n=TITLE_MAX):
    return s if len(s) <= n else s[:n - 1] + '…'


def link(text, url):
    text = cut(text).replace('[', '［').replace(']', '］')
    return f'[{text}](<{url}>)' if url else text


def strip_tags(title):
    return re.sub(r'^(【[^】]*】\s*)+', '', title)       # 【受注生産】【先行発送】などの頭の印を外す


def series_key(title):
    words = strip_tags(title).split()
    return ' '.join(words[:2]) if len(words) >= 3 else None


def rows_for(items):
    """同じシリーズの商品を1行にまとめた行の一覧を返す。"""
    groups, order = {}, []
    for it in items:
        k = series_key(it['title']) or ('#' + it['id'])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(it)
    rows = []
    for k in order:
        g = groups[k]
        if len(g) == 1 or k.startswith('#'):
            rows += [(f"- {link(i['title'], i.get('url'))}", 1) for i in g]
        else:
            parts = []
            for i in g:
                head = re.match(r'^(【[^】]*】\s*)*', i['title']).group(0).strip()
                rest = strip_tags(i['title'])[len(k):].strip() or i['title']
                parts.append(link(head + rest, i.get('url')))
            rows.append((f"- {k}（{len(g)}件）：" + '、'.join(parts), len(g)))
    return rows


def until_text(it, today):
    if it.get('status'):
        return {'soldout': '売り切れ', 'restock': '再入荷予定', 'resumed': '販売再開'}.get(it['status'], '')
    s, e = d(it.get('start')), d(it.get('end'))
    if it.get('startLabel'):
        return it['startLabel']
    if it.get('release') and s:
        return f'{short_date(s)} 発売'
    if e:
        n = (e - today).days
        return '今日まで' if n == 0 else f'あと{n}日・{short_date(e)}まで'
    return '終了日未定'


def bucketed(items, label_of):
    """ラベル（日付など）ごとにまとめて、見出し＋行の一覧にする。"""
    buckets, order = {}, []
    for it in items:
        lb = label_of(it)
        if lb not in buckets:
            buckets[lb] = []
            order.append(lb)
        buckets[lb].append(it)
    out = []
    for lb in order:
        out.append((lb, rows_for(buckets[lb])))
    return out


def collect(items, today):
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
    secs = []
    if new:
        secs.append((f'🆕 新しく載った商品（{len(new)}件）', bucketed(new, lambda i: until_text(i, today))))
    if soon:
        secs.append((f'⏰ 締切が近い商品（{SOON_DAYS}日以内・{len(soon)}件）', bucketed(soon, lambda i: until_text(i, today))))
    if start:
        secs.append((f'🛒 今日から（{len(start)}件）',
                     bucketed(start, lambda i: '今日発売' if i.get('release') else '今日から販売開始')))
    return secs


def section_text(title, buckets, budget=MSG_MAX - 80):
    """見出し＋日付ごとの行。行数か文字数が上限を超えたら「ほか○件」にまとめる。"""
    lines, used, rest = [f'### {title}'], 0, 0
    size = len(lines[0])
    for label, rows in buckets:
        label_line = f'**{label}**'
        label_added = False
        for text, n in rows:
            add = len(text) + 1 + (0 if label_added else len(label_line) + 1)
            if used >= MAX_ROWS or size + add > budget:
                rest += n
                continue
            if not label_added:
                lines.append(label_line)
                label_added = True
            lines.append(text)
            size += add
            used += 1
    if rest:
        lines.append(f'ほか{rest}件はカレンダーで確認してください')
    return '\n'.join(lines)


def build_messages(secs, today, test=False):
    head = f'## 📅 ホロライブ グッズカレンダー（{today.month}/{today.day}）'
    if test:
        head = '【テスト送信】\n' + head
    foot = f'🔗 <{SITE_URL}>\n-# ※購入前に販売ページで最新情報をご確認ください。'
    blocks = [head]
    if not secs:
        blocks.append('今日お知らせする新着・締切はありません。')
    for title, buckets in secs:
        blocks.append(section_text(title, buckets))
    blocks.append(foot)
    msgs, cur = [], ''
    for b in blocks:
        if cur and len(cur) + 2 + len(b) > MSG_MAX:
            msgs.append(cur)
            cur = b
        else:
            cur = (cur + '\n\n' + b) if cur else b
    msgs.append(cur)
    return [{
        'username': 'ホログッズカレンダー',
        'avatar_url': SITE_URL + 'icons/icon-192.png',
        'content': m,
        'flags': SUPPRESS_EMBEDS,               # リンクの大きなプレビューを出さない
        'allowed_mentions': {'parse': []},      # @everyone などの呼び出しは絶対にしない
    } for m in msgs]


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
    secs = collect(load_items(), today)
    if not secs and not test:
        print('お知らせする商品がないので投稿しません')
        return
    msgs = build_messages(secs, today, test)
    if dry:
        for m in msgs:
            print(f"----- {len(m['content'])}文字 -----")
            print(m['content'])
        return
    url = os.environ.get('DISCORD_WEBHOOK_URL', '').strip()
    if not url:
        print('DISCORD_WEBHOOK_URL が設定されていないので投稿しません')
        return
    for m in msgs:
        status = post(url, m)
        time.sleep(1)                           # 連続投稿の制限に当たらないよう少し待つ
    print(f'Discordへ投稿しました（{status}・{len(msgs)}通）：' + '、'.join(t for t, _ in secs))


if __name__ == '__main__':
    main()
