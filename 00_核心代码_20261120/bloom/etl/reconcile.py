"""清洗结果对账：DWD 清洗层 vs OLTP 业务库（真值）。

对账的意义
----------
清洗最容易犯的错不是"报错"，而是"悄悄少了几万行还看不出来"。
这里逐层比对：

1. 行数：清洗后应当等于"ODS 去重行数 - 隔离行数"，差额必须能对上账；
2. 逐字段：对能匹配上的订单，比较下单时间、状态、实付金额、送达时间；
3. 金额：合计差额应恰好等于被隔离订单的金额（可解释，而不是随机差）。

只有三条都对上，才能说"清洗没有丢数据、也没有造数据"。
"""
from __future__ import annotations

from typing import Any, Dict

import pandas as pd

from .. import db
from ..config import MYSQL_DW_DB, MYSQL_OLTP_DB
from ..logger import get_logger

log = get_logger("reconcile")


def _read(sql: str, database: str) -> pd.DataFrame:
    return db.read_df(sql, database=database)


def reconcile() -> Dict[str, Any]:
    log.info("开始对账：DWD 清洗层 vs 业务库 …")

    oltp_orders = _read(
        "SELECT order_id, customer_id, order_time, status, payable_amount, delivery_time "
        "FROM orders", MYSQL_OLTP_DB)
    dwd_orders = _read(
        "SELECT order_id, customer_id, order_time, status, payable_amount, delivery_time, "
        "repair_flags FROM dwd_orders_clean", MYSQL_DW_DB)
    rejects = _read(
        "SELECT source_table, reason_code, COUNT(*) AS cnt FROM dwd_rejects "
        "GROUP BY source_table, reason_code", MYSQL_DW_DB)

    merged = oltp_orders.merge(dwd_orders, on="order_id", how="outer",
                              suffixes=("_oltp", "_dwd"), indicator=True)
    both = merged[merged["_merge"] == "both"].copy()
    only_oltp = merged[merged["_merge"] == "left_only"]
    only_dwd = merged[merged["_merge"] == "right_only"]

    # 统一类型后再比较（DECIMAL 取回来是 Decimal，时间可能是 Timestamp 或 str）
    for col in ("payable_amount",):
        both[f"{col}_oltp"] = pd.to_numeric(both[f"{col}_oltp"], errors="coerce")
        both[f"{col}_dwd"] = pd.to_numeric(both[f"{col}_dwd"], errors="coerce")
    for col in ("order_time", "delivery_time"):
        both[f"{col}_oltp"] = both[f"{col}_oltp"].astype(str)
        both[f"{col}_dwd"] = both[f"{col}_dwd"].astype(str)

    # 逐字段一致性（排除客户ID被置为 UNKNOWN 的行）
    known_cust = both["customer_id_dwd"] != "UNKNOWN"
    field_checks = {
        "下单时间": (both["order_time_oltp"] == both["order_time_dwd"]),
        "订单状态": (both["status_oltp"] == both["status_dwd"]),
        "实付金额": (both["payable_amount_oltp"].round(2) == both["payable_amount_dwd"].round(2)),
        "送达时间": (both["delivery_time_oltp"] == both["delivery_time_dwd"]),
        "客户编号": (both["customer_id_oltp"].fillna("") == both["customer_id_dwd"].fillna(""))
                   & known_cust,
    }
    field_consistency = {
        name: {
            "checked": int(len(both)),
            "consistent": int(mask.sum()),
            "mismatch": int(len(both) - mask.sum()),
        }
        for name, mask in field_checks.items()
    }

    # 金额对账：差额应当恰好等于"只在业务库出现"的那些订单（即被隔离的订单）
    oltp_amt = float(pd.to_numeric(oltp_orders["payable_amount"], errors="coerce").sum())
    dwd_amt = float(pd.to_numeric(dwd_orders["payable_amount"], errors="coerce").sum())
    missing_amt = float(pd.to_numeric(only_oltp["payable_amount_oltp"], errors="coerce").fillna(0).sum())

    # 明细对账
    oltp_items = _read("SELECT COUNT(*) AS c, SUM(line_amount) AS amt FROM order_items",
                       MYSQL_OLTP_DB)
    dwd_items = _read("SELECT COUNT(*) AS c, SUM(line_amount) AS amt FROM dwd_order_items_clean",
                      MYSQL_DW_DB)

    result = {
        "orders": {
            "oltp_rows": int(len(oltp_orders)),
            "dwd_rows": int(len(dwd_orders)),
            "matched": int(len(both)),
            "only_in_oltp": int(len(only_oltp)),
            "only_in_dwd": int(len(only_dwd)),
            "rejected_rows": int(rejects[rejects["source_table"] == "ods_orders_raw"]["cnt"].sum()),
        },
        "items": {
            "oltp_rows": int(oltp_items.iloc[0]["c"]),
            "dwd_rows": int(dwd_items.iloc[0]["c"]),
            "oltp_amount": round(float(oltp_items.iloc[0]["amt"] or 0), 2),
            "dwd_amount": round(float(dwd_items.iloc[0]["amt"] or 0), 2),
            "rejected_rows": int(rejects[rejects["source_table"] == "ods_order_items_raw"]["cnt"].sum()),
        },
        "amount": {
            "oltp_total": round(oltp_amt, 2),
            "dwd_total": round(dwd_amt, 2),
            "diff": round(oltp_amt - dwd_amt, 2),
            "rejected_amount": round(missing_amt, 2),
            "explained_diff": round(missing_amt - (oltp_amt - dwd_amt), 2),
        },
        "field_consistency": field_consistency,
        "rejects_by_reason": rejects.to_dict("records"),
    }

    log.info("对账完成：业务库 %d 单 / 清洗层 %d 单 / 隔离 %d 单，金额差 %.2f（隔离单金额 %.2f）",
             result["orders"]["oltp_rows"], result["orders"]["dwd_rows"],
             result["orders"]["rejected_rows"], result["amount"]["diff"],
             result["amount"]["rejected_amount"])
    return result
