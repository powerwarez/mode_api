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

def add_data_to_single_json_array(date_str, mode_str):
    """
    1) 'id=1'인 행을 읽어서,
    2) mode(배열) 안에 {'date': date_str, 'mode': mode_str}가 없으면 추가.
    3) 최종적으로 업데이트/삽입 뒤 그 데이터 반환.
    """
    supabase = create_supabase_client()

    # id=1인 행이 있는지 검색
    existing_rows = supabase.from_("mode").select("*").eq("id", 1).execute()
    if existing_rows.data and len(existing_rows.data) > 0:
        row = existing_rows.data[0]
        # row["mode"]는 이미 저장된 배열 (예: [{"date":"...", "mode":"..."}])
        arr = row["mode"]
    else:
        # 아직 id=1 행이 없으면 새로 만들 준비
        row = None
        arr = []

    # 이미 해당 date가 있는지 확인
    is_existing = any(item["date"] == date_str for item in arr if "date" in item)
    if is_existing:
        # 이미 동일 date가 있으면 새로 추가 안 함
        return row

    # 없다면 새로 추가
    arr.append({"date": date_str, "mode": mode_str})

    if row:
        # 기존 행이 있으면 update
        updated = supabase.table("mode").update({"mode": arr}).eq("id", 1).execute()
        if updated.data and len(updated.data) > 0:
            return updated.data[0]
        else:
            return None
    else:
        # 아직 행이 없었으므로 insert
        inserted = supabase.table("mode").insert({"id": 1, "mode": arr}).execute()
        if inserted.data and len(inserted.data) > 0:
            return inserted.data[0]
        else:
            return None

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

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 0) 날짜 파라미터
        date_str = self.path.split('?date=')[-1]
        if not date_str or date_str == self.path:
            date_str = datetime.now().strftime('%Y-%m-%d')

        try:
            requested_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self.send_error(400, f"Invalid date format: {date_str}")
            return

        # 1) 한 달 전 날짜 계산
        start_date = requested_date - timedelta(days=30)

        try:
            # 2) 야후 파이낸스에서 1년 치 QQQ 데이터 받아와 RSI 계산
            qqq_data = yf.Ticker("QQQ")
            recent_close_prices = qqq_data.history(period="1y")
            # 타임존 제거
            recent_close_prices.index = recent_close_prices.index.tz_localize(None)
            # 금요일 데이터만 추출
            friday_data = recent_close_prices[recent_close_prices.index.weekday == 4]
            rsi_values = calculate_rsi(friday_data)

            # 3) requested_date 이하만 필터링
            rsi_up_to_requested = rsi_values.loc[rsi_values.index <= requested_date]
            if len(rsi_up_to_requested) < 2:
                self.send_error(404, "No enough RSI data found on or before the given date")
                return

            # 4) start_date 이상, requested_date 이하 금요일 RSI만 추출
            rsi_target_range = rsi_up_to_requested.loc[rsi_up_to_requested.index >= start_date]
            if len(rsi_target_range) < 2:
                self.send_error(404, "No Friday RSI data found in the last month range")
                return

            # 5) 범위 내 모든 연속된 금요일 쌍에 대해 모드 계산 + Supabase 저장
            for i in range(1, len(rsi_target_range)):
                prev_rsi = rsi_target_range.iloc[i - 1]
                curr_rsi = rsi_target_range.iloc[i]
                curr_date_str = rsi_target_range.index[i].strftime('%Y-%m-%d')

                mode_calculated = this_week_mode(curr_rsi, prev_rsi)
                add_data_to_single_json_array(curr_date_str, mode_calculated)

            # 6) 최종적으로 가장 최근 금요일(rsi_up_to_requested.index[-1]) 날짜
            final_date = rsi_up_to_requested.index[-1].strftime('%Y-%m-%d')

            # Supabase에서 해당 날짜 데이터를 가져오기
            supabase = create_supabase_client()
            final_query = supabase.from_("mode").select("*").eq("id", 1).execute()

            if final_query.data and len(final_query.data) > 0:
                final_entry = final_query.data[0]  # {"id":..., "mode": {"date": "...", "mode": "..."} }
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(final_entry).encode())
            else:
                # 혹시 DB에 없다면 직접 응답
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                unknown_json = {"id": 1, "mode": []}
                self.wfile.write(json.dumps(unknown_json).encode())

        except Exception as e:
            self.send_error(500, f"Internal Server Error: {str(e)}")