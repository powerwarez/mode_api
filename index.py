from http.server import BaseHTTPRequestHandler
import yfinance as yf
import pandas as pd
import json
from datetime import datetime, timedelta
from wilder_rsi import calculate_rsi
# supabase-py 로부터 Supabase 클라이언트 불러오기
from supabase import create_client, Client
import os

def create_supabase_client() -> Client:
    # Supabase URL과 KEY를 환경 변수에서 읽어오는 방식으로 처리
    # (Vercel 환경에서 환경 변수를 설정해두면 os.environ로 접근 가능)
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ANON_KEY")
    return create_client(supabase_url, supabase_key)

def this_week_mode(qqq_rsi_late, qqq_rsi_late_late):
    qqq_up = qqq_rsi_late_late < qqq_rsi_late

    if qqq_rsi_late_late > 65 and not qqq_up:
        return "safe"
    if 40 < qqq_rsi_late_late < 50 and not qqq_up:
        return "safe"
    if qqq_rsi_late_late >= 50 and qqq_rsi_late < 50:
        return "safe"

    if qqq_rsi_late_late <= 50 and qqq_rsi_late > 50:
        return "aggressive"
    if 50 < qqq_rsi_late_late < 60 and qqq_up:
        return "aggressive"
    if qqq_rsi_late_late <= 35 and qqq_up:
        return "aggressive"

    return "previous"

def add_data_to_db(date_str, mode_str):
    supabase = create_supabase_client()

    # 먼저 같은 날짜가 이미 있는지 확인
    existing_row = supabase.from_("mode").select("*").eq("date", date_str).execute()

    # 해당 date가 이미 있다면, 새로 추가하지 않고 바로 반환
    if existing_row.data and len(existing_row.data) > 0:
        return existing_row.data[0]

    # 없으면 새 레코드 삽입
    response = supabase.table("mode").insert({"date": date_str, "mode": mode_str}).execute()
    if response.data and len(response.data) > 0:
        return response.data[0]
    else:
        return None

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 1) date 파라미터
        date_str = self.path.split('?date=')[-1]
        if not date_str or date_str == self.path:
            date_str = datetime.now().strftime('%Y-%m-%d')

        try:
            requested_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self.send_error(400, f"Invalid date format: {date_str}")
            return

        # 2) 한 달 전 날짜 계산
        start_date = requested_date - timedelta(days=30)

        try:
            # 3) 1년 치 QQQ 데이터 받아와 RSI 계산
            qqq_data = yf.Ticker("QQQ")
            recent_close_prices = qqq_data.history(period="1y")
            # 타임존 제거
            recent_close_prices.index = recent_close_prices.index.tz_localize(None)
            # 금요일 데이터만 추출
            friday_data = recent_close_prices[recent_close_prices.index.weekday == 4]
            rsi_values = calculate_rsi(friday_data)

            # "requested_date 이하" 전체 RSI
            rsi_up_to_requested = rsi_values.loc[rsi_values.index <= requested_date]
            if len(rsi_up_to_requested) < 2:
                self.send_error(404, "No enough RSI data found on or before the given date")
                return

            # 4) start_date 이상 + requested_date 이하 범위만 뽑아서 모드 계산
            rsi_target_range = rsi_up_to_requested.loc[rsi_up_to_requested.index >= start_date]

            # 2개 이상 데이터가 있어야 모드 계산 가능
            if len(rsi_target_range) < 2:
                # 그래도 없으면 너무 과거/미래라서 데이터가 없다는 뜻
                self.send_error(404, "No Friday RSI data found in the last month range")
                return

            # 범위 내 모든 "연속된 금요일" 쌍에 대해 모드 계산 → Supabase에 누락된 날짜만 저장
            for i in range(1, len(rsi_target_range)):
                prev_rsi = rsi_target_range.iloc[i - 1]
                curr_rsi = rsi_target_range.iloc[i]
                curr_date_str = rsi_target_range.index[i].strftime('%Y-%m-%d')

                mode_calculated = this_week_mode(curr_rsi, prev_rsi)
                add_data_to_db(curr_date_str, mode_calculated)

            # 5) 최종적으로 "requested_date 이하"에서 가장 최근 금요일이 rsi_up_to_requested.index[-1]
            last_date_in_range = rsi_up_to_requested.index[-1].strftime('%Y-%m-%d')

            # Supabase DB에서 해당 날짜의 모드를 가져옴
            supabase = create_supabase_client()
            final_check = supabase.from_("mode").select("*").eq("date", last_date_in_range).execute()
            
            if final_check.data and len(final_check.data) > 0:
                final_entry = final_check.data[0]
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(final_entry).encode())
            else:
                # 혹시 DB에 저장이 안됐으면 직접 반환
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"date": last_date_in_range, "mode": "Unknown"}).encode())

        except Exception as e:
            self.send_error(500, f"Internal Server Error: {str(e)}")