#!/usr/bin/env python3
"""查阿里云百炼账号的预算管理概要(DescribeCostBudgetsSummary) + 账户余额(QueryAccountBalance)。
需要环境变量: ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET
只读查询,不做任何写操作。
"""
import os
import sys
import json

try:
    from alibabacloud_bssopenapi20171214.client import Client as BssClient
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_bssopenapi20171214 import models as bss_models
except ImportError:
    print("缺少SDK,先装: pip3 install alibabacloud_bssopenapi20171214 alibabacloud_tea_openapi", file=sys.stderr)
    sys.exit(1)

ak = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
sk = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
if not ak or not sk:
    print("请先 export ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET", file=sys.stderr)
    sys.exit(1)

config = open_api_models.Config(access_key_id=ak, access_key_secret=sk)
config.endpoint = "business.aliyuncs.com"
client = BssClient(config)

print("=== 账户余额 ===")
balance_resp = client.query_account_balance()
print(json.dumps(balance_resp.body.to_map(), ensure_ascii=False, indent=2))

print("\n=== 预算管理概要 ===")
req = bss_models.DescribeCostBudgetsSummaryRequest()
budget_resp = client.describe_cost_budgets_summary(req)
print(json.dumps(budget_resp.body.to_map(), ensure_ascii=False, indent=2))

print("\n=== 本月账单明细(查清楚钱花在哪) ===")
from datetime import datetime
cycle = datetime.now().strftime("%Y-%m")
bill_req = bss_models.QueryBillRequest(billing_cycle=cycle, product_code="bailian")
try:
    bill_resp = client.query_bill(bill_req)
    print(json.dumps(bill_resp.body.to_map(), ensure_ascii=False, indent=2)[:3000])
except Exception as e:
    print("QueryBill失败:", e)

print("\n=== 不限product_code,查全部本月账单 ===")
bill_req2 = bss_models.QueryBillRequest(billing_cycle=cycle)
try:
    bill_resp2 = client.query_bill(bill_req2)
    print(json.dumps(bill_resp2.body.to_map(), ensure_ascii=False, indent=2)[:3000])
except Exception as e:
    print("QueryBill失败:", e)
