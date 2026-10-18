# STAC カタログを自作し、「選ぶ」コストを転送量で測る

**問い**: COG の部分読みで「1枚の中から必要な部分だけ読む」ことはできた
（[cog-partial-read.md](cog-partial-read.md)）。では、**何百枚もあるシーンのうち、
どれを読むべきか**はどう決めるのか。その「選ぶ」段階に何バイト・何リクエストかかるのか。

対象は、[cog-partial-read.md](cog-partial-read.md) と同じ合成ラスターを UTM (EPSG:32654) に投影し、
2×2 に切り分けた 4 シーンの COG（各 2.9 MB、2724×3329 px）。撮影日時は北側 2 枚が 7 月、
南側 2 枚が 8 月で、TIFF のメタデータとして埋め込んである。
**「西側 かつ 7 月」で 4 枚中 1 枚だけが残る**ように、空間と時間をわざとずらしている。

## 0. 出発点の誤解を正す

作業前の理解は次の 2 つだった。

1. 「カタログが無いと、全ファイルを GET してローカルで重なりを判定することになる」
2. 「STAC は bbox や geometry を持っているから、ダウンロードせずに判定できる」

1 は半分だけ正しい。COG なら**ヘッダだけ読めば bbox と CRS は分かる**（全量 GET は不要）。
抜けていたのは 2 つ目の役割で、**撮影日時・雲量・センサーは TIFF ヘッダにそもそも無い**。
ヘッダをいくら読んでも「7 月に撮影」では絞れない。STAC の価値は次の 2 つに分かれる。

- **索引**: 候補を選ぶために読む量を減らす
- **入れ物**: ファイル形式が持てないメタデータ（時間など）を持つ

さらに、作業中における当初の想定、「100 GET が 1 GET になる」は、
**静的カタログについては誤り**だった（4 節で実測）。回数が定数になるのは STAC API か
stac-geoparquet の場合で、静的カタログは Item の数だけ GET が増える。

## 1. 構成 — Catalog → Collection → Item

```
stac/catalog.json                          Catalog（入口。Collection へのリンクだけ）
└── cog-stac-lab-scenes/collection.json    Collection（extent = 4 枚の空間・時間の要約）
    ├── scene_nw/scene_nw.json             Item（1 シーン = GeoJSON Feature 1 つ）
    ├── scene_ne/  scene_se/  scene_sw/
```

| 層 | 役割 | この実装での中身 |
| --- | --- | --- |
| Catalog | 木の根。リンクで子を辿る | Collection 1 つ |
| Collection | 子の範囲の要約（`extent`） | 空間: 4 枚の bbox の外接矩形 / 時間: 7/1〜8/1 |
| Item | 1 シーン。GeoJSON の Feature | geometry・bbox・datetime・proj:*・Asset |
| Asset | 実データへのリンク | COG の相対 href + media type |

Collection の `extent` は、カタログ本体を辿る前に「そもそも管轄外か」を弾くための情報。
STAC API の最初のフィルタはここを見る。

### 純粋ロジックと I/O の分離

```
gdalinfo -json ──► to_scene_metadata ──► SceneMetadata ──► build_item / build_collection / build_catalog
  (I/O, scripts/)   (唯一 gdalinfo の形式を知る)  (契約)         (純粋, src/cogstac/stac_build.py)
```

`SceneMetadata` は**受け取る側（純粋層）が定める契約**。入力元を gdalinfo から別の何かに
変えても、書き換えるのは `to_scene_metadata` だけで済む。
GDAL は Python バインディングではなく `gdalinfo` を subprocess で呼ぶ。venv の Python (3.13) と
brew の GDAL バインディング (3.14) の ABI が合わないため、プロセス境界で依存を切っている。

## 2. GeoJSON に乗る利点と代償

STAC の Item は **GeoJSON の Feature そのもの**。QGIS にドラッグすれば footprint が地図に出るし、
`geopandas.read_file()` でも読める。標準に乗ることで、既存の道具がコード無しで使える。

代償は、**GeoJSON の座標は WGS84 固定**（RFC 7946）であること。

### geometry と bbox は一致しない

UTM の長方形を WGS84 に投影すると、**長方形ではなくなる**（scene_nw で約 0.016° 傾く）。

```
scene_nw の footprint（WGS84）:
  [138.96958, 36.49949], [138.98519, 35.89285], [139.59541, 35.90154], [139.58452, 36.50837]
```

bbox はこの外接矩形なので、データの無い領域を含む。Item が geometry と bbox の両方を持つのはこのため。
隣り合うシーンは**継ぎ目で bbox が重なる**（scene_nw の東端 139.5954 > scene_ne の西端 139.5845）。
「西半分」を検索する bbox の東端を継ぎ目より手前に置かないと、4 枚すべてがヒットする。

### proj 拡張で native CRS を取り戻す

WGS84 に潰すと、本来の CRS・ピクセル数・アフィン変換が失われる。proj 拡張がそれを持つ。

```json
"stac_extensions": ["https://stac-extensions.github.io/projection/v2.0.0/schema.json"],
"properties": {
  "proj:code": "EPSG:32654",
  "proj:shape": [3329, 2724],
  "proj:transform": [20.2251, 0.0, 318152.92, 0.0, -20.2251, 4041268.09],
  "datetime": "2026-07-01T01:30:00Z"
}
```

これで、COG を開かずに「UTM 54N で縦 3329 × 横 2724 px、1 px 約 20 m」と分かる。

## 3. スキーマ検証は意味を見ない

`validate()` が見るのは**形**だけ。次の誤りはすべて**検証を通過した**（または通過する）。
捕まえたのはテスト（`tests/test_stac_build.py`, `tests/test_stac_query.py`）。

| 誤り | 何が起きるか | 番人 |
| --- | --- | --- |
| bbox に UTM のメートル値 | 数値が 4 つあれば通る | 値域のテスト（経度 ±180 / 緯度 ±90） |
| bbox の緯度経度を逆に | 同上 | 順序のテスト |
| `proj:shape` を `[cols, rows]` に | 整数が 2 つあれば通る | 順序のテスト |
| `proj:transform` を GDAL 順に | 数値が 6 つあれば通る | （`stac` キーから取ることで回避） |
| `license="hogehoge"` | 文字列なら通る（空文字列は弾かれる） | SPDX 識別子を使う |
| `union_bbox` が west の max を東端に | extent が半分に縮む | 合成のテスト |
| ジェネレータを 2 回走査 | 2 回目が空 → `min()` が落ちる | ジェネレータを渡すテスト |
| 交差判定が「包含」判定に | 4 枚 → 0 枚 | 半分重なるケース・対称性のテスト |
| 時間の条件が反転 | 7 月で絞ると 8 月が残る | `filter_items` の組み合わせのテスト |

部品（`bboxes_intersect`, `is_within`）を個別に正しく作っても、**組み合わせる所で反転すれば全体は間違う**。
最後の行は、部品のテストが 35 件通っている状態ですり抜けた。

## 4. 計測 — 「選ぶ → 部分読み」の全体コスト

`scripts/measure_stac_flow.py`。ローカルの `range_server.py`（Range 対応）で配信し、
**クライアント側とサーバ側の 2 系統**で突き合わせた。

読み手が 2 種類あるので、メーターも 2 種類要る。

- カタログの JSON を読むのは **pystac**（urllib）→ GDAL の `CPL_DEBUG` には出ない。
  `DefaultStacIO` を継承した `CountingStacIO`（`scripts/counting_stac_io.py`）で数える
- COG を読むのは **GDAL**（libcurl）→ `CPL_DEBUG` を `cogstac.vsi_stats` で集計（タスク 4 の道具）

| 段階 | GET | 転送量 | 備考 |
| --- | ---: | ---: | --- |
| 1. カタログで選ぶ（空間 AND 時間 → 1 枚） | 6 | 8.1 KB | catalog + collection + Item 4 |
| 2. 選んだ 1 枚を部分読み（512² 窓） | 2 | 98.3 KB | ヘッダ + タイル |
| 3. (比較) カタログ無しでヘッダを 4 枚読む | 4 | 65.5 KB | **撮影日時では絞れない** |

クライアント側とサーバ側は全行で一致した。1 の 8.1 KB は JSON 6 ファイルのサイズの合計
（524 + 1,312 + 約 1,577 × 4 バイト）とも一致する。
Item の Asset href（`../../../data/scenes/scene_nw.tif`）は、HTTP 上でも Item 自身の URL を基準に
`http://127.0.0.1:8899/data/scenes/scene_nw.tif` へ正しく解決された。

### 読み解き

**静的カタログは GET 回数を減らさない。** Item が N 枚なら、カタログは N+2 GET、ヘッダ読みは N GET。
カタログの方が多い。

それでもカタログが勝つ点は 2 つ。

1. **バイト数が約 1/10**: Item 1 枚 約 1.6 KB に対し、ヘッダ読み 1 枚 16 KB。
2. **できることが違う**: ヘッダでは時間で絞れない。これは表の数字に出ない差だが、STAC が必要な理由の半分。

流れ全体（1 + 2）は **8 GET / 約 106 KB**。4 枚の COG を丸ごと取りにいくと約 11.6 MB。

### ヘッダ読みの 16 KB は「ヘッダの大きさ」ではない

3 の 65.5 KB は 16,384 バイト × 4、2 の 98.3 KB は 16,384 × 6 で、どちらも 16 KB の倍数。
これは GDAL が `/vsicurl/` で読む最小単位 `CPL_VSIL_CURL_CHUNK_SIZE`（既定 16,384）による。
8,192 にすると先頭の読みは 8 KB、65,536 にすると 64 KB になることを確認した。
ヘッダの中身はもっと小さい（IFD はファイル先頭から 192 バイト目に始まる）。
[cog-partial-read.md](cog-partial-read.md) 0 節の「GDAL が 16KB 境界に切り上げる」と同じ現象。

## 5. スケールの見積もり（推定。実測ではない）

| 方式 | GET | バイト |
| --- | --- | --- |
| 静的カタログ | N + 2 | 約 1.6 KB × N |
| ヘッダを全部読む | N | 約 16 KB × N（時間では絞れない） |
| STAC API / stac-geoparquet | 定数 | 該当分のみ |

静的カタログもヘッダ読みも、**GET の回数は N に比例する**。シーンが数千〜数百万になると、
サーバ側で検索する STAC API か、Item を 1 つの Parquet にまとめる stac-geoparquet が要る。
stac-geoparquet を bbox で引く仕組みは、姉妹リポジトリで測った GeoParquet の row-group 刈り込みと同じ。

4 枚での実測から N 枚を外挿しているだけなので、Item の JSON サイズやサーバの挙動が
変われば数字は変わる。言えるのは**比例するという形**まで。

## 6. 落とし穴メモ（pystac 1.15.2 / STAC 1.1.0 / GDAL 3.12.3 時点）

1. **`proj:epsg` は proj 拡張 v2.0.0 で廃止**され、`proj:code`（`"EPSG:32654"` という文字列）に
   置き換わった。ところが `gdalinfo -json` の `stac` キーは旧仕様の `proj:epsg` を出す。
   「STAC 向け」と書かれた出力でも、どの版の仕様かは別。pystac の `epsg` プロパティに整数を入れると
   `proj:code` に変換して書いてくれる。
2. **transform の並びが 2 種類ある**。`gdalinfo` の `geoTransform` は GDAL 順（原点が先）、
   `stac.proj:transform` は affine 順（ピクセルサイズが先）。proj 拡張が求めるのは後者。
3. **shape の順序**: `gdalinfo` の `size` は `[cols, rows]`、`proj:shape` は `[rows, cols]`。逆向き。
4. **`pystac.Item(properties=None)` は `TypeError`**。pystac は datetime をこの dict に書き込む。
5. **`normalize_hrefs` は Asset の href を触らない**。相対化には `make_all_asset_hrefs_relative()` が別に要る。
   呼ばないと手元の絶対パスが JSON に残る。
6. **`save()` は古いファイルを消さない**。構成を変えて作り直すと孤児ファイルが残る。
7. **Collection を挟むと `get_items()` に `recursive=True` が要る**。付けないと 0 件になり、
   「絞り込みが効きすぎた」ように見える。
8. **`add_child()` に Item を渡すと `STACError`**（pystac の意図的なガード）。Item は `add_item()`。
9. **`gdal_translate -q -srcwin` が画像の外を指すと、タイルを 1 枚も読まずに終了コード 0 で終わる**。
   6000² 用の窓を 2724 幅のシーンに流用し、「部分読み = ヘッダと同じ 16 KB」という誤った値が出た。
   **部分読みの値がヘッダ読みと同値なら疑う**。
10. **pystac の標準 `StacIO` は署名なしの https しか送れない**。非公開の S3 バケットは読めない
    （GDAL の `/vsis3/` は署名できる）。今回ローカルで測ったのはこのため。
    GET 回数とバイト数は HTTP Range の仕組みで決まるので、S3 でも同じ値になる。

## 7. 再現手順

```bash
# 0. 元になる合成ラスターを作る（README の再現手順 1 と同じ）
python3 scripts/make_sample_raster.py --out data/sample_striped.tif

# 1. 4 シーンの COG を作る（UTM に投影 → 2x2 に分割、撮影日時を埋め込む）
python3 scripts/make_stac_scenes.py

# 2. カタログを作る
rm -rf stac && uv run python scripts/build_catalog.py --scene data/scenes/*.tif

# 3. 読み直して絞り込む（4 → 2 → 2 → 1 枚）
uv run python scripts/query_catalog.py

# 4. 「選ぶ → 部分読み」の転送量を測る（range_server は自動で起動・停止）
uv run python scripts/measure_stac_flow.py
```
