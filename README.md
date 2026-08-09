# cog_stac_lab

COG（Cloud Optimized GeoTIFF）の**部分読み出しが本当に転送量を減らすのか**を、
体感や実行時間ではなく **HTTP Range GET の実バイト数**で測るための小さなラボ。
続けて STAC カタログの自作もここで扱う。

> **姉妹リポジトリ: [small_road_network_pipeline](https://github.com/wing787/small_road_network_pipeline)（ベクター側）**
>
> クラウド上の地理データを部分的に読む仕組みは、ラスターもベクターも土台は
> **HTTP Range GET** で同じ。違うのは「何を最小単位に刈るか」だけ。
> 対応表は [docs/cog-partial-read.md](docs/cog-partial-read.md) の 6 節。
>
> 両者に共通する結論は
> **「転送量はファイルレイアウトとクエリの書き方の両方が揃って初めて下がる」**。

## 前提条件

GDAL は **pip で入れない**。ビルド済み C ライブラリと版を合わせる必要があり、
GIS で最も典型的な依存地獄になるため（コンテナ化が標準解とされる理由でもある）。

```bash
brew install gdal          # gdal_translate / gdalinfo と Python バインディング
gdalinfo --version         # 3.12.3 で確認
python3 -c "from osgeo import gdal; print(gdal.__version__)"
```

純粋ロジックのテストだけなら GDAL 不要:

```bash
uv sync
uv run pytest
```

## 再現手順

```bash
# 1. 素の GeoTIFF（strip レイアウト）を作る
python3 scripts/make_sample_raster.py --out data/sample_striped.tif
gdalinfo data/sample_striped.tif | grep Block     # → Block=6000x1（全幅 x 1行）

# 2. COG 化（タイル化 + overview + 圧縮がまとめて付く）
gdal_translate -of COG -co COMPRESS=ZSTD -co PREDICTOR=2 -co LEVEL=9 \
    data/sample_striped.tif data/sample_cog_zstd.tif
gdalinfo data/sample_cog_zstd.tif | grep -E "Block|Overviews"

# 3. Range 対応サーバを立てる（python -m http.server は Range 非対応なので不可）
python3 scripts/range_server.py --root data --port 8899 &

# 4. 転送量を測る
python3 scripts/measure_cog_transfer.py \
    --base /vsicurl/http://127.0.0.1:8899 \
    sample_striped.tif sample_cog_zstd.tif
```

S3 上で測る場合（GDAL は SSO トークンを自動更新しないので認証情報を環境変数へ）:

```bash
eval "$(aws configure export-credentials --format env)"
export AWS_REGION=ap-northeast-1
python3 scripts/measure_cog_transfer.py \
    --base /vsis3/<bucket>/cog sample_striped.tif sample_cog_zstd.tif
```

## 結果の要点

詳細は [docs/cog-partial-read.md](docs/cog-partial-read.md)。結論だけ:

| 問い | strip | COG(ZSTD+PREDICTOR) | 削減 |
| --- | ---: | ---: | ---: |
| 512² の窓を読む | 6.19 MB / 3 req | 0.17 MB / 2 req | **37×** |
| 全域を 750×750 で俯瞰 | 28.72 MB / **644 req** | 0.25 MB / **1 req** | **117×** |

（S3 実測。ローカル HTTP でもほぼ同値）
