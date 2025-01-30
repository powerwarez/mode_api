from http.server import BaseHTTPRequestHandler
import yfinance as yf
import pandas as pd
import json
from datetime import datetime
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

        # Supabase에서 이미 "mode" 테이블에 해당 date가 있는지 확인
        supabase = create_supabase_client()
        check_res = supabase.from_("mode").select("*").eq("date", date_str).execute()
        
        if check_res.data and len(check_res.data) > 0:
            # 이미 저장된 레코드가 있다면, 그 정보를 바로 반환
            existing_entry = check_res.data[0]
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(existing_entry).encode())
            return
        
        # 야후 파이낸스 데이터 가져와서 계산하기
        try:
            qqq_data = yf.Ticker("QQQ")
            recent_close_prices = qqq_data.history(period="1y")
            
            # 인덱스에서 타임존 제거
            recent_close_prices.index = recent_close_prices.index.tz_localize(None)
            
            # 금요일 데이터만 추출
            friday_data = recent_close_prices[recent_close_prices.index.weekday == 4]

            # RSI 계산
            rsi_values = calculate_rsi(friday_data)

            # rsi_values에서 requested_date 이하인 것만 필터링
            filtered_rsi = rsi_values.loc[rsi_values.index <= requested_date]

            if len(filtered_rsi) < 2:
                self.send_error(404, 'No enough RSI data found for or before the given date')
                return

            # 가장 최근 금요일 RSI
            qqq_rsi_late = filtered_rsi.iloc[-1]
            # 그 바로 이전 금요일 RSI
            qqq_rsi_late_late = filtered_rsi.iloc[-2]

            last_date = filtered_rsi.index[-1].strftime('%Y-%m-%d')

            # 모드 계산
            mode_calc = this_week_mode(qqq_rsi_late, qqq_rsi_late_late)

            # 여기가 핵심: add_data_to_db가 이미 날짜가 있으면 새로 추가 안 함
            new_entry = add_data_to_db(last_date, mode_calc)
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            if new_entry:
                self.wfile.write(json.dumps(new_entry).encode())
            else:
                # 혹시 insert 실패 시 응답
                self.wfile.write(json.dumps({"date": last_date, "mode": mode_calc}).encode())

        except Exception as e:
            self.send_error(500, f'Internal Server Error: {str(e)}')