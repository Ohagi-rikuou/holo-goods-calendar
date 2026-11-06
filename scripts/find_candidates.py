"""漏れ候補リストを作る（カレンダーには自動で載せない）。

AIの読み取りを許可しているニュース・公式サイトの新着から、ホロライブ関連で
グッズ・コラボ・キャンペーンらしい記事を集め、すでに載っている商品と照らし合わせて
data/candidates.json に「未確認の候補」として保存する。
「更新して」のときに候補を確認し、販売元の公式情報で確かめてから data/manual.json に登録する。
確認済み（登録した・対象外と判断した）の記事URLは data/candidates_reviewed.json に入れると、次から出なくなる。
data/watchlist.json の「注目キーワード」に合う記事は、グッズ用の絞り込みに関係なく【注目】として必ず候補に入れる
（PR TIMES のキーワード検索も注目キーワードごとに行う）。
注目キーワードの mode：until_found（見つかったら、登録後に外すか残すかを依頼者に確認する）／ permanent（ずっと監視する）。

各サイトの robots.txt で許可されていることを確認済み。読み取りは1日1回・各サイト1〜2回のみ。
"""
import datetime
import difflib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data')
JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).date()
KEEP_DAYS = 45
RECENT_DAYS = 30  # 記事の日付がこれより古いものは候補にしない
UA = 'Mozilla/5.0 (compatible; holo-goods-calendar/1.0; +https://ohagi-rikuou.github.io/holo-goods-calendar/)'
WAIT = 5  # インサイド・アニメ！アニメ！の Crawl-delay 5秒に合わせて全サイトで間隔をあける

# (表示名, URL, 種類)
SOURCES = [
    ('PR TIMES（キーワード：ホロライブ）', 'https://prtimes.jp/main/action.php?run=html&page=searchkey&search_word=%E3%83%9B%E3%83%AD%E3%83%A9%E3%82%A4%E3%83%96', 'prtimes'),
    ('PR TIMES（カバー株式会社）', 'https://prtimes.jp/companyrdf.php?company_id=30268', 'feed'),
    ('公式ショップ お知らせ', 'https://shop.hololivepro.com/blogs/news.atom', 'feed'),
    ('インサイド', 'https://www.inside-games.jp/rss/index.rdf', 'feed'),
    ('アニメ！アニメ！', 'https://animeanime.jp/rss/index.rdf', 'feed'),
    ('ホロライビュアーズ', 'https://holoviewers.net/feed/', 'feed'),
    ('コラボカフェ（ホロライブの記事一覧）', 'https://collabo-cafe.com/events/category/hololive/', 'collabo'),
    ('ホロライブ公式サイト 英語版ニュース（海外の展開）', 'https://hololive.hololivepro.com/en/news/', 'holoen'),
    # HoloIndex は robots.txt で ClaudeBot などAIの読み取りを拒否しているため、2026-11-06 に情報源から外した
]

HOLO = re.compile(r'ホロライブ|hololive|ホロカ|ホロスターズ|holostars|holoX|ReGLOSS|FLOW GLOW|アソビ★まわり隊', re.I)
GOODS = re.compile(r'グッズ|コラボ|発売|予約|販売|受注|キャンペーン|特典|ノベルティ|フェア|ポップアップ|POP ?UP|くじ|プライズ|'
                   r'フィギュア|ねんどろいど|figma|ぬいぐるみ|カフェ|展示|ストア|ショップ|景品|限定|再販|入荷|CD|アルバム|BOOK|書籍|ゲーム|'
                   r'ウエハース|カード|食玩|ガシャポン|ガチャ|カプセル|アクリル|アクスタ|缶バッジ|Tシャツ|アパレル|ラインナップ|第\d+弾|'
                   r'PCケース|キーキャップ|マウスパッド|香水|フレグランス|ブルーレイ|Blu-ray|写真集|画集|コミック', re.I)
# ホロライブ専門の情報源（ここの記事はグッズらしい言葉がなくても候補にする）
HOLO_ONLY = ('PR TIMES（カバー株式会社）', '公式ショップ お知らせ', 'ホロライブ公式サイト 英語版ニュース（海外の展開）', 'ホロライビュアーズ', 'コラボカフェ（ホロライブの記事一覧）')
SKIP = re.compile(r'配信開始|視聴|再生数|登録者|出演|ゲスト|インタビュー|ランキング|アンケート|求人|採用|決算|ライブ配信のお知らせ|不具合|'
                  r'メンテナンス|配送|ペイディ|キャッシュバック|関税|お詫び|不備|発送に関する|オフィシャルレポート|事前登録|ゲームジャム|スポンサー|【終了】|開催終了|ログイン|確認画面|引くべき')
GENERIC = re.compile(r'ホロライブプロダクション|ホロライブ|hololive|production|プロダクション|オフィシャル|公式|グッズ|コラボ(レーション)?|'
                     r'発売|決定|開催|販売|予約|受付|開始|登場|商品|限定|新作|情報|記念|第\d+弾|の|が|を|に|と|で|\s|[!！?？「」『』【】（）()・,、。:：/／-]', re.I)


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return r.read().decode('utf-8', 'replace')


def clean(s):
    s = re.sub(r'<!\[CDATA\[|\]\]>', '', s)
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s))).strip()


def parse_feed(text):
    out = []
    for block in re.findall(r'<(?:item|entry)\b[\s\S]*?</(?:item|entry)>', text):
        t = re.search(r'<title[^>]*>([\s\S]*?)</title>', block)
        link = re.search(r'<link[^>]*href="([^"]+)"', block) or re.search(r'<link>([\s\S]*?)</link>', block) \
            or re.search(r'rdf:about="([^"]+)"', block)
        d = re.search(r'<(?:pubDate|dc:date|updated|published)>([\s\S]*?)</', block)
        desc = re.search(r'<(?:description|summary|content)[^>]*>([\s\S]*?)</(?:description|summary|content)>', block)
        if t and link:
            out.append({'title': clean(t.group(1)), 'url': clean(link.group(1)),
                        'date': clean(d.group(1))[:25] if d else '', 'text': clean(desc.group(1))[:300] if desc else ''})
    return out


def parse_links(text, pattern):
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(' + pattern + r')"[^>]*>([\s\S]{0,900}?)</a>', text):
        url, title = m.group(1), clean(m.group(2))
        if url.startswith('/'):
            url = 'https://prtimes.jp' + url
        if len(title) > 8 and url not in seen:
            seen.add(url)
            out.append({'title': title[:160], 'url': url, 'date': '', 'text': ''})
    return out


def article_date(e):
    """記事の日付（分からなければ None）。"""
    d = e.get('date', '')
    for fmt in ('%a, %d %b %Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(d[:16] if fmt.startswith('%a') else d[:10], fmt).date()
        except ValueError:
            pass
    m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', e['title'] + ' ' + d)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def parse_holoen(text):
    """ホロライブ公式サイト（英語版）ニュース一覧から、記事URL・タイトル・日付を取る（海外のポップアップ・コラボの把握用）。"""
    out = []
    for block in re.split(r'(?=href="https://hololive\.hololivepro\.com/en/news/\d{8})', text)[1:]:
        u = re.match(r'href="([^"]+)"', block).group(1)
        s = clean(block[:1500])
        m = re.search(r'(20\d\d)\.(\d\d)\.(\d\d)\s+(.+)', s)
        if m:
            title = re.split(r'\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},|\s+N$', m.group(4))[0]
            out.append({'title': title[:160], 'url': u, 'date': f'{m.group(1)}-{m.group(2)}-{m.group(3)}', 'text': ''})
    return out


def parse_collabo(text):
    """コラボカフェの「ホロライブの記事一覧」から記事URL・タイトル・掲載日を取る。"""
    out = []
    for m in re.finditer(r'<a href="(https://collabo-cafe\.com/events/collabo/[^"]+)" rel="bookmark" title="([^"]+)"[\s\S]{0,2500}?'
                         r'class="date gf updated">(\d{4})/(\d{2})/(\d{2})<', text):
        out.append({'title': html.unescape(m.group(2)), 'url': m.group(1),
                    'date': f'{m.group(3)}-{m.group(4)}-{m.group(5)}', 'text': ''})
    return out


def parse_holoindex(text):
    """HoloIndex の記事一覧からタイトルとURLを取る（タイトル末尾の「ニュース 11/3(火)」などは除く）。"""
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(/articles/[^"]+)"[^>]*>([\s\S]{0,1500}?)</a>', text):
        url = 'https://holoindex.com' + m.group(1)
        title = re.sub(r'\s*(ニュース|まとめ|ゲーム|ホロカ)?\s*\d{1,2}/\d{1,2}\([^)]*\).*$', '', clean(m.group(2)))
        if len(title) > 10 and url not in seen:
            seen.add(url)
            out.append({'title': title[:160], 'url': url, 'date': '', 'text': ''})
    return out


def key(s):
    return GENERIC.sub('', s.lower())


def main():
    items = []
    for name in ('shop.json', 'manual.json'):
        with open(os.path.join(DATA, name), encoding='utf-8') as f:
            items += json.load(f)
    known_urls = set()
    for it in items:
        for f in ('url', 'source', 'note'):
            known_urls.update(re.findall(r'https?://[^\s）)、]+', it.get(f, '')))
    reviewed_path = os.path.join(DATA, 'candidates_reviewed.json')
    reviewed = json.load(open(reviewed_path, encoding='utf-8')) if os.path.exists(reviewed_path) else []
    reviewed_urls = {r['url'] for r in reviewed}
    cand_path = os.path.join(DATA, 'candidates.json')
    old = json.load(open(cand_path, encoding='utf-8')) if os.path.exists(cand_path) else []
    first_seen = {c['url']: c['firstSeen'] for c in old}

    watch_path = os.path.join(DATA, 'watchlist.json')
    watch = json.load(open(watch_path, encoding='utf-8')) if os.path.exists(watch_path) else []
    sources = list(SOURCES)
    for w in watch:
        for q in w.get('search', []):
            sources.append((f'PR TIMES（注目：{q}）', 'https://prtimes.jp/main/action.php?run=html&page=searchkey&search_word='
                            + urllib.parse.quote(q), 'prtimes', w['name']))

    def watched(text):
        for w in watch:
            if all(re.search(a, text, re.I) for a in w.get('all', [])) and any(re.search(a, text, re.I) for a in w.get('any', ['.'])):
                return w['name']
        return None

    found, errors = [], []
    for name, url, kind, *wname in sources:
        try:
            text = get(url)
            if kind == 'feed':
                entries = parse_feed(text)
            elif kind == 'prtimes':
                entries = parse_links(text, r'/main/html/rd/p/[0-9.]+\.html')
            elif kind == 'holoindex':
                entries = parse_holoindex(text)
            elif kind == 'holoen':
                entries = parse_holoen(text)
            else:
                entries = parse_collabo(text)
            for e in entries:
                e['source'] = name
                if wname:
                    e['watch'] = wname[0]  # 注目キーワードの検索で見つかった記事（本文に一致）
            found += entries
            print(f'{name}: {len(entries)} 件')
        except urllib.error.HTTPError as ex:
            if kind == 'prtimes' and ex.code == 404:  # PR TIMES は検索結果が0件のとき 404 を返す
                print(f'{name}: 0 件')
            else:
                errors.append(f'{name}: {ex}')
                print(f'::warning::{name} を取得できませんでした: {ex}')
        except Exception as ex:  # noqa: BLE001
            errors.append(f'{name}: {ex}')
            print(f'::warning::{name} を取得できませんでした: {ex}')
        time.sleep(WAIT)

    with open(os.path.join(DATA, 'members.json'), encoding='utf-8') as f:
        names = [m['name'] for m in json.load(f)['members'] if len(m['name']) >= 3]
    holo = re.compile(HOLO.pattern + '|' + '|'.join(map(re.escape, names)), re.I)
    keys = [(it, key(it['title'])) for it in items]
    result, seen = [], set()
    for e in found:
        u = e['url'].split('?')[0]
        blob = e['title'] + ' ' + e['text']
        if u in seen or u in reviewed_urls or e['url'] in known_urls or u in known_urls:
            continue
        w = e.get('watch') or watched(blob)
        need_goods = e['source'] not in HOLO_ONLY
        if not w and (not holo.search(blob) or (need_goods and not GOODS.search(blob)) or SKIP.search(e['title'])):
            continue
        ad = article_date(e)
        if ad and ad < TODAY - datetime.timedelta(days=RECENT_DAYS):
            continue
        seen.add(u)
        k = key(e['title'])
        best, score = None, 0.0
        for it, ik in keys:
            if not k or not ik:
                continue
            m = difflib.SequenceMatcher(None, k, ik)
            s = max(m.ratio(), m.find_longest_match(0, len(k), 0, len(ik)).size / max(6, min(len(k), len(ik))))
            if s > score:
                best, score = it, s
        c = {'firstSeen': first_seen.get(u, TODAY.isoformat()), 'source': e['source'], 'title': e['title'],
             'url': u, 'date': e['date']}
        if w:
            c['watch'] = w
        if best and score >= 0.45:
            c['similar'] = {'id': best['id'], 'title': best['title'], 'score': round(score, 2)}
        result.append(c)

    limit = (TODAY - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    keep = {c['url']: c for c in old if c['firstSeen'] >= limit and c['url'] not in reviewed_urls}
    for c in result:
        keep[c['url']] = c
    out = sorted(keep.values(), key=lambda c: ('watch' in c, c['firstSeen'], c['source']), reverse=True)
    with open(cand_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    new = [c for c in out if c['firstSeen'] == TODAY.isoformat()]
    likely = [c for c in out if 'similar' not in c]
    print(f'候補 {len(out)} 件（今日見つかった {len(new)} 件・既存商品と似ていない {len(likely)} 件）')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write(f'\n## 漏れ候補（{TODAY}）\n\n候補 {len(out)} 件・今日の新規 {len(new)} 件\n\n')
            for c in [c for c in out if 'watch' in c]:
                f.write(f'- **【注目：{c["watch"]}】** [{c["title"]}]({c["url"]})（{c["source"]}・{c["firstSeen"]}）\n')
            for c in [c for c in new if 'watch' not in c][:30]:
                f.write(f'- [{c["title"]}]({c["url"]})（{c["source"]}）\n')
            hit = {c['watch'] for c in out if 'watch' in c}
            for w in watch:
                mode = 'ずっと監視' if w.get('mode') == 'permanent' else '見つかるまで'
                extra = '　→ 情報あり：登録後、このキーワードを外すか残すかを確認' if w['name'] in hit and mode == '見つかるまで' else ''
                f.write(f'- 注目キーワード監視中：{w["name"]}（{w["added"]}〜・{mode}）{extra}\n')
            for er in errors:
                f.write(f'- 取得失敗: {er}\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f'::warning::漏れ候補の作成に失敗しました（カレンダーには影響しません）: {e}')
        sys.exit(0)
