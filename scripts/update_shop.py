"""公式ショップ（hololive production OFFICIAL SHOP）の掲載内容を data/shop.json に反映する。

毎日 GitHub Actions から実行される。やること：
  1. 商品一覧（/products.json）を取得
  2. 一覧から消えた商品・終了から7日たった商品を削除
  3. 在庫の有無（SOLD OUT）を更新
  4. 期間のある商品は商品ページを読み直し、期間や発送予定が変わっていれば更新（「更新」タグ）
  5. 最近（LOOKBACK_DAYS 日以内）掲載された商品でまだ載っていないものを追加（「NEW」タグ）

ショップに負担をかけないよう、商品ページの取得は1件ずつ間隔をあけて行う。
取得に失敗した場合は data/shop.json を変更せずに終了する（公開中のデータはそのまま残る）。
"""
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOP_JSON = os.path.join(ROOT, 'data', 'shop.json')
BASE = 'https://shop.hololivepro.com'
UA = ('Mozilla/5.0 (compatible; holo-goods-calendar/1.0; '
      '+https://ohagi-rikuou.github.io/holo-goods-calendar/)')
JST = datetime.timezone(datetime.timedelta(hours=9))
TODAY = datetime.datetime.now(JST).date()
LOOKBACK_DAYS = 14       # この日数以内に掲載された商品を新着として拾う
KEEP_ENDED_DAYS = 7      # 期間終了後もこの日数は「終了」として表示を残す
WAIT = 1.5               # 商品ページ取得の間隔（秒）
SKIP_TAGS = {'hololiveofficialcardgame_Englishedition'}   # 日本の公式ショップでは買えない英語版
SKIP_HANDLES = set()

log = print


def get(url, tries=3):
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err = f'HTTP {e.code}'
        except Exception as e:  # noqa: BLE001 - ネットワークエラーは再試行
            err = repr(e)
        log(f'  取得失敗（{n + 1}/{tries}）{url}: {err}')
        time.sleep(10 * (n + 1))
    raise RuntimeError(f'取得できませんでした: {url}')


def all_products():
    out = []
    for page in range(1, 40):
        body = get(f'{BASE}/products.json?limit=250&page={page}')
        products = json.loads(body)['products']
        if not products:
            break
        out += products
        time.sleep(WAIT)
    if len(out) < 500:  # 通常は2,500件前後。極端に少ない時は異常とみなして何も変えない
        raise RuntimeError(f'商品一覧が {len(out)} 件しか取得できませんでした')
    # 海外向けの表示（ドル建て価格）になっていたら中止する。言語ヘッダーを付けると円以外になることがある
    prices = [v['price'] for p in out for v in p['variants']]
    if sum('.' in str(x) for x in prices) > len(prices) * 0.1:
        raise RuntimeError('価格が円で取得できませんでした（海外向け表示になっています）')
    return {p['handle']: p for p in out}


def page_text(handle):
    raw = get(f'{BASE}/products/{handle}')
    if raw is None:
        return None
    t = re.sub(r'<(script|style|noscript)\b[\s\S]*?</\1>', ' ', raw, flags=re.I)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', html.unescape(t)).strip()


PERIOD = re.compile(r'(受注受付期間|販売期間)\s*[:：]\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{2})分\s*[〜～~]'
                    r'\s*(?:(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{2})分)?')
SHIP = re.compile(r'発送予定日\s*[:：]\s*(.{1,200}?(?:までに発送|発送いたします。|表示されます。|発行されます。|できます。))')


def header(text):
    """商品名の下の「受注生産商品」「販売期間」「発送予定日」が並ぶ部分だけを取り出す。"""
    i = text.find('カテゴリ一覧')
    i = text.find('カート', i if i >= 0 else 0)
    if i < 0:
        return ''
    seg = text[i + 3:i + 1500]
    m = re.search(r'すべて\s*[:：]|選択してください|¥', seg)
    return seg[:m.start()] if m else seg[:600]


def parse_page(text):
    head = header(text)
    m = PERIOD.search(head) or PERIOD.search(text)
    period = None
    if m:
        g = m.groups()
        start = f'{int(g[1]):04d}-{int(g[2]):02d}-{int(g[3]):02d}'
        stime = f'{int(g[4]):02d}:{g[5]}'
        end = f'{int(g[6]):04d}-{int(g[7]):02d}-{int(g[8]):02d}' if g[6] else ''
        etime = f'{int(g[9]):02d}:{g[10]}' if g[6] else ''
        period = (g[0], start, stime, end, etime)
    s = SHIP.search(head)
    ship = s.group(1).strip() if s else ''
    return {'period': period, 'ship': ship, 'order': '受注生産商品' in head, 'reserve': '予約販売商品' in head}


def ship_text(ship, digital):
    m = re.match(r'(\d{4}年\d{2}月(?:上旬|中旬|下旬|末|頃)?)までに発送', ship)
    if m:
        return m.group(1) + 'までに発送予定'
    if '10営業日' in ship:
        return '在庫商品（決済確認後10営業日以内に発送）'
    if digital or 'ご注文完了後' in ship:
        if ship.startswith('購入特典'):
            m = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)\s*(\d{1,2}:\d{2})', ship)
            return f'購入特典は{m.group(1)}{m.group(2)}からダウンロード' if m else ship.rstrip('。')
        return 'デジタル商品（購入後すぐ利用可）'
    return ship


def yen(product):
    prices = sorted(int(float(v['price'])) for v in product['variants'])
    paid = [x for x in prices if x > 0]   # 0円の特典・おまけは最低価格に含めない
    if not paid:
        return '無料（ホロプラス特典）' if product['handle'].startswith('holoplus') else '無料'
    a, b = paid[0], paid[-1]
    f = lambda n: f'¥{n:,}'  # noqa: E731
    return f(a) if a == b else f'{f(a)}〜{f(b)}'


def tags_of(product):
    t = product['tags']
    return t if isinstance(t, list) else [x.strip() for x in str(t).split(',')]


def is_digital(product):
    return all(not v.get('requires_shipping', True) for v in product['variants'])


def available(product):
    return any(v.get('available') for v in product['variants'])


def category(title, handle, talents, digital):
    plain = title.replace('ホロライブ', '').replace('hololive', '')
    if handle.startswith('hololiveofficialcardgame') or 'ホロカ' in title or 'OFFICIAL CARD GAME' in title:
        return 'hocg'
    if '誕生日' in title:
        return 'birthday'
    if re.search(r'Live|LIVE|ライブ|Concert|コンサート|Blu-ray', plain):
        return 'event'
    if re.search(r'周年|記念|新衣装', title):
        return 'anniv'
    if digital or 'ボイス' in title:
        return 'digital'
    if len(talents) >= 2:
        return 'unit'
    return 'other'


def sale_and_period(info, digital, pub):
    p = info['period']
    if info['order']:
        sale = '受注生産'
    elif p and p[3]:
        sale = '期間限定販売（デジタル）' if digital else '期間限定販売'
    elif info['reserve']:
        sale = '予約販売'
    else:
        sale = '在庫販売'
    if p and p[3]:
        label = '受注受付期間' if sale == '受注生産' else '販売期間'
        text = f'{label} {p[1].replace("-", "/")} {p[2]} 〜 {p[3].replace("-", "/")} {p[4]}'
        return sale, p[1], p[3], text
    return sale, pub, '', ''


def jst_date(iso):
    return datetime.datetime.fromisoformat(iso).astimezone(JST).date().isoformat()


def new_item(product, info):
    handle, title = product['handle'], product['title'].strip()
    tags = tags_of(product)
    talents = [t[len('Talent_'):] for t in tags if t.startswith('Talent_')]
    digital = is_digital(product)
    sale, start, end, text = sale_and_period(info, digital, jst_date(product['published_at']))
    item_tags = (['デジタル'] if digital else []) + (['再販売'] if '再販' in title else []) \
        + (['先行発送'] if '先行発送' in title else []) + (['再入荷予定'] if 'restock-soon' in tags else [])
    item = {
        'id': 's_' + handle, 'title': title, 'members': talents,
        'category': category(title, handle, talents, digital), 'channel': 'online', 'origin': 'shop',
        'sale': sale, 'start': start, 'end': end, 'periodText': text,
        'ship': ship_text(info['ship'], digital), 'price': yen(product),
        'shop': 'hololive production OFFICIAL SHOP', 'url': f'{BASE}/products/{handle}',
        'source': f'公式ショップ 商品ページ（{TODAY.isoformat()} 確認・自動取得）',
        'tags': item_tags, 'firstSeen': TODAY.isoformat(),
    }
    if not available(product):
        item['soldOut'] = True
    return item


def ended_long_ago(item):
    if not item.get('end'):
        return False
    return datetime.date.fromisoformat(item['end']) < TODAY - datetime.timedelta(days=KEEP_ENDED_DAYS)


def mark_changed(item, note):
    item['changed'] = TODAY.isoformat()
    item['changeNote'] = note
    log(f'  更新: {item["title"]}（{note}）')


def main():
    with open(SHOP_JSON, encoding='utf-8') as f:
        items = json.load(f)
    log(f'掲載中の公式ショップ商品: {len(items)} 件')
    products = all_products()
    log(f'ショップの商品一覧: {len(products)} 件')

    kept, removed = [], []
    for it in items:
        handle = it['id'][2:]
        if handle not in products:
            removed.append((it, 'ショップから削除'))
        elif ended_long_ago(it):
            removed.append((it, f'終了から{KEEP_ENDED_DAYS}日経過'))
        else:
            kept.append(it)
    for it, why in removed:
        log(f'  削除: {it["title"]}（{why}）')

    # 在庫・期間の更新
    checked = found = 0
    for it in kept:
        p = products[it['id'][2:]]
        sold = not available(p)
        if sold != bool(it.get('soldOut')):
            if sold:
                it['soldOut'] = True
                mark_changed(it, 'SOLD OUT')
            else:
                it.pop('soldOut', None)
                mark_changed(it, '販売再開')
        if not (it.get('end') or it.get('sale') in ('受注生産', '予約販売')):
            continue
        text = page_text(p['handle'])
        time.sleep(WAIT)
        if not text:
            continue
        info = parse_page(text)
        per = info['period']
        if it.get('end'):
            checked += 1
            found += bool(per)
        if per and per[3] and (per[1] != it.get('start') or per[3] != it.get('end')):
            label = '受注受付期間' if it.get('sale') == '受注生産' else '販売期間'
            old = it.get('end')
            it['start'], it['end'] = per[1], per[3]
            it['periodText'] = f'{label} {per[1].replace("-", "/")} {per[2]} 〜 {per[3].replace("-", "/")} {per[4]}'
            mark_changed(it, '期間変更' + (f'（締切 {old} → {per[3]}）' if old and old != per[3] else ''))
        if info['ship']:
            ship = ship_text(info['ship'], is_digital(p))
            if re.match(r'\d{4}年', ship) and ship != it.get('ship'):
                old = it.get('ship', '')
                it['ship'] = ship
                mark_changed(it, f'発送予定の変更（{old} → {ship}）' if old else f'発送予定 {ship}')

    if checked >= 5 and found < checked * 0.5:
        raise RuntimeError(f'商品ページの販売期間が読み取れません（{checked} 件中 {found} 件）。ページの形式が変わった可能性があります')

    # 新着
    have = {it['id'][2:] for it in kept} | {it['id'][2:] for it, _ in removed}
    since = TODAY - datetime.timedelta(days=LOOKBACK_DAYS)
    added = []
    for handle, p in sorted(products.items(), key=lambda kv: kv[1]['published_at']):
        if handle in have or handle in SKIP_HANDLES or SKIP_TAGS & set(tags_of(p)):
            continue
        if datetime.date.fromisoformat(jst_date(p['published_at'])) < since:
            continue
        text = page_text(handle)
        time.sleep(WAIT)
        if not text:
            continue
        item = new_item(p, parse_page(text))
        if ended_long_ago(item) or item.get('soldOut'):
            continue  # 期間が終わった商品・売り切れ（再入荷待ち）の商品は載せない
        added.append(item)
        log(f'  追加: {item["title"]}（{item["sale"]}・{item["category"]}）')

    for it in kept:
        if it['source'].startswith('公式ショップ 商品ページ（'):
            it['source'] = re.sub(r'（\d{4}-\d{2}-\d{2}', f'（{TODAY.isoformat()}', it['source'], count=1)

    result = added[::-1] + kept
    with open(SHOP_JSON, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    log(f'結果: 追加 {len(added)}・削除 {len(removed)}・掲載 {len(result)} 件')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write(f'## 公式ショップ自動更新（{TODAY}）\n\n- 追加 {len(added)} 件\n- 削除 {len(removed)} 件\n'
                    f'- 掲載 {len(result)} 件\n')
            for it in added:
                f.write(f'  - 追加: {it["title"]}\n')
            for it, why in removed:
                f.write(f'  - 削除: {it["title"]}（{why}）\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f'::error::公式ショップの更新に失敗しました（データは変更していません）: {e}')
        sys.exit(1)
