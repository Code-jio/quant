"""
期货品种监控模块 — 合约搜索

数据覆盖：SHFE / DCE / CZCE / CFFEX / INE / GFEX 全品种。
搜索支持：
  - 合约根码模糊匹配（rb → 螺纹钢所有合约）
  - 中文名称包含匹配
  - 拼音首字母匹配（lwg → 螺纹钢，hy → 黄金 / 豪油…）
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 品种目录
# ---------------------------------------------------------------------------
# 每条记录字段：
#   root        合约根码（不含月份）
#   name        中文品种名
#   exchange    交易所
#   product_type 品种类型
#   pinyin      拼音首字母（用于检索）
#   months      可上市月份列表（None = 1-12 全月）
#   upper       合约代码是否大写（CZCE / CFFEX）
#   quarterly   是否仅有季月（3/6/9/12），用于金融期货

_CATALOG: list[dict] = [
    # ── 上期所 SHFE ──────────────────────────────────────────────────────────
    {"root": "rb",  "name": "螺纹钢",      "exchange": "SHFE", "product_type": "黑色",  "pinyin": "lwg",  "months": [1,2,3,4,5,6,7,8,9,10], "upper": False, "quarterly": False},
    {"root": "hc",  "name": "热轧卷板",    "exchange": "SHFE", "product_type": "黑色",  "pinyin": "ryjb", "months": [1,2,3,4,5,6,7,8,9,10], "upper": False, "quarterly": False},
    {"root": "cu",  "name": "铜",          "exchange": "SHFE", "product_type": "有色",  "pinyin": "t",    "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "al",  "name": "铝",          "exchange": "SHFE", "product_type": "有色",  "pinyin": "l",    "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "zn",  "name": "锌",          "exchange": "SHFE", "product_type": "有色",  "pinyin": "x",    "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "pb",  "name": "铅",          "exchange": "SHFE", "product_type": "有色",  "pinyin": "q",    "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "ni",  "name": "镍",          "exchange": "SHFE", "product_type": "有色",  "pinyin": "n",    "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "sn",  "name": "锡",          "exchange": "SHFE", "product_type": "有色",  "pinyin": "x",    "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "ss",  "name": "不锈钢",      "exchange": "SHFE", "product_type": "有色",  "pinyin": "bxg",  "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "au",  "name": "黄金",        "exchange": "SHFE", "product_type": "贵金属","pinyin": "hj",   "months": [2,4,6,8,10,12],         "upper": False, "quarterly": False},
    {"root": "ag",  "name": "白银",        "exchange": "SHFE", "product_type": "贵金属","pinyin": "by",   "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "fu",  "name": "燃料油",      "exchange": "SHFE", "product_type": "能源化工","pinyin": "rly", "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "bu",  "name": "沥青",        "exchange": "SHFE", "product_type": "能源化工","pinyin": "lq",  "months": [2,4,6,8,10,12],         "upper": False, "quarterly": False},
    {"root": "ru",  "name": "天然橡胶",    "exchange": "SHFE", "product_type": "能源化工","pinyin": "trxj","months": [1,3,4,5,6,7,8,9,10,11], "upper": False, "quarterly": False},
    {"root": "sp",  "name": "纸浆",        "exchange": "SHFE", "product_type": "能源化工","pinyin": "zj",  "months": [1,2,3,4,5,6,7,8,9,10,11], "upper": False, "quarterly": False},
    {"root": "wr",  "name": "线材",        "exchange": "SHFE", "product_type": "黑色",  "pinyin": "xc",  "months": [1,2,3,4,5,6,7,8,9,10],  "upper": False, "quarterly": False},

    # ── 大商所 DCE ───────────────────────────────────────────────────────────
    {"root": "i",   "name": "铁矿石",      "exchange": "DCE",  "product_type": "黑色",  "pinyin": "tks",  "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "j",   "name": "焦炭",        "exchange": "DCE",  "product_type": "黑色",  "pinyin": "jt",   "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "jm",  "name": "焦煤",        "exchange": "DCE",  "product_type": "黑色",  "pinyin": "jm",   "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "l",   "name": "聚乙烯",      "exchange": "DCE",  "product_type": "能源化工","pinyin": "jyx", "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "pp",  "name": "聚丙烯",      "exchange": "DCE",  "product_type": "能源化工","pinyin": "jbn", "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "v",   "name": "PVC",         "exchange": "DCE",  "product_type": "能源化工","pinyin": "pvc", "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "eb",  "name": "苯乙烯",      "exchange": "DCE",  "product_type": "能源化工","pinyin": "byx", "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "eg",  "name": "乙二醇",      "exchange": "DCE",  "product_type": "能源化工","pinyin": "yeg", "months": [1,3,4,5,6,7,8,9,10,11,12], "upper": False, "quarterly": False},
    {"root": "pg",  "name": "液化石油气",  "exchange": "DCE",  "product_type": "能源化工","pinyin": "yhsyq","months": [1,3,4,5,6,7,8,9,10,11,12],"upper": False, "quarterly": False},
    {"root": "c",   "name": "玉米",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "ym",   "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "cs",  "name": "玉米淀粉",    "exchange": "DCE",  "product_type": "农产品", "pinyin": "ymdf", "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "a",   "name": "豆一",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "dy",   "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "b",   "name": "豆二",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "de",   "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "m",   "name": "豆粕",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "dp",   "months": [1,3,5,7,8,9,11,12],     "upper": False, "quarterly": False},
    {"root": "y",   "name": "豆油",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "dy",   "months": [1,3,5,7,8,9,11,12],     "upper": False, "quarterly": False},
    {"root": "p",   "name": "棕榈油",      "exchange": "DCE",  "product_type": "农产品", "pinyin": "zly",  "months": [1,3,5,7,8,9,11,12],     "upper": False, "quarterly": False},
    {"root": "jd",  "name": "鸡蛋",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "jd",   "months": [1,3,4,5,6,7,8,9,10,11], "upper": False, "quarterly": False},
    {"root": "lh",  "name": "生猪",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "sz",   "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "rr",  "name": "粳米",        "exchange": "DCE",  "product_type": "农产品", "pinyin": "jm",   "months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},

    # ── 郑商所 CZCE ──────────────────────────────────────────────────────────
    {"root": "CF",  "name": "棉花",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "mh",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "CY",  "name": "棉纱",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "ms",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "SR",  "name": "白糖",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "bt",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "TA",  "name": "PTA",         "exchange": "CZCE", "product_type": "能源化工","pinyin": "pta",  "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "OI",  "name": "菜油",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "cy",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "RM",  "name": "菜粕",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "cp",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "RS",  "name": "菜籽",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "cz",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "MA",  "name": "甲醇",        "exchange": "CZCE", "product_type": "能源化工","pinyin": "jc",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "ZC",  "name": "动力煤",      "exchange": "CZCE", "product_type": "能源化工","pinyin": "dlm",  "months": [1,3,4,5,6,7,8,9,10,11], "upper": True,  "quarterly": False},
    {"root": "FG",  "name": "玻璃",        "exchange": "CZCE", "product_type": "建材",   "pinyin": "bl",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "SA",  "name": "纯碱",        "exchange": "CZCE", "product_type": "能源化工","pinyin": "cj",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "UR",  "name": "尿素",        "exchange": "CZCE", "product_type": "能源化工","pinyin": "ns",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "AP",  "name": "苹果",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "pg",   "months": [1,3,5,7,10,11],         "upper": True,  "quarterly": False},
    {"root": "CJ",  "name": "红枣",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "hz",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "PF",  "name": "短纤",        "exchange": "CZCE", "product_type": "能源化工","pinyin": "dx",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "PK",  "name": "花生",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "hs",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "SM",  "name": "锰硅",        "exchange": "CZCE", "product_type": "黑色",   "pinyin": "mg",   "months": [1,3,4,5,6,7,8,9,10,11,12],"upper": True,  "quarterly": False},
    {"root": "SF",  "name": "硅铁",        "exchange": "CZCE", "product_type": "黑色",   "pinyin": "gt",   "months": [1,3,4,5,6,7,8,9,10,11,12],"upper": True,  "quarterly": False},
    {"root": "WH",  "name": "强麦",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "qm",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "PM",  "name": "普麦",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "pm",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "LR",  "name": "晚稻",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "wd",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},
    {"root": "JR",  "name": "粳稻",        "exchange": "CZCE", "product_type": "农产品", "pinyin": "jd",   "months": [1,3,5,7,9,11],          "upper": True,  "quarterly": False},

    # ── 中金所 CFFEX ─────────────────────────────────────────────────────────
    {"root": "IF",  "name": "沪深300股指", "exchange": "CFFEX","product_type": "金融",   "pinyin": "hs300gz", "months": [3,6,9,12],          "upper": True,  "quarterly": True},
    {"root": "IC",  "name": "中证500股指", "exchange": "CFFEX","product_type": "金融",   "pinyin": "zz500gz", "months": [3,6,9,12],          "upper": True,  "quarterly": True},
    {"root": "IH",  "name": "上证50股指",  "exchange": "CFFEX","product_type": "金融",   "pinyin": "sz50gz",  "months": [3,6,9,12],          "upper": True,  "quarterly": True},
    {"root": "IM",  "name": "中证1000股指","exchange": "CFFEX","product_type": "金融",   "pinyin": "zz1000gz","months": [3,6,9,12],          "upper": True,  "quarterly": True},
    {"root": "TL",  "name": "30年国债",    "exchange": "CFFEX","product_type": "金融",   "pinyin": "30ngz",   "months": [3,6,9,12],          "upper": True,  "quarterly": False},
    {"root": "T",   "name": "10年国债",    "exchange": "CFFEX","product_type": "金融",   "pinyin": "10ngz",   "months": [3,6,9,12],          "upper": True,  "quarterly": False},
    {"root": "TF",  "name": "5年国债",     "exchange": "CFFEX","product_type": "金融",   "pinyin": "5ngz",    "months": [3,6,9,12],          "upper": True,  "quarterly": False},
    {"root": "TS",  "name": "2年国债",     "exchange": "CFFEX","product_type": "金融",   "pinyin": "2ngz",    "months": [3,6,9,12],          "upper": True,  "quarterly": False},

    # ── 上期能源 INE ─────────────────────────────────────────────────────────
    {"root": "sc",  "name": "原油",        "exchange": "INE",  "product_type": "能源化工","pinyin": "yy",   "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "lu",  "name": "低硫燃料油",  "exchange": "INE",  "product_type": "能源化工","pinyin": "dlrly","months": [1,3,5,7,9,11],          "upper": False, "quarterly": False},
    {"root": "nr",  "name": "20号胶",      "exchange": "INE",  "product_type": "能源化工","pinyin": "20hj", "months": [1,3,4,5,6,7,8,9,10,11], "upper": False, "quarterly": False},
    {"root": "bc",  "name": "国际铜",      "exchange": "INE",  "product_type": "有色",   "pinyin": "gjt",  "months": list(range(1,13)),        "upper": False, "quarterly": False},
    {"root": "ec",  "name": "集运欧线",    "exchange": "INE",  "product_type": "航运",   "pinyin": "jyox", "months": [1,3,4,5,6,7,8,9,10,11,12],"upper": False,"quarterly": False},

    # ── 广期所 GFEX ──────────────────────────────────────────────────────────
    {"root": "si",  "name": "工业硅",      "exchange": "GFEX", "product_type": "有色",   "pinyin": "gyg",  "months": [1,2,3,4,5,6,7,8,9,10,11,12],"upper": False,"quarterly": False},
    {"root": "lc",  "name": "碳酸锂",      "exchange": "GFEX", "product_type": "有色",   "pinyin": "tsl",  "months": [1,2,3,4,5,6,7,8,9,10,11,12],"upper": False,"quarterly": False},
]

# 内部：以根码为键的快速索引
_ROOT_INDEX: dict[str, dict] = {p["root"].lower(): p for p in _CATALOG}

# ---------------------------------------------------------------------------
# 合约代码生成
# ---------------------------------------------------------------------------

def search_contracts(query: str, exchange: Optional[str] = None, limit: int = 50,
                     contracts=None) -> list[dict]:
    """Search gateway-verified instruments or explicitly imported historical symbols."""
    if contracts is None:
        from ..data import DataManager
        contracts={symbol:{'symbol':symbol,'source':'historical','tradable':False}
                   for symbol in DataManager().db.get_available_symbols()}
    rows=[]; query=query.strip().lower()
    for symbol, spec in contracts.items():
        root=''.join(c for c in symbol.split('.')[0] if c.isalpha()).lower()
        product=_ROOT_INDEX.get(root,{})
        row={**product,**spec,'symbol':symbol,'name':spec.get('name') or product.get('name',symbol)}
        if exchange and str(row.get('exchange','')).upper()!=exchange.upper():
            continue
        if query and not any(query in str(row.get(k,'')).lower() for k in ('symbol','name','pinyin')):
            continue
        rows.append(row)
    return sorted(rows,key=lambda r:r['symbol'])[:max(1,min(limit,200))]
