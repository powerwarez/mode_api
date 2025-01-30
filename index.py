from http.server import BaseHTTPRequestHandler
import yfinance as yf
import pandas as pd
import json
from datetime import datetime
from wilder_rsi import calculate_rsi
from write_mode import add_data_to_json

def this_week_mode(qqq_rsi_late, qqq_rsi_late_late):
    qqq_up = qqq_rsi_late_late < qqq_rsi_late

    if qqq_rsi_late_late > 65 and not qqq_up:
        return "안전모드"
    if 40 < qqq_rsi_late_late < 50 and not qqq_up:
        return "안전모드"
    if qqq_rsi_late_late >= 50 and qqq_rsi_late < 50:
        return "안전모드"

    if qqq_rsi_late_late <= 50 and qqq_rsi_late > 50:
        return "공세모드"
    if 50 < qqq_rsi_late_late < 60 and qqq_up:
        return "공세모드"
    if qqq_rsi_late_late <= 35 and qqq_up:
        return "공세모드"

    return "이전모드"

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 쿼리 문자열에서 date 파라미터 추출
        date_str = self.path.split('?date=')[-1]
        
        # date 파라미터가 비어 있거나 ?date= 형태가 들어오지 않았다면, 오늘 날짜로 기본값 설정
        if not date_str or date_str == self.path:
            today = datetime.now().strftime('%Y-%m-%d')
            date_str = today

        try:
            with open("mode.json", "r", encoding="utf-8") as file:
                data = json.load(file)
                existing_entry = next((entry for entry in data if entry['date'] == date_str), None)
                if existing_entry:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(existing_entry).encode())
                    return
        except (FileNotFoundError, json.JSONDecodeError):
            data = []

        try:
            qqq_data = yf.Ticker("QQQ")
            recent_close_prices = qqq_data.history(period="1y")
            recent_close_prices.index = pd.to_datetime(recent_close_prices.index)
            friday_data = recent_close_prices[recent_close_prices.index.weekday == 4]

            rsi_values = calculate_rsi(friday_data)

            for i in range(1, len(rsi_values)):
                qqq_rsi_late_late = rsi_values.iloc[i - 1]
                qqq_rsi_late = rsi_values.iloc[i]
                last_date = rsi_values.index[i].strftime('%Y-%m-%d')

                if last_date == date_str:
                    mode = this_week_mode(qqq_rsi_late, qqq_rsi_late_late)
                    add_data_to_json(last_date, mode)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"date": last_date, "mode": mode}).encode())
                    return

            self.send_error(404, 'Mode not found for the given date')
        except Exception as e:
            self.send_error(500, f'Internal Server Error: {str(e)}')