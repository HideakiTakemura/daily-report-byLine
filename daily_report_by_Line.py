import os
from dotenv import load_dotenv
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Metric, RunReportRequest
import requests
from datetime import datetime, timedelta, timezone

# === タイムスタンプログ出力 ===
JST = timezone(timedelta(hours=9))
print("🕒 LINE通知送信スクリプト 実行開始（JST）:", datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S'))

load_dotenv()

# === 環境変数 ===
SHOP_NAME = os.getenv("SHOPIFY_SHOP_NAME")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
GA_PROPERTY_ID = os.getenv("GA_PROPERTY_ID")
GA_KEY_JSON = os.getenv("GA4_KEY_JSON")
LINE_TOKEN = os.getenv("LINE_CHANNEL_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

from google.oauth2 import service_account
import json

credentials = service_account.Credentials.from_service_account_info(json.loads(GA_KEY_JSON))

# === GA4 セッション数取得 ===
def get_ga_sessions(start_date, end_date):
    client = BetaAnalyticsDataClient(credentials=credentials)
    request = RunReportRequest(
        property=f"properties/{GA_PROPERTY_ID}",
        metrics=[Metric(name="sessions")],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)]
    )
    response = client.run_report(request)
    return int(response.rows[0].metric_values[0].value)

# === Shopify売上と注文数取得（ページネーション対応）===
def get_shopify_sales(date_from: str, date_to: str):
    base_url = f"https://{SHOP_NAME}.myshopify.com/admin/api/2023-10/orders.json"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }
    params = {
        "status": "any",
        "created_at_min": f"{date_from}T00:00:00+09:00",
        "created_at_max": f"{date_to}T23:59:59+09:00",
        "limit": 250
    }
    orders = []
    url = base_url

    while url:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        orders.extend(data.get("orders", []))
        params = None
        link_header = response.headers.get('Link')
        if link_header and 'rel="next"' in link_header:
            next_url = None
            parts = link_header.split(',')
            for part in parts:
                if 'rel="next"' in part:
                    start = part.find('<') + 1
                    end = part.find('>')
                    next_url = part[start:end].strip()
                    break
            url = next_url
        else:
            url = None

    total_sales = sum(float(o["total_price"]) for o in orders)
    return round(total_sales), len(orders), orders

# === 商品別売上ランキング（数量ベース） ===
def get_product_ranking(orders):
    product_quantities = {}
    for order in orders:
        for item in order.get("line_items", []):
            title = item["title"]
            quantity = int(item["quantity"])
            product_quantities[title] = product_quantities.get(title, 0) + quantity

    ranked = sorted(product_quantities.items(), key=lambda x: x[1], reverse=True)
    return ranked[:5]

# === LINE通知 ===
def push_line_message(user_ids: list, message: str):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    for user_id in user_ids:
        data = {
            "to": user_id,
            "messages": [
                {
                    "type": "text",
                    "text": message
                }
            ]
        }
        response = requests.post(url, headers=headers, json=data)
        print(f"LINE status for {user_id}:", response.status_code, response.text)

# === メッセージ整形 ===
def build_line_message(month, day, ranking, report_date):
    msg = f"Admiral Shopify 売上レポート（{report_date}）\n\n"
    msg += f"🗓 当月総計（{month['date_from']}～{month['date_to']}）\n"
    msg += f" 売上金額：¥{month['sales']:,} \n 注文数：{month['orders']}件 \n👥 セッション数：{month['sessions']}\n"
    msg += f"✅ CVR：{month['cvr']:.2f}%\n💰 注文単価：¥{month['unit_price']:,}\n\n"

    msg += f"🗖 昨日（{day['date']}）\n"
    msg += f" 売上金額：¥{day['sales']:,} \n 注文数：{day['orders']}件 \n👥 セッション数：{day['sessions']}\n"
    msg += f"✅ CVR：{day['cvr']:.2f}%\n💰 注文単価：¥{day['unit_price']:,}\n\n"

    msg += "🏆 昨日の売上個数ランキング（Top 5） \n"
    msg += "\n".join([f"{i+1}位 {name}（{qty}個）" for i, (name, qty) in enumerate(ranking)])
    return msg

# === 実行 ===
report_date = datetime.now().date()
yesterday = report_date - timedelta(days=1)
month_start = yesterday.replace(day=1)

# GAセッション取得
day_sessions = get_ga_sessions(str(yesterday), str(yesterday))
month_sessions = get_ga_sessions(str(month_start), str(yesterday))

# Shopify売上取得
day_sales, day_orders, day_order_list = get_shopify_sales(str(yesterday), str(yesterday))
month_sales, month_orders, _ = get_shopify_sales(str(month_start), str(yesterday))

# CVR, 注文単価計算
def calc_metrics(sales, orders, sessions):
    cvr = (orders / sessions * 100) if sessions else 0
    unit_price = (sales / orders) if orders else 0
    return round(cvr, 2), round(unit_price)

day_cvr, day_unit = calc_metrics(day_sales, day_orders, day_sessions)
month_cvr, month_unit = calc_metrics(month_sales, month_orders, month_sessions)

# メッセージ作成&送信
message = build_line_message(
    month={
        "sales": month_sales, "orders": month_orders, "sessions": month_sessions,
        "cvr": month_cvr, "unit_price": month_unit,
        "date_from": str(month_start), "date_to": str(yesterday)
    },
    day={
        "sales": day_sales, "orders": day_orders, "sessions": day_sessions,
        "cvr": day_cvr, "unit_price": day_unit, "date": str(yesterday)
    },
    ranking=get_product_ranking(day_order_list),
    report_date=str(report_date)
)

push_line_message(LINE_USER_ID.split(','), message)
