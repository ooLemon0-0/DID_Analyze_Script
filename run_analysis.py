# -*- coding: utf-8 -*-
"""
社区食堂空间 DID - Python 一键运行版

用法：
1. 把本文件放在项目根目录。
2. 把所有原始 .rar 文件也放在同一根目录，文件名不限。
3. 运行 04_运行Python版.bat。

本脚本自动：
- 扫描根目录全部 RAR；
- 分别解压到 .cache/extracted/；
- 汇总其中所有 CSV 为 raw_all；
- 运行与 Jupyter Notebook 相同的清洗、空间匹配、平行趋势、规格选择、DID 和异质性逻辑；
- 结果写入 processed/；
- 图形写入 processed/figures/。
"""

from pathlib import Path
import os
import shutil
import subprocess
import warnings
import sys
import re

import matplotlib
matplotlib.use("Agg")

import duckdb
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib as mpl
from matplotlib import font_manager

from sklearn.neighbors import BallTree
import statsmodels.api as sm
from scipy.stats import chi2
from linearmodels.iv import AbsorbingLS

warnings.filterwarnings("ignore")

REPORT_MODE = False

def vprint(*args, **kwargs):
    if not REPORT_MODE:
        print(*args, **kwargs, flush=True)

# ============================================================
# 项目路径：始终使用本脚本所在目录，不依赖用户名/盘符
# ============================================================
ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "processed"
OUT_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = ROOT / "dzdp_did.duckdb"
DUCKDB_TMP = ROOT / "duckdb_tmp"
DUCKDB_TMP.mkdir(parents=True, exist_ok=True)
EXTRACT_ROOT = ROOT / ".cache" / "extracted"
EXTRACT_ROOT.mkdir(parents=True, exist_ok=True)
FIG_DIR = OUT_DIR / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 中文字体
# ============================================================
def setup_chinese_font():
    font_files = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    selected_font = None
    for font_path in font_files:
        if Path(font_path).exists():
            try:
                font_manager.fontManager.addfont(font_path)
                selected_font = font_manager.FontProperties(fname=font_path).get_name()
                break
            except Exception:
                pass
    if selected_font is None:
        available_fonts = {f.name for f in font_manager.fontManager.ttflist}
        for name in ["Microsoft YaHei", "SimHei", "SimSun", "Noto Sans CJK SC", "Source Han Sans CN"]:
            if name in available_fonts:
                selected_font = name
                break
    if selected_font:
        mpl.rcParams["font.family"] = "sans-serif"
        mpl.rcParams["font.sans-serif"] = [selected_font, "DejaVu Sans"]
        mpl.rcParams["axes.unicode_minus"] = False
        vprint("[OK] Matplotlib 中文字体:", selected_font)
    else:
        vprint("[WARN] 未找到常用中文字体，图中文字可能显示异常")

setup_chinese_font()

# Python 版不弹出图形窗口，自动保存每一次 plt.show() 对应的图
_figure_counter = 0

def _save_current_figure(*args, **kwargs):
    global _figure_counter
    _figure_counter += 1
    fig = plt.gcf()
    path = FIG_DIR / f"figure_{_figure_counter:02d}.png"
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)
    vprint(f"[FIG] {path.name}")

plt.show = _save_current_figure

# ============================================================
# RAR 自动发现与解压：根目录下文件名不限
# ============================================================
def find_extractor():
    candidates = [
        ROOT / "tools" / "7zip" / "7z.exe",
        shutil.which("7z"),
        shutil.which("7z.exe"),
        shutil.which("WinRAR"),
        shutil.which("WinRAR.exe"),
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
        Path(r"C:\Program Files\WinRAR\WinRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\WinRAR.exe"),
    ]
    for p in candidates:
        if p and Path(p).exists():
            return Path(p)
    return None


def extract_one_rar(rar_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    if list(out_dir.rglob("*.csv")):
        vprint(f"[SKIP] 已存在 CSV: {rar_path.name}")
        return

    extractor = find_extractor()
    if extractor is not None:
        exe_name = extractor.name.lower()
        if "7z" in exe_name:
            cmd = [str(extractor), "x", str(rar_path), f"-o{out_dir}", "-y"]
        else:
            cmd = [str(extractor), "x", "-o+", str(rar_path), str(out_dir) + "\\"]
        vprint(f"[EXTRACT] {rar_path.name}")
        subprocess.run(cmd, check=True)
        return

    # Windows 10/11 通常自带 tar.exe；部分版本可读取 RAR，先尝试作为兜底
    tar_exe = shutil.which("tar") or shutil.which("tar.exe")
    if tar_exe:
        vprint(f"[EXTRACT] 尝试 Windows tar: {rar_path.name}")
        try:
            subprocess.run([tar_exe, "-xf", str(rar_path), "-C", str(out_dir)], check=True)
            if list(out_dir.rglob("*.csv")):
                return
        except Exception:
            pass

    raise RuntimeError(
        "没有找到可用的 RAR 解压程序。请先双击 01_一键安装环境.bat，"
        "它会尝试安装 7-Zip。"
    )


rar_files = sorted(ROOT.glob("*.rar"))
if not rar_files:
    raise RuntimeError(
        f"项目根目录没有找到任何 .rar 文件：{ROOT}\n"
        "请把原始 RAR 文件直接放到项目根目录，文件名可以任意。"
    )

vprint(f"[INFO] 找到 {len(rar_files)} 个 RAR:")
for p in rar_files:
    vprint("   ", p.name)

all_csvs = []
for idx, rar_path in enumerate(rar_files, start=1):
    safe_stem = re.sub(r'[^0-9A-Za-z_\-\u4e00-\u9fff]+', '_', rar_path.stem)
    out_dir = EXTRACT_ROOT / f"{idx:02d}_{safe_stem}"
    extract_one_rar(rar_path, out_dir)
    all_csvs.extend(sorted(out_dir.rglob("*.csv")))

# 去重路径
all_csvs = list(dict.fromkeys(p.resolve() for p in all_csvs))
if not all_csvs:
    raise RuntimeError("RAR 已处理，但没有发现任何 CSV。请检查压缩包内容。")

vprint(f"[INFO] 共发现 {len(all_csvs)} 个 CSV")
for p in all_csvs:
    vprint("   ", p)

# ============================================================
# DuckDB：直接把所有 RAR 中的 CSV 统一注册成 raw_all
# ============================================================
con = duckdb.connect(str(DB_PATH))
con.execute("PRAGMA threads=8")
con.execute("PRAGMA memory_limit='8GB'")
con.execute(f"SET temp_directory='{DUCKDB_TMP.as_posix()}'")


def duckdb_file_list(paths):
    return "[" + ",".join(
        "'" + Path(p).as_posix().replace("'", "''") + "'" for p in paths
    ) + "]"

csv_list = duckdb_file_list(all_csvs)
con.execute(f"""
CREATE OR REPLACE VIEW raw_all AS
SELECT *
FROM read_csv_auto(
    {csv_list},
    all_varchar=true,
    union_by_name=true,
    filename=true,
    ignore_errors=true
)
""")

vprint("[OK] raw_all 注册完成")


# ============================================================
# 配置
# ============================================================

SHOPS_PATH = OUT_DIR / "shops_clean.parquet"
FACILITY_PATH = OUT_DIR / "facilities_clean.parquet"
PANEL_PATH = OUT_DIR / "spatial_panel_v2.parquet"

EARTH_RADIUS_KM = 6371.0088


SCHEMES = {
    "R100": {
        "treat_max": 0.1,
        "control_max": 0.5,
        "label": "0-100m vs 100-500m",
    },

    "R200": {
        "treat_max": 0.2,
        "control_max": 0.6,
        "label": "0-200m vs 200-600m",
    },

    "R300": {
        "treat_max": 0.3,
        "control_max": 0.7,
        "label": "0-300m vs 300-700m",
    },

    "R500": {
        "treat_max": 0.5,
        "control_max": 1.0,
        "label": "0-500m vs 500-1000m",
    },
}


# ============================================================
# 1. 商户清洗
#
# 保留当前识别和去重逻辑，不做修改
# ============================================================

clean_sql = r"""
WITH typed AS (

    SELECT

        TRY_CAST(year AS INTEGER) AS year,

        NULLIF(
            TRIM(
                CAST(shop_id AS VARCHAR)
            ),
            ''
        ) AS shop_id,

        NULLIF(TRIM(name), '') AS name,
        NULLIF(TRIM(alias), '') AS alias,

        TRY_CAST(
            lon_wgs AS DOUBLE
        ) AS lon_wgs,

        TRY_CAST(
            lat_wgs AS DOUBLE
        ) AS lat_wgs,

        NULLIF(TRIM(PAC), '') AS PAC,

        NULLIF(
            TRIM(province19),
            ''
        ) AS province19,

        NULLIF(
            TRIM(city19),
            ''
        ) AS city19,

        NULLIF(
            TRIM(county19),
            ''
        ) AS county19,

        NULLIF(
            TRIM(big_cate),
            ''
        ) AS big_cate,

        NULLIF(
            TRIM(small_cate),
            ''
        ) AS small_cate,

        NULLIF(
            TRIM(address),
            ''
        ) AS address,


        CASE
            WHEN TRY_CAST(
                avg_price AS DOUBLE
            ) BETWEEN 0 AND 5000

            THEN TRY_CAST(
                avg_price AS DOUBLE
            )

            ELSE NULL
        END AS avg_price,


        CASE
            WHEN TRY_CAST(
                stars AS DOUBLE
            ) BETWEEN 0 AND 5

            THEN TRY_CAST(
                stars AS DOUBLE
            )

            ELSE NULL
        END AS stars,


        CASE
            WHEN TRY_CAST(
                all_remarks AS DOUBLE
            ) >= 0

            THEN TRY_CAST(
                all_remarks AS DOUBLE
            )

            ELSE NULL
        END AS all_remarks,


        CASE
            WHEN TRY_CAST(
                review_count AS DOUBLE
            ) >= 0

            THEN TRY_CAST(
                review_count AS DOUBLE
            )

            ELSE NULL
        END AS review_count,


        CASE

            WHEN LOWER(
                TRIM(is_closed)
            ) IN (
                'yes','true','1'
            )
            THEN 1

            WHEN LOWER(
                TRIM(is_closed)
            ) IN (
                'no','false','0'
            )
            THEN 0

            ELSE NULL

        END AS is_closed,


        CONCAT_WS(
            ' ',
            COALESCE(name, ''),
            COALESCE(alias, '')
        ) AS identity_text,


        CONCAT_WS(
            ' ',
            COALESCE(tag, ''),
            COALESCE(tag2, ''),
            COALESCE(tags, '')
        ) AS tag_text,


        CONCAT_WS(
            ' ',
            COALESCE(navigation, ''),
            COALESCE(description, '')
        ) AS context_text,


        CONCAT_WS(
            ' ',
            COALESCE(name, ''),
            COALESCE(big_cate, ''),
            COALESCE(small_cate, ''),
            COALESCE(tag, ''),
            COALESCE(tag2, ''),
            COALESCE(tags, '')
        ) AS food_text,

        filename AS source_file

    FROM raw_all
),

valid AS (

    SELECT
        *,

        CASE

            WHEN all_remarks IS NULL
             AND review_count IS NULL

            THEN NULL

            ELSE GREATEST(
                COALESCE(
                    all_remarks,
                    0
                ),
                COALESCE(
                    review_count,
                    0
                )
            )

        END AS comments

    FROM typed

    WHERE year BETWEEN 2012 AND 2022

      AND shop_id IS NOT NULL

      AND lon_wgs BETWEEN 73 AND 136
      AND lat_wgs BETWEEN 3 AND 54
),

classified AS (

    SELECT
        *,

        CASE

            WHEN REGEXP_MATCHES(
                identity_text,

                '社区食堂|社区餐厅|社区助餐|助餐中心|助餐点|助餐站|助餐服务|长者食堂|老人食堂|老年食堂|养老食堂|幸福食堂|爱心食堂|惠民食堂'
            )
            THEN 3

            WHEN REGEXP_MATCHES(
                tag_text,

                '社区食堂|社区助餐|助餐中心|长者食堂|老年食堂|幸福食堂|爱心食堂'
            )
            THEN 2

            WHEN REGEXP_MATCHES(
                context_text,

                '社区食堂|社区助餐|助餐中心|助餐点|长者食堂|老年食堂|幸福食堂|爱心食堂'
            )
            THEN 1

            ELSE 0

        END AS facility_confidence,


        CASE

            WHEN REGEXP_MATCHES(
                CONCAT_WS(
                    ' ',
                    identity_text,
                    tag_text
                ),

                '员工食堂|学校食堂|医院食堂|公司食堂|工厂食堂|单位食堂|机关食堂|食堂承包|自助餐厅'
            )

            THEN 1

            ELSE 0

        END AS facility_excluded,


        CASE

            WHEN big_cate = '美食'

              OR REGEXP_MATCHES(
                  food_text,

                  '餐厅|餐饮|小吃|快餐|火锅|烧烤|饮品|面包|甜点'
              )

            THEN 1

            ELSE 0

        END AS is_food

    FROM valid
),

classified2 AS (

    SELECT
        *,

        CASE
            WHEN facility_confidence >= 2
             AND facility_excluded = 0
            THEN 1
            ELSE 0
        END AS is_community_canteen,


        CASE

            WHEN is_food = 1

             AND (

                 REGEXP_MATCHES(
                     food_text,

                     '小吃|快餐|简餐|面食|粉面|米线|馄饨|饺子|便当|盖浇饭'
                 )

                 OR avg_price <= 30

             )

            THEN 1

            ELSE 0

        END AS is_homogeneous_fastfood

    FROM classified
),

scored AS (

    SELECT
        *,

        facility_confidence * 100

        + CAST(
            name IS NOT NULL
            AS INTEGER
        )

        + CAST(
            city19 IS NOT NULL
            AS INTEGER
        )

        + CAST(
            county19 IS NOT NULL
            AS INTEGER
        )

        + CAST(
            avg_price IS NOT NULL
            AS INTEGER
        )

        + CAST(
            stars IS NOT NULL
            AS INTEGER
        )

        + CAST(
            comments IS NOT NULL
            AS INTEGER
        )

        AS quality_score

    FROM classified2
),

dedup AS (

    SELECT *

    FROM scored

    QUALIFY ROW_NUMBER() OVER (

        PARTITION BY
            year,
            shop_id

        ORDER BY
            quality_score DESC,
            source_file

    ) = 1
)

SELECT

    year,
    shop_id,
    name,

    lon_wgs,
    lat_wgs,

    PAC,
    province19,
    city19,
    county19,

    address,

    big_cate,
    small_cate,

    avg_price,

    stars,

    CASE
        WHEN stars > 0
        THEN stars
        ELSE NULL
    END AS valid_stars,

    all_remarks,
    review_count,
    comments,

    is_closed,

    facility_confidence,
    facility_excluded,

    is_community_canteen,

    is_food,
    is_homogeneous_fastfood,

    source_file

FROM dedup
"""


vprint("1/5 清洗商户数据...")

con.execute(f"""
COPY (
    {clean_sql}
)
TO '{SHOPS_PATH.as_posix()}'
(
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    ROW_GROUP_SIZE 250000
)
""")


con.execute(f"""
CREATE OR REPLACE VIEW shops_clean AS
SELECT *
FROM read_parquet(
    '{SHOPS_PATH.as_posix()}'
)
""")


# ============================================================
# 2. 社区食堂设施
#
# opening_year =
# 第一次作为社区食堂出现的年份
#
# 坐标使用多年 median
# ============================================================

vprint("2/5 生成社区食堂设施...")

con.execute(f"""
COPY (

    SELECT

        shop_id AS facility_id,

        MIN(year)
            AS opening_year,

        MAX(year)
            AS last_seen_year,

        COUNT(
            DISTINCT year
        ) AS observed_years,

        ANY_VALUE(name)
            AS name,

        MEDIAN(lon_wgs)
            AS lon_wgs,

        MEDIAN(lat_wgs)
            AS lat_wgs,

        ANY_VALUE(PAC)
            AS PAC,

        ANY_VALUE(province19)
            AS province19,

        ANY_VALUE(city19)
            AS city19,

        ANY_VALUE(county19)
            AS county19,

        MAX(
            facility_confidence
        ) AS facility_confidence

    FROM shops_clean

    WHERE is_community_canteen = 1

    GROUP BY shop_id

)
TO '{FACILITY_PATH.as_posix()}'
(
    FORMAT PARQUET,
    COMPRESSION ZSTD
)
""")


con.execute(f"""
CREATE OR REPLACE VIEW facilities_clean AS
SELECT *
FROM read_parquet(
    '{FACILITY_PATH.as_posix()}'
)
""")


fac = con.execute("""
SELECT *
FROM facilities_clean
ORDER BY facility_id
""").df()

fac["facility_id"] = (
    fac["facility_id"]
    .astype(str)
)


vprint(
    "facilities:",
    len(fac)
)


# ============================================================
# 3. BallTree 空间匹配
#
# 这里不保存几千万 pair
# 每一年匹配后直接聚合
#
# 逻辑和文档一致：
#
# R100  0-100 / 100-500
# R200  0-200 / 200-600
# R300  0-300 / 300-700
# R500  0-500 / 500-1000
#
# control 如果落入任意其他食堂 treatment
# 就从 control 删除
# ============================================================

vprint("3/5 空间匹配并聚合...")

fac_rad = np.radians(
    fac[
        [
            "lat_wgs",
            "lon_wgs"
        ]
    ].to_numpy()
)


annual_agg = []


for year in range(
    2012,
    2023
):

    vprint(
        f"  spatial year {year}"
    )

    shops = con.execute(f"""
        SELECT

            shop_id,
            lon_wgs,
            lat_wgs,

            is_food,
            is_homogeneous_fastfood,
            is_closed,

            avg_price,
            valid_stars,
            comments

        FROM shops_clean

        WHERE year = {year}
    """).df()

    if shops.empty:
        continue

    shops["shop_id"] = (
        shops["shop_id"]
        .astype(str)
    )

    coords = np.radians(
        shops[
            [
                "lat_wgs",
                "lon_wgs"
            ]
        ].to_numpy()
    )

    tree = BallTree(
        coords,
        metric="haversine"
    )

    indexes, distances = (
        tree.query_radius(
            fac_rad,

            r=(
                1.0
                / EARTH_RADIUS_KM
            ),

            return_distance=True,

            sort_results=True
        )
    )


    pair_blocks = []


    for i, (
        idx,
        dist
    ) in enumerate(
        zip(
            indexes,
            distances
        )
    ):

        if len(idx) == 0:
            continue

        x = shops.iloc[
            idx
        ].copy()

        x["facility_id"] = (
            fac.iloc[i][
                "facility_id"
            ]
        )

        x["opening_year"] = (
            fac.iloc[i][
                "opening_year"
            ]
        )

        x["distance_km"] = (
            dist
            * EARTH_RADIUS_KM
        )

        # 食堂自身排除
        x = x[
            x["shop_id"]
            != x["facility_id"]
        ]

        pair_blocks.append(x)


    if not pair_blocks:
        continue


    pairs = pd.concat(
        pair_blocks,
        ignore_index=True
    )


    # ========================================================
    # 每个 scheme 分别处理
    # ========================================================

    for scheme, cfg in SCHEMES.items():

        treat_max = (
            cfg["treat_max"]
        )

        control_max = (
            cfg["control_max"]
        )


        x = pairs[
            pairs["distance_km"]
            <= control_max
        ].copy()


        x["scheme"] = scheme


        # ----------------------------------------------------
        # 环带
        #
        # <= treat_max        Treatment
        # > treat_max         Control
        # <= control_max
        # ----------------------------------------------------

        x["treat_ring"] = (
            x["distance_km"]
            <= treat_max
        ).astype("int8")


        # ----------------------------------------------------
        # control contamination
        #
        # 一个 shop 只要处于任何设施 treatment ring
        # 就不能成为其他设施 control
        # ----------------------------------------------------

        treated_shop_ids = set(
            x.loc[
                x["treat_ring"] == 1,
                "shop_id"
            ]
        )


        x = x[
            ~(
                (x["treat_ring"] == 0)
                &
                (
                    x["shop_id"]
                    .isin(
                        treated_shop_ids
                    )
                )
            )
        ].copy()


        # ----------------------------------------------------
        # active / category indicator
        #
        # DuckDB -> pandas 后整数列可能是 nullable Int64，
        # 内部可能包含 pd.NA。
        # 在生成 0/1 指标之前统一处理掉 NA。
        #
        # 规则：
        # is_closed 缺失 -> 暂按未闭店处理
        # is_food 缺失 -> 非餐饮
        # is_homogeneous_fastfood 缺失 -> 非快餐小吃
        # ----------------------------------------------------

        x["is_closed_num"] = (
            pd.to_numeric(
                x["is_closed"],
                errors="coerce"
            )
            .fillna(0)
            .astype("int8")
        )

        x["is_food_num"] = (
            pd.to_numeric(
                x["is_food"],
                errors="coerce"
            )
            .fillna(0)
            .astype("int8")
        )

        x["is_fastfood_num"] = (
            pd.to_numeric(
                x["is_homogeneous_fastfood"],
                errors="coerce"
            )
            .fillna(0)
            .astype("int8")
        )


        # 活跃商户
        x["active"] = np.where(
            x["is_closed_num"] == 1,
            0,
            1
        ).astype("int8")


        # 活跃餐饮商户
        x["food_active"] = np.where(
            (x["active"] == 1)
            &
            (x["is_food_num"] == 1),
            1,
            0
        ).astype("int8")


        # 活跃同质化快餐小吃
        x["fastfood_active"] = np.where(
            (x["active"] == 1)
            &
            (x["is_fastfood_num"] == 1),
            1,
            0
        ).astype("int8")


        # 活跃非餐饮
        x["nonfood_active"] = np.where(
            (x["active"] == 1)
            &
            (x["is_food_num"] == 0),
            1,
            0
        ).astype("int8")


        # 闭店商户
        x["closed"] = np.where(
            x["is_closed_num"] == 1,
            1,
            0
        ).astype("int8")


        # ----------------------------------------------------
        # 聚合 facility × ring × year
        # ----------------------------------------------------

        g = (
            x.groupby(
                [
                    "facility_id",
                    "opening_year",
                    "scheme",
                    "treat_ring"
                ],
                as_index=False
            )
            .agg(

                active_count=(
                    "active",
                    "sum"
                ),

                food_count=(
                    "food_active",
                    "sum"
                ),

                fastfood_count=(
                    "fastfood_active",
                    "sum"
                ),

                nonfood_count=(
                    "nonfood_active",
                    "sum"
                ),

                closed_count=(
                    "closed",
                    "sum"
                ),

                comments=(
                    "comments",
                    "sum"
                ),

                avg_comments_per_shop=(
                    "comments",
                    "mean"
                ),

                avg_price=(
                    "avg_price",
                    "mean"
                ),

                avg_stars=(
                    "valid_stars",
                    "mean"
                ),
            )
        )


        g["year"] = year

        annual_agg.append(g)


ring_year = pd.concat(
    annual_agg,
    ignore_index=True
)


# ============================================================
# 4. 完整平衡面板
# ============================================================

vprint("4/5 构造完整平衡面板...")


grid = pd.MultiIndex.from_product(
    [
        fac["facility_id"],
        list(SCHEMES.keys()),
        [0, 1],
        range(2012, 2023),
    ],

    names=[
        "facility_id",
        "scheme",
        "treat_ring",
        "year",
    ]
).to_frame(
    index=False
)


meta_cols = [
    "facility_id",
    "opening_year",
    "name",
    "lon_wgs",
    "lat_wgs",
    "PAC",
    "province19",
    "city19",
    "county19",
]


panel = grid.merge(
    fac[meta_cols],
    on="facility_id",
    how="left"
)


ring_year["facility_id"] = (
    ring_year["facility_id"]
    .astype(str)
)


panel = panel.merge(
    ring_year.drop(
        columns=[
            "opening_year"
        ]
    ),

    on=[
        "facility_id",
        "scheme",
        "treat_ring",
        "year"
    ],

    how="left"
)


# ============================================================
# 计数变量补0
# ============================================================

count_cols = [
    "active_count",
    "food_count",
    "fastfood_count",
    "nonfood_count",
    "closed_count",
    "comments",
]


panel[count_cols] = (
    panel[count_cols]
    .fillna(0)
)


# ============================================================
# 连续变量沿用之前插值方法
# ============================================================

continuous_cols = [
    "avg_comments_per_shop",
    "avg_price",
    "avg_stars",
]


panel = panel.sort_values(
    [
        "facility_id",
        "scheme",
        "treat_ring",
        "year",
    ]
)


for col in continuous_cols:

    panel[col] = (
        panel.groupby(
            [
                "facility_id",
                "scheme",
                "treat_ring",
            ]
        )[col]
        .transform(
            lambda s:
            s.interpolate(
                limit_direction="both"
            )
        )
    )


    year_med = (
        panel.groupby(
            "year"
        )[col]
        .transform(
            "median"
        )
    )


    panel[col] = (
        panel[col]
        .fillna(
            year_med
        )
        .fillna(
            panel[col].median()
        )
    )


# ============================================================
# DID / event time
# ============================================================

panel["rel_year"] = (
    panel["year"]
    - panel["opening_year"]
)


panel["post"] = (
    panel["rel_year"] >= 0
).astype("int8")


panel["did"] = (
    panel["treat_ring"]
    * panel["post"]
)


# ============================================================
# 5. survival index
#
# 基准：
# 同一 facility + scheme + ring
# 所有政策前年份 active_count 的平均值
# ============================================================

vprint("5/5 生成 survival_index...")


baseline = (
    panel[
        panel["rel_year"] < 0
    ]
    .groupby(
        [
            "facility_id",
            "scheme",
            "treat_ring"
        ],
        as_index=False
    )["active_count"]
    .mean()
    .rename(
        columns={
            "active_count":
            "pre_active_baseline"
        }
    )
)


panel = panel.merge(
    baseline,

    on=[
        "facility_id",
        "scheme",
        "treat_ring"
    ],

    how="left"
)


panel["survival_index"] = np.where(

    panel[
        "pre_active_baseline"
    ] > 0,

    panel["active_count"]
    /
    panel[
        "pre_active_baseline"
    ],

    np.nan
)


# survival 属连续变量
# 按文档思路继续补值

panel = panel.sort_values(
    [
        "facility_id",
        "scheme",
        "treat_ring",
        "year"
    ]
)


panel["survival_index"] = (
    panel.groupby(
        [
            "facility_id",
            "scheme",
            "treat_ring"
        ]
    )["survival_index"]
    .transform(
        lambda s:
        s.interpolate(
            limit_direction="both"
        )
    )
)


year_med = (
    panel.groupby(
        "year"
    )["survival_index"]
    .transform(
        "median"
    )
)


panel["survival_index"] = (
    panel[
        "survival_index"
    ]
    .fillna(
        year_med
    )
    .fillna(
        panel[
            "survival_index"
        ].median()
    )
)


# ============================================================
# LOG outcome
# ============================================================

panel["ln_active_count"] = (
    np.log1p(
        panel["active_count"]
    )
)

panel["ln_food_count"] = (
    np.log1p(
        panel["food_count"]
    )
)

panel["ln_fastfood_count"] = (
    np.log1p(
        panel["fastfood_count"]
    )
)

panel["ln_comments"] = (
    np.log1p(
        panel["comments"]
    )
)

panel[
    "ln_avg_comments_per_shop"
] = (
    np.log1p(
        panel[
            "avg_comments_per_shop"
        ]
    )
)


# ============================================================
# 保存
# ============================================================

panel = (
    panel.reset_index(
        drop=True
    )
)


panel.to_parquet(
    PANEL_PATH,
    compression="zstd",
    index=False
)


con.execute(f"""
CREATE OR REPLACE VIEW spatial_panel_v2 AS

SELECT *
FROM read_parquet(
    '{PANEL_PATH.as_posix()}'
)
""")


# ============================================================
# CELL 3
# 全部设施级平行趋势筛选
# ============================================================

OUTCOMES = {
    "ln_active_count": "活跃商户数（对数）",
    "ln_food_count": "餐饮商户数（对数）",
    "ln_fastfood_count": "同质化快餐小吃（对数）",
    "survival_index": "存活率指数",
    "avg_price": "平均价格",
    "ln_comments": "评论数量（对数）",
    "ln_avg_comments_per_shop": "单店平均评论（对数）",
}

WINDOWS = [3, 5]


def facility_pretrend(panel, scheme, outcome, window):
    d = panel[
        panel["scheme"] == scheme
    ][
        [
            "facility_id",
            "opening_year",
            "year",
            "rel_year",
            "treat_ring",
            outcome,
        ]
    ].copy()

    wide = (
        d.pivot_table(
            index=["facility_id", "opening_year", "year", "rel_year"],
            columns="treat_ring",
            values=outcome,
            aggfunc="first",
        )
        .reset_index()
    )

    wide.columns.name = None
    wide = wide.rename(columns={0: "control", 1: "treat"})

    if "control" not in wide.columns:
        wide["control"] = np.nan
    if "treat" not in wide.columns:
        wide["treat"] = np.nan

    wide["gap"] = wide["treat"] - wide["control"]
    results = []

    for facility_id, g in wide.groupby("facility_id"):
        pre = (
            g[g["rel_year"].between(-window, -1)]
            .dropna(subset=["treat", "control", "gap"])
            .copy()
        )

        post = (
            g[g["rel_year"].between(0, window)]
            .dropna(subset=["treat", "control"])
            .copy()
        )

        n_pre = pre["rel_year"].nunique()
        n_post = post["rel_year"].nunique()

        row = {
            "facility_id": str(facility_id),
            "scheme": scheme,
            "window": window,
            "outcome": outcome,
            "opening_year": g["opening_year"].iloc[0],
            "n_pre_years": n_pre,
            "n_post_years": n_post,
            "testable": 0,
            "pre_slope": np.nan,
            "pretrend_pvalue": np.nan,
            "mean_abs_pre_gap": np.nan,
            "parallel_pass": 0,
            "fail_reason": "",
        }

        if n_pre < 3:
            row["fail_reason"] = "insufficient_pre"
            results.append(row)
            continue

        if n_post < 1:
            row["fail_reason"] = "no_post"
            results.append(row)
            continue

        row["testable"] = 1
        row["mean_abs_pre_gap"] = pre["gap"].abs().mean()

        if np.nanstd(pre["gap"].to_numpy()) < 1e-12:
            slope = 0.0
            pvalue = 1.0
        else:
            X = sm.add_constant(pre["rel_year"].astype(float))
            model = sm.OLS(pre["gap"].astype(float), X).fit()
            slope = float(model.params["rel_year"])
            pvalue = float(model.pvalues["rel_year"])

        row["pre_slope"] = slope
        row["pretrend_pvalue"] = pvalue
        row["parallel_pass"] = int(pvalue >= 0.10)

        if pvalue < 0.10:
            row["fail_reason"] = "significant_pretrend"

        results.append(row)

    return pd.DataFrame(results)


facility_results = []

for window in WINDOWS:
    for scheme in SCHEMES:
        for outcome in OUTCOMES:
            facility_results.append(
                facility_pretrend(
                    panel=panel,
                    scheme=scheme,
                    outcome=outcome,
                    window=window,
                )
            )

facility_screen = pd.concat(facility_results, ignore_index=True)

facility_meta = (
    fac[
        [
            "facility_id",
            "name",
            "lon_wgs",
            "lat_wgs",
            "province19",
            "city19",
            "county19",
        ]
    ]
    .rename(
        columns={
            "name": "facility_name",
            "lon_wgs": "facility_lon",
            "lat_wgs": "facility_lat",
        }
    )
)

facility_screen = facility_screen.merge(
    facility_meta,
    on="facility_id",
    how="left",
)

facility_summary = (
    facility_screen[facility_screen["testable"] == 1]
    .groupby(["window", "scheme", "outcome"], as_index=False)
    .agg(
        testable_facilities=("facility_id", "nunique"),
        passed_facilities=("parallel_pass", "sum"),
        median_facility_pvalue=("pretrend_pvalue", "median"),
        mean_abs_pre_gap=("mean_abs_pre_gap", "mean"),
    )
)

facility_summary["facility_pass_rate"] = (
    facility_summary["passed_facilities"]
    / facility_summary["testable_facilities"]
)

facility_summary["facility_pass_rate_pct"] = (
    facility_summary["facility_pass_rate"] * 100
)

facility_summary["outcome_name"] = facility_summary["outcome"].map(OUTCOMES)
facility_summary["scheme_label"] = facility_summary["scheme"].map(
    {k: v["label"] for k, v in SCHEMES.items()}
)

facility_screen.to_csv(
    OUT_DIR / "scheme_facility_pretrend_screen.csv",
    index=False,
    encoding="utf-8-sig",
)

facility_summary.to_csv(
    OUT_DIR / "scheme_pretrend_screen_summary.csv",
    index=False,
    encoding="utf-8-sig",
)


# ============================================================
# Event dummy name
# ============================================================

def event_name(k):

    if k < 0:
        return (
            f"event_m{abs(k)}"
        )

    return (
        f"event_p{k}"
    )


# ============================================================
# 单个 pooled Event Study
# ============================================================

def pooled_event_study(
    panel,
    facility_screen,
    scheme,
    outcome,
    window
):

    # --------------------------------------------------------
    # 设施级通过
    # --------------------------------------------------------

    passed_ids = (
        facility_screen[
            (
                facility_screen[
                    "scheme"
                ] == scheme
            )
            &
            (
                facility_screen[
                    "window"
                ] == window
            )
            &
            (
                facility_screen[
                    "outcome"
                ] == outcome
            )
            &
            (
                facility_screen[
                    "parallel_pass"
                ] == 1
            )
        ][
            "facility_id"
        ]
        .astype(str)
        .unique()
    )


    if len(passed_ids) == 0:
        return None, None


    d = panel[
        (
            panel["scheme"]
            == scheme
        )
        &
        (
            panel[
                "facility_id"
            ].isin(
                passed_ids
            )
        )
        &
        (
            panel[
                "rel_year"
            ].between(
                -window,
                window
            )
        )
    ].copy()


    d = d.dropna(
        subset=[
            outcome
        ]
    )


    if d.empty:
        return None, None


    # --------------------------------------------------------
    # Event dummies
    #
    # -1 不创建，作为 baseline
    # --------------------------------------------------------

    event_times = [

        k

        for k in range(
            -window,
            window + 1
        )

        if k != -1
    ]


    event_cols = []


    for k in event_times:

        col = event_name(k)


        d[col] = (
            (
                d[
                    "rel_year"
                ] == k
            )
            &
            (
                d[
                    "treat_ring"
                ] == 1
            )
        ).astype(float)


        # 避免完全空的event列
        if (
            d[col].sum()
            > 0
        ):

            event_cols.append(
                col
            )


    # --------------------------------------------------------
    # FE
    # --------------------------------------------------------

    d["facility_ring_fe"] = (
        d[
            "facility_id"
        ].astype(str)
        +
        "_"
        +
        d[
            "treat_ring"
        ].astype(str)
    )


    d["facility_year_fe"] = (
        d[
            "facility_id"
        ].astype(str)
        +
        "_"
        +
        d[
            "year"
        ].astype(str)
    )


    absorb = pd.DataFrame({

        "facility_ring_fe":
            d[
                "facility_ring_fe"
            ].astype(
                "category"
            ),

        "facility_year_fe":
            d[
                "facility_year_fe"
            ].astype(
                "category"
            ),
    })


    y = (
        d[outcome]
        .astype(float)
    )


    X = (
        d[event_cols]
        .astype(float)
    )


    # --------------------------------------------------------
    # 回归
    # --------------------------------------------------------

    model = AbsorbingLS(

        dependent=y,

        exog=X,

        absorb=absorb,

        drop_absorbed=True
    )


    clusters = (
        d[
            "facility_id"
        ]
        .astype("category")
        .cat.codes
    )


    fit = model.fit(

        cov_type=
            "clustered",

        clusters=
            clusters
    )


    # --------------------------------------------------------
    # 政策前联合检验
    #
    # n=3:
    # beta_-3 = beta_-2 = 0
    #
    # n=5:
    # beta_-5 ... beta_-2 = 0
    # --------------------------------------------------------

    required_pre_cols = [

        event_name(k)

        for k in range(
            -window,
            -1
        )
    ]


    available_pre_cols = [

        c

        for c in required_pre_cols

        if c in fit.params.index
    ]


    # 如果政策前 event 不完整
    # 不认为通过
    if (
        len(
            available_pre_cols
        )
        !=
        len(
            required_pre_cols
        )
    ):

        pooled_p = np.nan
        wald_stat = np.nan
        pooled_pass = 0

    else:

        beta = (
            fit.params[
                available_pre_cols
            ]
            .to_numpy()
        )


        cov = (
            fit.cov.loc[
                available_pre_cols,
                available_pre_cols
            ]
            .to_numpy()
        )


        wald_stat = float(

            beta.T
            @ np.linalg.pinv(
                cov
            )
            @ beta

        )


        pooled_p = float(

            chi2.sf(
                wald_stat,

                df=len(
                    available_pre_cols
                )
            )

        )


        pooled_pass = int(
            pooled_p >= 0.10
        )


    # --------------------------------------------------------
    # 系数结果
    # --------------------------------------------------------

    coef_rows = []


    # -1 baseline
    coef_rows.append({

        "window":
            window,

        "scheme":
            scheme,

        "outcome":
            outcome,

        "rel_year":
            -1,

        "coef":
            0.0,

        "se":
            0.0,

        "lower95":
            0.0,

        "upper95":
            0.0,

        "pvalue":
            np.nan,
    })


    for k in event_times:

        col = event_name(k)

        if (
            col
            not in
            fit.params.index
        ):
            continue


        coef = float(
            fit.params[col]
        )

        se = float(
            fit.std_errors[col]
        )

        pvalue = float(
            fit.pvalues[col]
        )


        coef_rows.append({

            "window":
                window,

            "scheme":
                scheme,

            "outcome":
                outcome,

            "rel_year":
                k,

            "coef":
                coef,

            "se":
                se,

            "lower95":
                coef
                - 1.96 * se,

            "upper95":
                coef
                + 1.96 * se,

            "pvalue":
                pvalue,
        })


    summary = {

        "window":
            window,

        "scheme":
            scheme,

        "outcome":
            outcome,

        "retained_facilities":
            len(
                passed_ids
            ),

        "event_nobs":
            int(
                fit.nobs
            ),

        "wald_stat":
            wald_stat,

        "pooled_pretrend_pvalue":
            pooled_p,

        "pooled_parallel_pass":
            pooled_pass,
    }


    return (
        summary,
        pd.DataFrame(
            coef_rows
        )
    )


# ============================================================
# 全部56个组合
# ============================================================

pooled_results = []
event_results = []


total = (
    len(SCHEMES)
    *
    len(WINDOWS)
    *
    len(OUTCOMES)
)

cnt = 0


for window in WINDOWS:

    for scheme in SCHEMES:

        for outcome in OUTCOMES:

            cnt += 1


            vprint(
                f"[{cnt}/{total}] "
                f"pooled "
                f"n={window} "
                f"{scheme} "
                f"{outcome}"
            )


            s, c = (
                pooled_event_study(

                    panel,
                    facility_screen,

                    scheme,
                    outcome,
                    window
                )
            )


            if s is not None:

                pooled_results.append(
                    s
                )


            if c is not None:

                event_results.append(
                    c
                )


pooled_summary = pd.DataFrame(
    pooled_results
)


event_coef = pd.concat(
    event_results,
    ignore_index=True
)


analysis_summary = (
    facility_summary.merge(

        pooled_summary,

        on=[
            "window",
            "scheme",
            "outcome"
        ],

        how="left"
    )
)


analysis_summary[
    "pooled_status"
] = np.where(

    analysis_summary[
        "pooled_parallel_pass"
    ] == 1,

    "PASS",

    "FAIL"
)


analysis_summary.to_csv(

    OUT_DIR
    / "pooled_pretrend_summary.csv",

    index=False,

    encoding="utf-8-sig"
)


event_coef.to_csv(

    OUT_DIR
    / "pooled_event_study_coefficients.csv",

    index=False,

    encoding="utf-8-sig"
)


# ============================================================
# 6个核心指标
# ============================================================

CORE_OUTCOMES = [

    "ln_active_count",

    "ln_food_count",

    "ln_fastfood_count",

    "survival_index",

    "avg_price",

    "ln_comments",
]


# ============================================================
# 规格排名
# ============================================================

core = (
    analysis_summary[
        analysis_summary[
            "outcome"
        ].isin(
            CORE_OUTCOMES
        )
    ]
    .copy()
)


spec_ranking = (
    core.groupby(
        [
            "window",
            "scheme"
        ],
        as_index=False
    )
    .agg(

        core_pooled_pass_count=(
            "pooled_parallel_pass",
            "sum"
        ),

        median_pooled_pvalue=(
            "pooled_pretrend_pvalue",
            "median"
        ),

        mean_facility_pass_rate=(
            "facility_pass_rate",
            "mean"
        ),

        median_retained_facilities=(
            "passed_facilities",
            "median"
        ),
    )
)


spec_ranking = (
    spec_ranking
    .sort_values(

        [
            "core_pooled_pass_count",
            "median_pooled_pvalue",
            "mean_facility_pass_rate",
            "median_retained_facilities",
        ],

        ascending=[
            False,
            False,
            False,
            False,
        ]
    )
    .reset_index(
        drop=True
    )
)


spec_ranking[
    "rank"
] = (
    np.arange(
        1,
        len(spec_ranking) + 1
    )
)


spec_ranking[
    "scheme_label"
] = (
    spec_ranking[
        "scheme"
    ].map(
        {
            k: v["label"]
            for k, v
            in SCHEMES.items()
        }
    )
)


spec_ranking[
    "mean_facility_pass_rate_pct"
] = (
    100
    *
    spec_ranking[
        "mean_facility_pass_rate"
    ]
)


# ============================================================
# 最优规格
# ============================================================

best = (
    spec_ranking
    .iloc[0]
)


BEST_WINDOW = int(
    best["window"]
)


BEST_SCHEME = str(
    best["scheme"]
)


# 图1：8个规格 pooled 通过数
# ============================================================

plot_rank = (
    spec_ranking.copy()
)


plot_rank["label"] = (

    "n="
    +
    plot_rank[
        "window"
    ].astype(str)
    +
    " "
    +
    plot_rank[
        "scheme"
    ]
)


plot_rank = (
    plot_rank.sort_values(
        "rank",
        ascending=False
    )
)


plt.figure(
    figsize=(9, 5)
)


plt.barh(

    plot_rank[
        "label"
    ],

    plot_rank[
        "core_pooled_pass_count"
    ]
)


plt.xlabel(
    "Core outcomes passing pooled pre-trend"
)


plt.ylabel(
    "Specification"
)


plt.title(
    "Specification ranking"
)


plt.xlim(
    0,
    6
)


plt.grid(
    axis="x",
    alpha=0.3
)


plt.show()


# ============================================================


# 图2：
# 最优规格 ln_active_count Event Study
# ============================================================

best_event = (

    event_coef[

        (
            event_coef[
                "window"
            ]
            == BEST_WINDOW
        )

        &

        (
            event_coef[
                "scheme"
            ]
            == BEST_SCHEME
        )

        &

        (
            event_coef[
                "outcome"
            ]
            == "ln_active_count"
        )

    ]
    .sort_values(
        "rel_year"
    )
)


active_info = (
    analysis_summary[

        (
            analysis_summary[
                "window"
            ]
            == BEST_WINDOW
        )

        &

        (
            analysis_summary[
                "scheme"
            ]
            == BEST_SCHEME
        )

        &

        (
            analysis_summary[
                "outcome"
            ]
            == "ln_active_count"
        )

    ]
    .iloc[0]
)


pooled_p = (
    active_info[
        "pooled_pretrend_pvalue"
    ]
)


plt.figure(
    figsize=(9, 5)
)


plt.errorbar(

    best_event[
        "rel_year"
    ],

    best_event[
        "coef"
    ],

    yerr=[

        best_event[
            "coef"
        ]
        -
        best_event[
            "lower95"
        ],

        best_event[
            "upper95"
        ]
        -
        best_event[
            "coef"
        ],
    ],

    fmt="o-",

    capsize=4
)


plt.axhline(
    0,
    linestyle="--"
)


# -1 是baseline
# -0.5 分隔政策前后
plt.axvline(
    -0.5,
    linestyle="--"
)


plt.xlabel(
    "Relative year"
)


plt.ylabel(
    "Treatment × Event-time coefficient"
)


plt.title(
    f"{BEST_SCHEME}, "
    f"n={BEST_WINDOW}, "
    f"ln(active count)\n"
    f"Pooled pre-trend p={pooled_p:.3f}"
)


plt.grid(
    alpha=0.3
)


plt.show()


# ============================================================


# 图3：
# 最优规格所有 outcome 的 pooled P
# ============================================================

best_outcomes = (
    analysis_summary[
        (
            analysis_summary[
                "window"
            ]
            == BEST_WINDOW
        )
        &
        (
            analysis_summary[
                "scheme"
            ]
            == BEST_SCHEME
        )
    ]
    .sort_values(
        "pooled_pretrend_pvalue"
    )
)


plt.figure(
    figsize=(9, 5)
)


plt.barh(

    best_outcomes[
        "outcome_name"
    ],

    best_outcomes[
        "pooled_pretrend_pvalue"
    ]
)


plt.axvline(
    0.10,
    linestyle="--"
)


plt.xlabel(
    "Pooled pre-trend p-value"
)


plt.ylabel(
    "Outcome"
)


plt.title(
    f"Best specification: "
    f"n={BEST_WINDOW}, "
    f"{BEST_SCHEME}"
)


plt.grid(
    axis="x",
    alpha=0.3
)


plt.show()


# ============================================================
# 保存排名
# ============================================================

spec_ranking.to_csv(

    OUT_DIR
    / "specification_ranking.csv",

    index=False,

    encoding="utf-8-sig"
)


vprint(
    "\n输出目录:",
    OUT_DIR
)


# ============================================================
# CELL 6
# 主规格正式 DID
# ============================================================

# 当前 Cell5 已经得到：
#
# BEST_SCHEME
# BEST_WINDOW
#
# 例如：
# BEST_SCHEME = "R500"
# BEST_WINDOW = 3


# ============================================================
# DID 回归函数
# ============================================================

def run_did_model(
    data,
    screen,
    scheme,
    outcome,
    window
):

    # --------------------------------------------------------
    # 1. 只使用：
    # 当前 scheme + window + outcome
    # 设施级平行趋势通过的设施
    # --------------------------------------------------------

    passed_ids = (
        screen[
            (screen["scheme"] == scheme)
            &
            (screen["window"] == window)
            &
            (screen["outcome"] == outcome)
            &
            (screen["parallel_pass"] == 1)
        ]["facility_id"]
        .astype(str)
        .unique()
    )

    if len(passed_ids) == 0:
        return None


    # --------------------------------------------------------
    # 2. 限制事件窗口
    #
    # n=3：
    # -3 ~ +3
    # --------------------------------------------------------

    d = data[
        (data["scheme"] == scheme)
        &
        (data["facility_id"].astype(str).isin(passed_ids))
        &
        (data["rel_year"].between(-window, window))
    ].copy()


    d = d.dropna(
        subset=[outcome]
    )


    if d.empty:
        return None


    # --------------------------------------------------------
    # 3. DID
    #
    # Treat × Post
    # --------------------------------------------------------

    d["post_did"] = (
        d["rel_year"] >= 0
    ).astype(float)

    d["did_reg"] = (
        d["treat_ring"].astype(float)
        *
        d["post_did"]
    )


    # --------------------------------------------------------
    # 4. 固定效应
    #
    # facility × ring FE
    #
    # facility × year FE
    # --------------------------------------------------------

    d["facility_ring_fe"] = (
        d["facility_id"].astype(str)
        + "_"
        + d["treat_ring"].astype(str)
    )

    d["facility_year_fe"] = (
        d["facility_id"].astype(str)
        + "_"
        + d["year"].astype(str)
    )


    absorb = pd.DataFrame({

        "facility_ring_fe":
            d["facility_ring_fe"]
            .astype("category"),

        "facility_year_fe":
            d["facility_year_fe"]
            .astype("category"),
    })


    y = (
        d[outcome]
        .astype(float)
    )


    X = pd.DataFrame({
        "did": d["did_reg"].astype(float)
    })


    # --------------------------------------------------------
    # 5. 回归
    # --------------------------------------------------------

    model = AbsorbingLS(

        dependent=y,

        exog=X,

        absorb=absorb,

        drop_absorbed=True
    )


    clusters = (
        d["facility_id"]
        .astype("category")
        .cat.codes
    )


    fit = model.fit(

        cov_type="clustered",

        clusters=clusters
    )


    beta = float(
        fit.params["did"]
    )

    se = float(
        fit.std_errors["did"]
    )

    pvalue = float(
        fit.pvalues["did"]
    )


    ci_low = (
        beta
        - 1.96 * se
    )

    ci_high = (
        beta
        + 1.96 * se
    )


    return {

        "scheme":
            scheme,

        "window":
            window,

        "outcome":
            outcome,

        "retained_facilities":
            len(passed_ids),

        "nobs":
            int(fit.nobs),

        "did_coef":
            beta,

        "did_se":
            se,

        "did_pvalue":
            pvalue,

        "ci95_low":
            ci_low,

        "ci95_high":
            ci_high,
    }


# ============================================================
# 跑所有主结果
# ============================================================

main_did_rows = []


for outcome in OUTCOMES:

    vprint(
        f"DID: {BEST_SCHEME} "
        f"n={BEST_WINDOW} "
        f"{outcome}"
    )


    result = run_did_model(

        data=panel,

        screen=facility_screen,

        scheme=BEST_SCHEME,

        outcome=outcome,

        window=BEST_WINDOW
    )


    if result is not None:

        main_did_rows.append(
            result
        )


main_did = pd.DataFrame(
    main_did_rows
)


# ============================================================
# 加 pooled 平行趋势结果
# ============================================================

pooled_best = (
    analysis_summary[
        (analysis_summary["scheme"] == BEST_SCHEME)
        &
        (analysis_summary["window"] == BEST_WINDOW)
    ][
        [
            "outcome",
            "pooled_pretrend_pvalue",
            "pooled_parallel_pass"
        ]
    ]
)


main_did = main_did.merge(

    pooled_best,

    on="outcome",

    how="left"
)


# ============================================================
# 指标名称
# ============================================================

main_did["outcome_name"] = (
    main_did["outcome"]
    .map(OUTCOMES)
)


# ============================================================
# 对数指标转百分比
# ============================================================

LOG_OUTCOMES = {

    "ln_active_count",
    "ln_food_count",
    "ln_fastfood_count",
    "ln_comments",
    "ln_avg_comments_per_shop",
}


def convert_effect(row):

    beta = row["did_coef"]

    if row["outcome"] in LOG_OUTCOMES:

        pct = (
            np.exp(beta)
            - 1
        ) * 100

        return pct

    return beta


main_did["effect"] = (
    main_did.apply(
        convert_effect,
        axis=1
    )
)


def effect_text(row):

    if row["outcome"] in LOG_OUTCOMES:

        return (
            f"{row['effect']:+.2f}%"
        )

    return (
        f"{row['effect']:+.4f}"
    )


main_did["effect_text"] = (
    main_did.apply(
        effect_text,
        axis=1
    )
)


# ============================================================
# 因果解释资格
#
# pooled pretrend p >= 0.10
# 才进入严格因果解释
# ============================================================

main_did["causal_status"] = np.where(

    main_did[
        "pooled_parallel_pass"
    ] == 1,

    "可作因果解释",

    "仅作描述性结果"
)


# ============================================================
# 展示
# ============================================================

main_did_display = (
    main_did[
        [
            "outcome_name",

            "retained_facilities",

            "pooled_pretrend_pvalue",

            "did_coef",

            "did_se",

            "did_pvalue",

            "effect_text",

            "causal_status",
        ]
    ]
    .copy()
)


# ============================================================

# 保存
# ============================================================

MAIN_DID_PATH = (
    OUT_DIR
    / "main_did_results.csv"
)


main_did.to_csv(

    MAIN_DID_PATH,

    index=False,

    encoding="utf-8-sig"
)


vprint(
    "保存:",
    MAIN_DID_PATH
)


# ============================================================


# 图1：对数指标 DID 百分比效应
# ============================================================

log_plot = main_did[
    main_did["outcome"].isin(LOG_OUTCOMES)
].copy()


# DID beta -> 百分比
log_plot["effect_pct"] = (
    np.exp(log_plot["did_coef"]) - 1
) * 100


# 95% CI 也转换成百分比
log_plot["ci_low_pct"] = (
    np.exp(log_plot["ci95_low"]) - 1
) * 100

log_plot["ci_high_pct"] = (
    np.exp(log_plot["ci95_high"]) - 1
) * 100


log_plot = log_plot.sort_values(
    "effect_pct"
)


plt.figure(figsize=(10, 6))

plt.errorbar(
    log_plot["effect_pct"],
    log_plot["outcome_name"],

    xerr=[
        log_plot["effect_pct"] - log_plot["ci_low_pct"],
        log_plot["ci_high_pct"] - log_plot["effect_pct"],
    ],

    fmt="o",
    capsize=5
)

plt.axvline(
    0,
    linestyle="--"
)

plt.xlabel("DID 折算效应（%）")
plt.ylabel("结果变量")

plt.title(
    f"主规格 DID 百分比效应："
    f"{BEST_SCHEME}, n={BEST_WINDOW}"
)

plt.grid(
    axis="x",
    alpha=0.3
)

plt.show()

# ============================================================


# 图2：非对数指标
# ============================================================

raw_plot = main_did[
    main_did["outcome"].isin(
        [
            "survival_index",
            "avg_price",
        ]
    )
].copy()


fig, axes = plt.subplots(
    1,
    2,
    figsize=(11, 4)
)


# ------------------------------------------------------------
# 存活率
# ------------------------------------------------------------

x = raw_plot[
    raw_plot["outcome"]
    == "survival_index"
].iloc[0]


axes[0].errorbar(
    x["did_coef"],
    0,

    xerr=[[
        x["did_coef"] - x["ci95_low"]
    ], [
        x["ci95_high"] - x["did_coef"]
    ]],

    fmt="o",
    capsize=5
)

axes[0].axvline(
    0,
    linestyle="--"
)

axes[0].set_yticks([0])
axes[0].set_yticklabels(
    ["存活率指数"]
)

axes[0].set_xlabel(
    "DID 系数"
)

axes[0].set_title(
    f"存活率效应\n"
    f"β={x['did_coef']:.4f}, "
    f"p={x['did_pvalue']:.3f}"
)


# ------------------------------------------------------------
# 平均价格
# ------------------------------------------------------------

x = raw_plot[
    raw_plot["outcome"]
    == "avg_price"
].iloc[0]


axes[1].errorbar(
    x["did_coef"],
    0,

    xerr=[[
        x["did_coef"] - x["ci95_low"]
    ], [
        x["ci95_high"] - x["did_coef"]
    ]],

    fmt="o",
    capsize=5
)

axes[1].axvline(
    0,
    linestyle="--"
)

axes[1].set_yticks([0])
axes[1].set_yticklabels(
    ["平均价格"]
)

axes[1].set_xlabel(
    "价格变化（元）"
)

axes[1].set_title(
    f"平均价格（仅描述性）\n"
    f"β={x['did_coef']:.2f}, "
    f"p={x['did_pvalue']:.3f}"
)


plt.tight_layout()
plt.show()


# ============================================================
# CELL 7
# 异质性分析
#
# 固定：
# BEST_SCHEME
# BEST_WINDOW
#
# 每个 subgroup：
#
# 重新空间聚合
# → 设施平行趋势
# → pooled Event Study
# → DID
# ============================================================


# ============================================================
# 1. 异质性分组定义
# ============================================================

SUBGROUPS = {

    "food":
        "餐饮商户",

    "fastfood":
        "同质化快餐小吃",

    "other_food":
        "其他餐饮",

    "nonfood":
        "非餐饮商户",

    "low_price_food":
        "低价餐饮（<=30元）",

    "high_price_food":
        "中高价餐饮（>30元）",
}


best_cfg = (
    SCHEMES[
        BEST_SCHEME
    ]
)


treat_max = (
    best_cfg[
        "treat_max"
    ]
)


control_max = (
    best_cfg[
        "control_max"
    ]
)


vprint(
    "异质性固定规格:"
)

vprint(
    BEST_SCHEME,
    best_cfg["label"]
)

vprint(
    "window:",
    BEST_WINDOW
)


# ============================================================
# 2. 设施
# ============================================================

fac_het = con.execute("""
SELECT

    facility_id,
    opening_year,

    name,

    lon_wgs,
    lat_wgs,

    province19,
    city19,
    county19

FROM facilities_clean
""").df()


fac_het["facility_id"] = (
    fac_het[
        "facility_id"
    ].astype(str)
)


fac_rad = np.radians(

    fac_het[
        [
            "lat_wgs",
            "lon_wgs"
        ]
    ].to_numpy()
)


# ============================================================
# 3. 重新按 BEST_SCHEME 匹配商户
#
# 不再跑四个 scheme，
# 只跑已经选好的主规格
# ============================================================

hetero_annual = []


for year in range(
    2012,
    2023
):

    vprint(
        f"heterogeneity spatial year {year}"
    )


    shops = con.execute(f"""
        SELECT

            shop_id,

            lon_wgs,
            lat_wgs,

            is_food,
            is_homogeneous_fastfood,

            is_closed,

            avg_price

        FROM shops_clean

        WHERE year = {year}
    """).df()


    if shops.empty:
        continue


    shops["shop_id"] = (
        shops["shop_id"]
        .astype(str)
    )


    coords = np.radians(

        shops[
            [
                "lat_wgs",
                "lon_wgs"
            ]
        ].to_numpy()
    )


    tree = BallTree(

        coords,

        metric="haversine"
    )


    indexes, distances = (
        tree.query_radius(

            fac_rad,

            r=(
                control_max
                /
                EARTH_RADIUS_KM
            ),

            return_distance=True,

            sort_results=True
        )
    )


    pair_blocks = []


    for i, (
        idx,
        dist
    ) in enumerate(
        zip(
            indexes,
            distances
        )
    ):

        if len(idx) == 0:
            continue


        x = (
            shops.iloc[
                idx
            ]
            .copy()
        )


        x["facility_id"] = (
            fac_het.iloc[i][
                "facility_id"
            ]
        )


        x["opening_year"] = (
            fac_het.iloc[i][
                "opening_year"
            ]
        )


        x["distance_km"] = (
            dist
            *
            EARTH_RADIUS_KM
        )


        # 社区食堂自身剔除
        x = x[
            x["shop_id"]
            !=
            x["facility_id"]
        ]


        pair_blocks.append(
            x
        )


    if not pair_blocks:
        continue


    pairs = pd.concat(
        pair_blocks,
        ignore_index=True
    )


    # ========================================================
    # Treat / Control
    # ========================================================

    pairs["scheme"] = (
        BEST_SCHEME
    )


    pairs["treat_ring"] = (
        pairs[
            "distance_km"
        ]
        <= treat_max
    ).astype("int8")


    # ========================================================
    # 对照组污染
    #
    # 必须在 subgroup 分类之前处理
    # ========================================================

    treated_shop_ids = set(

        pairs.loc[
            pairs[
                "treat_ring"
            ] == 1,

            "shop_id"
        ]
    )


    pairs = pairs[
        ~(
            (
                pairs[
                    "treat_ring"
                ] == 0
            )
            &
            (
                pairs[
                    "shop_id"
                ].isin(
                    treated_shop_ids
                )
            )
        )
    ].copy()


    # ========================================================
    # nullable integer 统一处理
    # ========================================================

    pairs["is_closed_num"] = (

        pd.to_numeric(
            pairs["is_closed"],
            errors="coerce"
        )
        .fillna(0)
        .astype("int8")
    )


    pairs["is_food_num"] = (

        pd.to_numeric(
            pairs["is_food"],
            errors="coerce"
        )
        .fillna(0)
        .astype("int8")
    )


    pairs["is_fastfood_num"] = (

        pd.to_numeric(
            pairs[
                "is_homogeneous_fastfood"
            ],
            errors="coerce"
        )
        .fillna(0)
        .astype("int8")
    )


    pairs["active"] = np.where(

        pairs[
            "is_closed_num"
        ] == 1,

        0,

        1

    ).astype("int8")


    # ========================================================
    # 4. 六个 subgroup
    # ========================================================

    masks = {

        "food":
            (
                pairs[
                    "is_food_num"
                ] == 1
            ),

        "fastfood":
            (
                pairs[
                    "is_fastfood_num"
                ] == 1
            ),

        "other_food":
            (
                (
                    pairs[
                        "is_food_num"
                    ] == 1
                )
                &
                (
                    pairs[
                        "is_fastfood_num"
                    ] == 0
                )
            ),

        "nonfood":
            (
                pairs[
                    "is_food_num"
                ] == 0
            ),

        # 文档：
        # 有价格的餐饮按30元分组
        "low_price_food":
            (
                (
                    pairs[
                        "is_food_num"
                    ] == 1
                )
                &
                (
                    pairs[
                        "avg_price"
                    ].notna()
                )
                &
                (
                    pairs[
                        "avg_price"
                    ] <= 30
                )
            ),

        "high_price_food":
            (
                (
                    pairs[
                        "is_food_num"
                    ] == 1
                )
                &
                (
                    pairs[
                        "avg_price"
                    ].notna()
                )
                &
                (
                    pairs[
                        "avg_price"
                    ] > 30
                )
            ),
    }


    # ========================================================
    # 每个 subgroup 聚合 active_count
    # ========================================================

    for subgroup, mask in masks.items():

        z = (
            pairs[
                mask
            ]
            .copy()
        )


        if z.empty:
            continue


        g = (
            z.groupby(
                [
                    "facility_id",
                    "opening_year",
                    "scheme",
                    "treat_ring"
                ],
                as_index=False
            )
            .agg(

                active_count=(
                    "active",
                    "sum"
                )
            )
        )


        g["year"] = year

        g["subgroup"] = subgroup


        hetero_annual.append(
            g
        )


# ============================================================
# 5. 合并
# ============================================================

hetero_raw = pd.concat(

    hetero_annual,

    ignore_index=True
)


# ============================================================
# 6. 每个 subgroup 建完整平衡面板
#
# facility × ring × year
# ============================================================

hetero_panels = []


for subgroup in SUBGROUPS:

    grid = (
        pd.MultiIndex
        .from_product(

            [
                fac_het[
                    "facility_id"
                ],

                [0, 1],

                range(
                    2012,
                    2023
                ),
            ],

            names=[
                "facility_id",
                "treat_ring",
                "year",
            ]
        )
        .to_frame(
            index=False
        )
    )


    x = (
        grid.merge(

            fac_het[
                [
                    "facility_id",
                    "opening_year",
                    "name",
                    "lon_wgs",
                    "lat_wgs",
                    "province19",
                    "city19",
                    "county19",
                ]
            ],

            on="facility_id",

            how="left"
        )
    )


    agg = hetero_raw[
        hetero_raw[
            "subgroup"
        ] == subgroup
    ][
        [
            "facility_id",
            "treat_ring",
            "year",
            "active_count"
        ]
    ]


    x = x.merge(

        agg,

        on=[
            "facility_id",
            "treat_ring",
            "year"
        ],

        how="left"
    )


    # 没有该类商户 = 0
    x["active_count"] = (
        x["active_count"]
        .fillna(0)
    )


    x["scheme"] = (
        BEST_SCHEME
    )


    x["subgroup"] = (
        subgroup
    )


    x["rel_year"] = (
        x["year"]
        -
        x["opening_year"]
    )


    x["post"] = (
        x["rel_year"]
        >= 0
    ).astype("int8")


    x["did"] = (
        x["treat_ring"]
        *
        x["post"]
    )


    x["ln_active_count"] = (
        np.log1p(
            x[
                "active_count"
            ]
        )
    )


    hetero_panels.append(
        x
    )


hetero_panel = pd.concat(

    hetero_panels,

    ignore_index=True
)


vprint(
    "heterogeneity panel rows:",
    len(hetero_panel)
)


# ============================================================
# 7. 每个 subgroup：
#
# 设施级平行趋势
# + pooled Event Study
# + DID
# ============================================================

hetero_screen_list = []
hetero_event_list = []
hetero_result_rows = []


for subgroup, subgroup_name in SUBGROUPS.items():

    vprint(
        "\n===================================="
    )

    vprint(
        subgroup_name
    )

    vprint(
        "===================================="
    )


    sub_panel = (
        hetero_panel[
            hetero_panel[
                "subgroup"
            ] == subgroup
        ]
        .copy()
    )


    # --------------------------------------------------------
    # 7.1 设施平行趋势
    # --------------------------------------------------------

    screen = facility_pretrend(

        panel=sub_panel,

        scheme=BEST_SCHEME,

        outcome="ln_active_count",

        window=BEST_WINDOW
    )


    screen["subgroup"] = (
        subgroup
    )


    screen[
        "subgroup_name"
    ] = (
        subgroup_name
    )


    hetero_screen_list.append(
        screen
    )


    testable = int(
        screen[
            "testable"
        ].sum()
    )


    passed = int(
        screen[
            "parallel_pass"
        ].sum()
    )


    pass_rate = (

        passed / testable

        if testable > 0

        else np.nan
    )


    # --------------------------------------------------------
    # 7.2 pooled Event Study
    # --------------------------------------------------------

    pooled, coef_df = (
        pooled_event_study(

            panel=sub_panel,

            facility_screen=
                screen,

            scheme=
                BEST_SCHEME,

            outcome=
                "ln_active_count",

            window=
                BEST_WINDOW
        )
    )


    if pooled is None:

        pooled_p = np.nan
        pooled_pass = 0

    else:

        pooled_p = (
            pooled[
                "pooled_pretrend_pvalue"
            ]
        )

        pooled_pass = (
            pooled[
                "pooled_parallel_pass"
            ]
        )


    if coef_df is not None:

        coef_df[
            "subgroup"
        ] = subgroup

        coef_df[
            "subgroup_name"
        ] = subgroup_name

        hetero_event_list.append(
            coef_df
        )


    # --------------------------------------------------------
    # 7.3 DID
    # --------------------------------------------------------

    did_result = run_did_model(

        data=sub_panel,

        screen=screen,

        scheme=BEST_SCHEME,

        outcome=
            "ln_active_count",

        window=
            BEST_WINDOW
    )


    if did_result is None:

        beta = np.nan
        did_se = np.nan
        did_p = np.nan
        effect_pct = np.nan
        retained = 0

    else:

        beta = (
            did_result[
                "did_coef"
            ]
        )

        did_se = (
            did_result[
                "did_se"
            ]
        )

        did_p = (
            did_result[
                "did_pvalue"
            ]
        )

        retained = (
            did_result[
                "retained_facilities"
            ]
        )

        effect_pct = (
            np.exp(beta)
            - 1
        ) * 100


    hetero_result_rows.append({

        "subgroup":
            subgroup,

        "subgroup_name":
            subgroup_name,

        "scheme":
            BEST_SCHEME,

        "window":
            BEST_WINDOW,

        "testable_facilities":
            testable,

        "passed_facilities":
            passed,

        "facility_pass_rate":
            pass_rate,

        "pooled_pretrend_pvalue":
            pooled_p,

        "pooled_parallel_pass":
            pooled_pass,

        "retained_facilities":
            retained,

        "did_coef":
            beta,

        "did_se":
            did_se,

        "did_pvalue":
            did_p,

        "effect_pct":
            effect_pct,

        "causal_status":
            (
                "可作因果解释"
                if pooled_pass == 1
                else
                "仅作描述性结果"
            ),
    })


# ============================================================
# 8. 汇总
# ============================================================

hetero_screen = pd.concat(

    hetero_screen_list,

    ignore_index=True
)


hetero_results = pd.DataFrame(

    hetero_result_rows
)


if hetero_event_list:

    hetero_event_coef = (
        pd.concat(
            hetero_event_list,
            ignore_index=True
        )
    )

else:

    hetero_event_coef = (
        pd.DataFrame()
    )


hetero_results[
    "facility_pass_rate_pct"
] = (
    hetero_results[
        "facility_pass_rate"
    ]
    * 100
)


hetero_results[
    "effect_text"
] = (
    hetero_results[
        "effect_pct"
    ]
    .map(
        lambda x:
        f"{x:+.2f}%"
        if pd.notna(x)
        else ""
    )
)


# ============================================================
# 9. 展示
# ============================================================

# ============================================================

# 11. 保存
# ============================================================

hetero_panel.to_parquet(

    OUT_DIR
    / "heterogeneity_panel.parquet",

    compression="zstd",

    index=False
)


hetero_screen.to_csv(

    OUT_DIR
    / "heterogeneity_facility_pretrend_screen.csv",

    index=False,

    encoding="utf-8-sig"
)


hetero_results.to_csv(

    OUT_DIR
    / "heterogeneity_results.csv",

    index=False,

    encoding="utf-8-sig"
)


if not hetero_event_coef.empty:

    hetero_event_coef.to_csv(

        OUT_DIR
        / "heterogeneity_event_study_coefficients.csv",

        index=False,

        encoding="utf-8-sig"
    )


vprint(
    "\n异质性分析完成"
)


# 10. 可视化
# ============================================================

plot_het = (
    hetero_results
    .sort_values(
        "effect_pct"
    )
)


plt.figure(
    figsize=(9, 6)
)


plt.barh(

    plot_het[
        "subgroup_name"
    ],

    plot_het[
        "effect_pct"
    ]
)


plt.axvline(
    0,
    linestyle="--"
)


plt.xlabel(
    "DID 折算效应（%）"
)


plt.ylabel(
    "商户类型"
)


plt.title(
    f"异质性 DID："
    f"{BEST_SCHEME}, "
    f"n={BEST_WINDOW}"
)


plt.grid(
    axis="x",
    alpha=0.3
)


plt.show()


# ============================================================


# pooled P 图
# ============================================================

plot_p = (
    hetero_results
    .sort_values(
        "pooled_pretrend_pvalue"
    )
)


plt.figure(
    figsize=(9, 6)
)


plt.barh(

    plot_p[
        "subgroup_name"
    ],

    plot_p[
        "pooled_pretrend_pvalue"
    ]
)


plt.axvline(
    0.10,
    linestyle="--"
)


plt.xlabel(
    "Pooled 平行趋势 P 值"
)


plt.ylabel(
    "商户类型"
)


plt.title(
    "异质性平行趋势复检"
)


plt.grid(
    axis="x",
    alpha=0.3
)


plt.show()


# ============================================================



try:
    con.close()
except Exception:
    pass

print("\n" + "=" * 70)
print("分析完成")
print("结果目录:", OUT_DIR)
print("图形目录:", FIG_DIR)
print("=" * 70)
