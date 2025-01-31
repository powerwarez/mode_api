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

def get_or_create_single_row():
    """
    mode 테이블에서 첫 행을 가져오고, 없으면 새로 insert해 반환.
    반환값 예: {'id': 2, 'mode': []} (파이썬 딕셔너리)
    """
    supabase = create_supabase_client()
    # 아무 조건 없이 select -> 첫 번째 row만 확인
    existing = supabase.from_("mode").select("*").execute()
    if existing.data and len(existing.data) > 0:
        # 이미 row가 있으면 그 중 첫 번째 값 반환
        return existing.data[0]
    else:
        # 아직 데이터가 없으면 빈 배열로 새 row insert
        inserted = supabase.table("mode").insert({"mode": []}).execute()
        if inserted.data and len(inserted.data) > 0:
            return inserted.data[0]
        else:
            return None

def add_data_to_single_json_array(date_str, mode_str):
    """
    1) 'mode' 테이블에서 첫 행(row)을 가져온다 (혹은 없으면 새로 만든다).
    2) 그 행의 'mode' 필드(배열)에 date_str가 이미 있나 확인 -> 없으면 추가
    3) 최종적으로 update 후 해당 row 반환
    """
    supabase = create_supabase_client()

    # (1) 이미 존재하는 단일 row를 가져오거나 (없으면 새로 만들기)
    row = get_or_create_single_row()
    if not row:
        # row 생성 실패 시 None 반환
        return None

    arr = row["mode"]  # jsonb 배열(파이썬 list)
    if not isinstance(arr, list):
        # 혹시 mode 필드가 배열이 아니면 새 배열로 reset
        arr = []

    # 이미 date_str가 존재하는가?
    is_existing = any(item["date"] == date_str for item in arr if "date" in item)
    if is_existing:
        # 이미 있으면 중복 추가 안 함
        return row

    # 새로 추가
    arr.append({"date": date_str, "mode": mode_str})

    # (3) update
    updated = supabase.table("mode").update({"mode": arr}).eq("id", row["id"]).execute()
    if updated.data and len(updated.data) > 0:
        return updated.data[0]
    else:
        return None

def calculate_rsi(data, window=14):
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

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
            final_query = supabase.from_("mode").select("*").limit(1).execute()

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            if final_query.data and len(final_query.data) > 0:
                self.wfile.write(json.dumps(final_query.data[0]).encode())
            else:
                # 아무 행도 없다면
                empty_result = {"mode": []}
                self.wfile.write(json.dumps(empty_result).encode())

        except Exception as e:
            self.send_error(500, f"Internal Server Error: {str(e)}")