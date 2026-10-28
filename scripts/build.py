"""data/*.json をまとめて、公開ページが読む data.js を作る。

  data/shop.json    公式ショップの商品（update_shop.py が毎日自動で更新）
  data/manual.json  企業・店舗・プライズなど手動で追加した商品
  data/members.json メンバー一覧（期生・推しマーク・読み）
  data/meta.json    種別の名前・掲載範囲の説明
"""
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = datetime.timezone(datetime.timedelta(hours=9))


def load(name):
    with open(os.path.join(ROOT, 'data', name), encoding='utf-8') as f:
        return json.load(f)


def build(updated=None):
    updated = updated or datetime.datetime.now(JST).date().isoformat()
    shop, manual = load('shop.json'), load('manual.json')
    master, base = load('members.json'), load('meta.json')
    items = shop + manual

    ids = [i['id'] for i in items]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        sys.exit('同じIDの商品が重複しています: ' + ', '.join(sorted(dup)))

    order = master['genOrder']
    known = {m['name']: (n, m) for n, m in enumerate(master['members'])}
    used = []
    for it in items:
        for name in it['members']:
            if name not in used:
                used.append(name)

    def info(name):
        if name in known:
            return known[name]
        return 9999, {'name': name, 'kana': '', 'mark': '', 'gens': ['その他']}

    used.sort(key=lambda n: (order.index(info(n)[1]['gens'][0]), info(n)[0]))
    members = [{'id': n, 'name': n, 'kana': info(n)[1]['kana'], 'mark': info(n)[1]['mark'],
                'gens': info(n)[1]['gens']} for n in used]

    meta = dict(base['meta'])
    meta['updated'] = updated
    meta['genOrder'] = [g for g in order if any(g in m['gens'] for m in members)]
    data = {'meta': {'isTest': meta.pop('isTest', False), 'updated': updated, 'newDays': meta.pop('newDays', 7),
                     'genOrder': meta.pop('genOrder'), **{k: v for k, v in meta.items() if k != 'updated'}},
            'categories': base['categories'], 'members': members, 'items': items}

    js = ('/* ホロライブ グッズカレンダー データ（' + updated + ' 更新）\n'
          ' * 出典：hololive production OFFICIAL SHOP 各商品ページ／PR TIMES 各社発表ほか（各商品の source・url を参照）。\n'
          ' * このファイルは scripts/build.py が data/*.json から自動で作ります。直接編集しないでください。\n'
          ' */\nwindow.HOLO_GOODS = ' + json.dumps(data, ensure_ascii=False, indent=1) + ';\n')
    with open(os.path.join(ROOT, 'data.js'), 'w', encoding='utf-8') as f:
        f.write(js)
    print(f'data.js: 商品 {len(items)} 件（公式ショップ {len(shop)}・その他 {len(manual)}）、メンバー {len(members)} 名')


if __name__ == '__main__':
    build(sys.argv[1] if len(sys.argv) > 1 else None)
