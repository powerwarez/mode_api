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
            date_str = datetime.now().strftime('%Y-%m-%d')

        # 문자열을 datetime 객체로 변환
        try:
            requested_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self.send_error(400, f"Invalid date format: {date_str}")
            return

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
            # 지수 형태 맞추기
            recent_close_prices.index = pd.to_datetime(recent_close_prices.index)
            # 금요일 데이터만 추출
            friday_data = recent_close_prices[recent_close_prices.index.weekday == 4]

            # RSI 계산
            rsi_values = calculate_rsi(friday_data)

            # rsi_values: 날짜(DateTimeIndex)를 인덱스로, RSI를 값으로 가지고 있음
            # step 1) requested_date 이하(과거) 데이터만 필터링
            filtered_rsi = rsi_values.loc[rsi_values.index <= requested_date]

            if len(filtered_rsi) < 2:
                # 필요 최소 개수(2개)가 없으면 모드 계산 불가
                self.send_error(404, 'No enough RSI data found for or before the given date')
                return

            # step 2) 가장 최근 금요일(= filtered_rsi의 마지막 인덱스) RSI를 찾음
            qqq_rsi_late = filtered_rsi.iloc[-1]
            # 바로 이전 금요일 RSI
            qqq_rsi_late_late = filtered_rsi.iloc[-2]

            last_date = filtered_rsi.index[-1].strftime('%Y-%m-%d')

            # 모드 계산
            mode = this_week_mode(qqq_rsi_late, qqq_rsi_late_late)

            # mode.json에 저장
            add_data_to_json(last_date, mode)

            # 응답 전송
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"date": last_date, "mode": mode}).encode())
        except Exception as e:
            self.send_error(500, f'Internal Server Error: {str(e)}')