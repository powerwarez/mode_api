from http.server import BaseHTTPRequestHandler
import yfinance as yf
import pandas as pd
import json
from datetime import datetime, timedelta
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
    mode 테이블에서 id가 1인 행을 가져오고, 없으면 id를 1로 지정하여 새로 insert해 반환.
    반환값 예: {'id': 1, 'mode': []} (파이썬 딕셔너리)
    """
    supabase = create_supabase_client()
    # id가 1인 row만 확인
    existing = supabase.from_("mode").select("*").eq("id", 1).execute()
    if existing.data and len(existing.data) > 0:
        # id가 1인 row가 있으면 반환
        return existing.data[0]
    else:
        # id가 1인 row가 없으면 id를 1로 지정하여 빈 배열로 새 row insert
        inserted = supabase.table("mode").insert({"id": 1, "mode": []}).execute()
        if inserted.data and len(inserted.data) > 0:
            return inserted.data[0]
        else:
            return None

def add_data_to_single_json_array(date_str, mode_str, rsi_value=None, prev_rsi_value=None):
    """
    1) 'mode' 테이블에서 id가 1인 행(row)을 가져오거나 (없으면 새로 만든다).
    2) 그 행의 'mode' 필드(배열)에 date_str가 이미 있나 확인 -> 없으면 추가
    3) 최종적으로 id가 1인 행만 update 후 해당 row 반환
    """
    supabase = create_supabase_client()

    row = get_or_create_single_row()
    if not row:
        return None

    arr = row["mode"]
    if not isinstance(arr, list):
        arr = []

    # 이미 date_str가 존재하는지 확인
    is_existing = any(item["date"] == date_str for item in arr if "date" in item)
    if is_existing:
        return row

    # 새로 데이터 추가 (rsi 값 포함)
    new_item = {"date": date_str, "mode": mode_str}
    if prev_rsi_value is not None:
        new_item["prev_rsi"] = float(prev_rsi_value)
    if rsi_value is not None:
        new_item["rsi"] = float(rsi_value)
    arr.append(new_item)

    # date 기준 정렬
    arr.sort(key=lambda x: x["date"])

    # 업데이트 - id가 1인 행만 업데이트
    updated = supabase.table("mode").update({"mode": arr}).eq("id", 1).execute()
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

def get_last_non_previous_mode(date_str: str) -> str:
    """
    date_str보다 이전(과거) 날짜들 중에서 'previous'가 아닌 가장 최근 모드를 찾아서 반환.
    id가 1인 행에서만 데이터를 검색하며, 찾지 못하면 'previous'를 반환.
    """
    supabase = create_supabase_client()
    row = get_or_create_single_row()
    if not row:
        return "previous"

    arr = row.get("mode", [])
    if not isinstance(arr, list):
        return "previous"

    # date 필드가 date_str 보다 작은(과거) 데이터를 모두 가져옴
    past_modes = [item for item in arr if item.get("date", "") < date_str]

    # 날짜 오름차순 정렬 후 뒤에서부터 탐색
    past_modes.sort(key=lambda x: x["date"])
    for item in reversed(past_modes):
        if item.get("mode") != "previous":
            return item["mode"]

    return "previous"

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self._send_cors_headers(200)
        self.end_headers()

    def do_GET(self):
        try:
            # 0) 날짜 파라미터
            date_str = self.path.split('?date=')[-1]
            if not date_str or date_str == self.path:
                date_str = datetime.now().strftime('%Y-%m-%d')

            try:
                requested_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                self._send_json_error(400, f"Invalid date format: {date_str}")
                return

            # 1) 한 달 전 날짜 계산
            start_date = requested_date - timedelta(days=60)

            # 2) 야후 파이낸스에서 1년 치 QQQ 데이터 받아와 RSI 계산
            qqq_data = yf.Ticker("QQQ")
            recent_close_prices = qqq_data.history(period="1y")
            # 인덱스를 DatetimeIndex로 변환 후 타임존 제거
            recent_close_prices.index = pd.to_datetime(recent_close_prices.index)
            if recent_close_prices.index.tz is not None:
                recent_close_prices.index = recent_close_prices.index.tz_localize(None)
            # 금요일 데이터만 추출
            friday_data = recent_close_prices[recent_close_prices.index.weekday == 4]
            rsi_values = calculate_rsi(friday_data)

            # 3) requested_date 이하만 필터링
            rsi_up_to_requested = rsi_values.loc[rsi_values.index <= requested_date]
            if len(rsi_up_to_requested) < 2:
                self._send_json_error(404, "No enough RSI data found on or before the given date")
                return

            # 4) start_date 이상, requested_date 이하 금요일 RSI만 추출
            rsi_target_range = rsi_up_to_requested.loc[rsi_up_to_requested.index >= start_date]
            if len(rsi_target_range) < 2:
                self._send_json_error(404, "No Friday RSI data found in the last month range")
                return

            # 5) 범위 내 모든 연속된 금요일 쌍에 대해 모드 계산 + Supabase 저장
            for i in range(1, len(rsi_target_range)):
                prev_rsi = rsi_target_range.iloc[i - 1]
                curr_rsi = rsi_target_range.iloc[i]
                curr_date_str = rsi_target_range.index[i].strftime('%Y-%m-%d')

                mode_calculated = this_week_mode(curr_rsi, prev_rsi)
                # 추가 로직: 만약 "previous"라면, 직전(더 이전) 데이터 중 "previous"가 아닌 모드를 찾아서 대체
                if mode_calculated == "previous":
                    mode_calculated = get_last_non_previous_mode(curr_date_str)

                add_data_to_single_json_array(curr_date_str, mode_calculated, curr_rsi, prev_rsi)

            # 6) 최종적으로 가장 최근 금요일(rsi_up_to_requested.index[-1]) 날짜
            final_date = rsi_up_to_requested.index[-1].strftime('%Y-%m-%d')

            # Supabase에서 해당 날짜 데이터를 가져오기
            supabase = create_supabase_client()
            final_query = supabase.from_("mode").select("*").eq("id", 1).execute()

            if final_query.data and len(final_query.data) > 0:
                self._send_json_response(200, final_query.data[0])
            else:
                # id가 1인 행이 없다면
                empty_result = {"id": 1, "mode": []}
                self._send_json_response(200, empty_result)

        except Exception as e:
            # 모든 예외를 처리하고 클라이언트에게 적절한 오류 메시지를 반환
            self._send_json_error(500, f"Internal Server Error: {str(e)}")

    def _send_cors_headers(self, status_code):
        """CORS 헤더를 응답에 추가합니다."""
        self.send_response(status_code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json_response(self, status_code, data):
        """JSON 응답을 클라이언트에게 전송합니다."""
        self._send_cors_headers(status_code)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_json_error(self, status_code, message):
        """JSON 형식의 오류 메시지를 클라이언트에게 전송합니다."""
        self._send_cors_headers(status_code)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        error_response = {"error": message}
        self.wfile.write(json.dumps(error_response).encode()) 
