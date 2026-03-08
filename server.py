"""
Korean Stock MCP Server
한국 주식시장 데이터 MCP 서버

Data Source: data.go.kr - 금융위원회
APIs:
  - 주식시세정보 (GetStockSecuritiesInfoService)
  - KRX상장종목정보 (GetKrxListedInfoService)
  - 지수시세정보 (GetMarketIndexInfoService)
  - 주식배당정보 (GetStocDiviInfoService)
"""

import os
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

DATA_GO_KR_KEY = os.getenv("DATA_GO_KR_API_KEY", "")

mcp = FastMCP("Korean Stock Market")

# ─── 주요 종목 코드 (빠른 참조용) ───

POPULAR_STOCKS = {
    "삼성전자": "005930", "SK하이닉스": "000660", "LG에너지솔루션": "373220",
    "삼성바이오로직스": "207940", "현대자동차": "005380", "기아": "000270",
    "셀트리온": "068270", "KB금융": "105560", "POSCO홀딩스": "005490",
    "NAVER": "035420", "카카오": "035720", "삼성SDI": "006400",
    "LG화학": "051910", "현대모비스": "012330", "삼성물산": "028260",
    "SK이노베이션": "096770", "한국전력": "015760", "신한지주": "055550",
    "LG전자": "066570", "삼성생명": "032830", "하나금융지주": "086790",
    "SK텔레콤": "017670", "포스코퓨처엠": "003670", "카카오뱅크": "323410",
    "두산에너빌리티": "034020", "KT": "030200", "크래프톤": "259960",
    "HD현대중공업": "329180", "에코프로비엠": "247540", "LG": "003550",
}

# 주요 지수명
MARKET_INDICES = [
    "코스피", "코스닥", "코스피 200", "코스닥 150",
    "KRX 300", "코스피 배당성장 50",
]

# ─── 캐싱 ───

CACHE_TTL = {
    "realtime": 300,       # 시세: 5분
    "daily": 3600,         # 일별: 1시간
    "stock_list": 86400,   # 종목 목록: 24시간
}

_cache: dict[str, tuple[float, str]] = {}


def _cache_get(key: str) -> str | None:
    if key in _cache:
        expires, value = _cache[key]
        if time.time() < expires:
            return value
        del _cache[key]
    return None


def _cache_set(key: str, value: str, ttl_key: str):
    ttl = CACHE_TTL.get(ttl_key, 600)
    _cache[key] = (time.time() + ttl, value)


# ─── data.go.kr API 클라이언트 ───

API_STOCK_PRICE = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
API_STOCK_LIST = "https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo"
API_MARKET_INDEX = "https://apis.data.go.kr/1160100/service/GetMarketIndexInfoService/getStockMarketIndex"
API_DIVIDEND = "https://apis.data.go.kr/1160100/service/GetStocDiviInfoService/getDiviInfo"


async def _fetch_api(url: str, params: dict) -> dict:
    """data.go.kr API 호출 (공통)"""
    if not DATA_GO_KR_KEY:
        return {"error": "DATA_GO_KR_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."}

    query = {
        "serviceKey": DATA_GO_KR_KEY,
        "resultType": "json",
        "numOfRows": str(params.get("numOfRows", 100)),
        "pageNo": str(params.get("pageNo", 1)),
    }

    # 추가 파라미터 병합
    for k, v in params.items():
        if k not in ("numOfRows", "pageNo") and v:
            query[k] = str(v)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, params=query)
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json()
            # data.go.kr 에러 체크
            header = data.get("response", {}).get("header", {})
            if header.get("resultCode") != "00":
                return {"error": f"API 오류: {header.get('resultMsg', '알 수 없는 오류')}"}
            return data
    except Exception as e:
        return {"error": str(e)}


def _extract_items(data: dict) -> list[dict]:
    """응답에서 items 추출 (단건/다건 처리)"""
    items_raw = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
    if isinstance(items_raw, dict):
        items_raw = [items_raw]
    return items_raw


def _get_total_count(data: dict) -> int:
    return data.get("response", {}).get("body", {}).get("totalCount", 0)


def _safe_int(val) -> int:
    """안전한 정수 변환"""
    try:
        return int(float(str(val).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    """안전한 실수 변환"""
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _format_date(yyyymmdd: str) -> str:
    """YYYYMMDD → YYYY-MM-DD"""
    if len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


def _to_yyyymmdd(date_str: str) -> str:
    """YYYY-MM-DD → YYYYMMDD, 또는 빈 문자열이면 최근 영업일 추정"""
    if not date_str:
        # 오늘 기준 최근 영업일 (data.go.kr은 T+1 영업일 13시 이후 제공)
        now = datetime.now()
        # 2일 전 데이터를 기본값으로 (안전하게)
        target = now - timedelta(days=2)
        # 주말이면 금요일로
        while target.weekday() >= 5:
            target -= timedelta(days=1)
        return target.strftime("%Y%m%d")
    return date_str.replace("-", "")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 1: 주식 시세 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def get_stock_price(
    stock_name: str = "",
    stock_code: str = "",
    date: str = "",
    market: str = "",
    num_results: int = 20,
) -> str:
    """한국 주식 시세를 조회합니다. 종목명 또는 종목코드로 검색할 수 있습니다.
    데이터는 전일 종가 기준입니다 (당일 실시간 아님).

    Args:
        stock_name: 종목명 (예: "삼성전자", "NAVER", "카카오")
        stock_code: 종목 단축코드 6자리 (예: "005930"). stock_name과 둘 중 하나만 입력.
        date: 조회일 (YYYY-MM-DD). 빈 문자열이면 최근 영업일.
        market: 시장 구분 ("KOSPI", "KOSDAQ", "KONEX"). 빈 문자열이면 전체.
        num_results: 조회 건수 (기본 20, 최대 100)

    Returns:
        종가, 시가, 고가, 저가, 거래량, 등락률, 시가총액 등
    """
    num_results = min(num_results, 100)

    # 종목명으로 코드 변환
    if stock_name and not stock_code:
        stock_code = POPULAR_STOCKS.get(stock_name, "")

    cache_key = f"price:{stock_name}:{stock_code}:{date}:{market}:{num_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    base_dt = _to_yyyymmdd(date)
    params = {"numOfRows": num_results, "basDt": base_dt}

    if stock_name:
        params["itmsNm"] = stock_name
    if stock_code:
        params["likeSrtnCd"] = stock_code
    if market:
        params["mrktCls"] = market

    data = await _fetch_api(API_STOCK_PRICE, params)
    if "error" in data:
        return f"API 오류: {data['error']}"

    items = _extract_items(data)
    total = _get_total_count(data)

    if not items:
        # 날짜를 하루 더 뒤로 시도
        prev_dt = (datetime.strptime(base_dt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        params["basDt"] = prev_dt
        data = await _fetch_api(API_STOCK_PRICE, params)
        if "error" in data:
            return f"해당 날짜({_format_date(base_dt)})에 데이터가 없습니다. 주말/공휴일일 수 있습니다."
        items = _extract_items(data)
        total = _get_total_count(data)
        if not items:
            return f"해당 조건에 맞는 데이터가 없습니다. 종목명/코드를 확인하거나 다른 날짜를 시도하세요."
        base_dt = prev_dt

    lines = [f"[주식 시세] {_format_date(base_dt)} (조회 {len(items)}건 / 전체 {total:,}건)\n"]
    lines.append(f"  {'종목명':12} {'종가':>10} {'전일비':>8} {'등락률':>8} {'시가':>10} {'고가':>10} {'저가':>10} {'거래량':>12}")
    lines.append(f"  {'-'*90}")

    for item in items:
        name = item.get("itmsNm", "")
        clpr = _safe_int(item.get("clpr", 0))
        vs = _safe_int(item.get("vs", 0))
        flt_rt = _safe_float(item.get("fltRt", 0))
        mkp = _safe_int(item.get("mkp", 0))
        hipr = _safe_int(item.get("hipr", 0))
        lopr = _safe_int(item.get("lopr", 0))
        trqu = _safe_int(item.get("trqu", 0))
        mrkt = item.get("mrktCtg", "")
        mrkt_tot = _safe_int(item.get("mrktTotAmt", 0))

        sign = "+" if vs > 0 else ""
        flt_sign = "+" if flt_rt > 0 else ""

        lines.append(
            f"  {name:12} {clpr:>10,}원 {sign}{vs:>7,} {flt_sign}{flt_rt:>6.2f}% "
            f"{mkp:>10,} {hipr:>10,} {lopr:>10,} {trqu:>12,}"
        )
        if mrkt_tot > 0:
            # 시가총액 억 단위
            cap_eok = mrkt_tot // 100_000_000
            lines.append(f"    [{mrkt}] 시가총액: {cap_eok:,}억원 | 코드: {item.get('srtnCd', '')}")

    result = "\n".join(lines)
    _cache_set(cache_key, result, "realtime")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 2: 종목 검색
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def search_stock(
    keyword: str,
    market: str = "",
    num_results: int = 20,
) -> str:
    """종목명 키워드로 KRX 상장종목을 검색합니다.

    Args:
        keyword: 검색 키워드 (예: "삼성", "바이오", "에너지")
        market: 시장 구분 ("KOSPI", "KOSDAQ", "KONEX"). 빈 문자열이면 전체.
        num_results: 조회 건수 (기본 20, 최대 100)

    Returns:
        종목코드, 종목명, 시장구분, 법인명, 법인등록번호
    """
    num_results = min(num_results, 100)

    cache_key = f"search:{keyword}:{market}:{num_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    params = {
        "numOfRows": num_results,
        "likeItmsNm": keyword,
    }
    if market:
        params["mrktCtg"] = market

    data = await _fetch_api(API_STOCK_LIST, params)
    if "error" in data:
        return f"API 오류: {data['error']}"

    items = _extract_items(data)
    total = _get_total_count(data)

    if not items:
        return f"'{keyword}' 검색 결과가 없습니다."

    lines = [f"[종목 검색] '{keyword}' (조회 {len(items)}건 / 전체 {total:,}건)\n"]
    lines.append(f"  {'종목코드':8} {'종목명':20} {'시장':8} {'법인명':20}")
    lines.append(f"  {'-'*60}")

    for item in items:
        code = item.get("srtnCd", "")
        name = item.get("itmsNm", "")
        mrkt = item.get("mrktCtg", "")
        corp = item.get("corpNm", "")
        lines.append(f"  {code:8} {name:20} {mrkt:8} {corp:20}")

    result = "\n".join(lines)
    _cache_set(cache_key, result, "stock_list")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 3: 시장 지수 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def get_market_index(
    index_name: str = "",
    date: str = "",
    num_results: int = 20,
) -> str:
    """KOSPI, KOSDAQ 등 주요 시장 지수를 조회합니다.

    Args:
        index_name: 지수명 (예: "코스피", "코스닥"). 빈 문자열이면 주요 지수 전체.
        date: 조회일 (YYYY-MM-DD). 빈 문자열이면 최근 영업일.
        num_results: 조회 건수 (기본 20, 최대 100)

    Returns:
        지수 종가, 등락률, 거래량, 거래대금, 상장시가총액
    """
    num_results = min(num_results, 100)

    cache_key = f"index:{index_name}:{date}:{num_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    base_dt = _to_yyyymmdd(date)
    params = {"numOfRows": num_results, "basDt": base_dt}

    if index_name:
        params["idxNm"] = index_name

    data = await _fetch_api(API_MARKET_INDEX, params)
    if "error" in data:
        return f"API 오류: {data['error']}"

    items = _extract_items(data)

    if not items:
        # 하루 전 재시도
        prev_dt = (datetime.strptime(base_dt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        params["basDt"] = prev_dt
        data = await _fetch_api(API_MARKET_INDEX, params)
        if "error" in data:
            return f"해당 날짜에 지수 데이터가 없습니다."
        items = _extract_items(data)
        if not items:
            return f"해당 조건에 맞는 지수 데이터가 없습니다."
        base_dt = prev_dt

    lines = [f"[시장 지수] {_format_date(base_dt)}\n"]
    lines.append(f"  {'지수명':16} {'종가':>10} {'전일비':>10} {'등락률':>8} {'거래량':>14} {'거래대금(백만)':>14}")
    lines.append(f"  {'-'*80}")

    for item in items:
        idx_nm = item.get("idxNm", "")
        clpr = _safe_float(item.get("clpr", 0))
        vs = _safe_float(item.get("vs", 0))
        flt_rt = _safe_float(item.get("fltRt", 0))
        trqu = _safe_int(item.get("trqu", 0))
        tr_prc = _safe_int(item.get("trPrc", 0))
        tot_amt = _safe_int(item.get("lstgMrktTotAmt", 0))

        sign = "+" if vs > 0 else ""
        flt_sign = "+" if flt_rt > 0 else ""

        lines.append(
            f"  {idx_nm:16} {clpr:>10,.2f} {sign}{vs:>9,.2f} {flt_sign}{flt_rt:>6.2f}% "
            f"{trqu:>14,} {tr_prc // 1_000_000:>14,}"
        )
        if tot_amt > 0:
            lines.append(f"    상장시가총액: {tot_amt // 100_000_000:,}억원 | 구성종목: {item.get('epyItmsCnt', '')}개")

    result = "\n".join(lines)
    _cache_set(cache_key, result, "realtime")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 4: 주가 추이 (N일간)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def get_price_trend(
    stock_name: str = "",
    stock_code: str = "",
    days: int = 7,
) -> str:
    """종목의 최근 N일간 주가 추이를 조회합니다.

    Args:
        stock_name: 종목명 (예: "삼성전자", "NAVER")
        stock_code: 종목 단축코드 6자리 (예: "005930"). stock_name과 둘 중 하나.
        days: 조회 기간 (기본 7일, 최대 30일)

    Returns:
        일별 종가, 등락률, 거래량 추이 및 기간 수익률
    """
    days = min(days, 30)

    if stock_name and not stock_code:
        stock_code = POPULAR_STOCKS.get(stock_name, "")

    cache_key = f"trend:{stock_name}:{stock_code}:{days}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # 기간 범위로 한 번에 조회
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days + 10)  # 주말/공휴일 감안 여유

    params = {
        "numOfRows": days + 10,
        "beginBasDt": start_dt.strftime("%Y%m%d"),
        "endBasDt": end_dt.strftime("%Y%m%d"),
    }

    if stock_name:
        params["itmsNm"] = stock_name
    if stock_code:
        params["likeSrtnCd"] = stock_code

    data = await _fetch_api(API_STOCK_PRICE, params)
    if "error" in data:
        return f"API 오류: {data['error']}"

    items = _extract_items(data)
    if not items:
        return f"'{stock_name or stock_code}' 최근 {days}일간 데이터가 없습니다."

    # 날짜순 정렬 (오래된 것 먼저)
    items.sort(key=lambda x: x.get("basDt", ""))

    # 최근 N영업일만
    items = items[-days:]

    name = items[0].get("itmsNm", stock_name or stock_code)
    lines = [f"[주가 추이] {name} / 최근 {len(items)}영업일\n"]
    lines.append(f"  {'날짜':12} {'종가':>10} {'전일비':>8} {'등락률':>8} {'시가':>10} {'고가':>10} {'저가':>10} {'거래량':>12}")
    lines.append(f"  {'-'*90}")

    for item in items:
        dt = _format_date(item.get("basDt", ""))
        clpr = _safe_int(item.get("clpr", 0))
        vs = _safe_int(item.get("vs", 0))
        flt_rt = _safe_float(item.get("fltRt", 0))
        mkp = _safe_int(item.get("mkp", 0))
        hipr = _safe_int(item.get("hipr", 0))
        lopr = _safe_int(item.get("lopr", 0))
        trqu = _safe_int(item.get("trqu", 0))

        sign = "+" if vs > 0 else ""
        flt_sign = "+" if flt_rt > 0 else ""
        lines.append(
            f"  {dt:12} {clpr:>10,}원 {sign}{vs:>7,} {flt_sign}{flt_rt:>6.2f}% "
            f"{mkp:>10,} {hipr:>10,} {lopr:>10,} {trqu:>12,}"
        )

    # 기간 수익률
    if len(items) >= 2:
        first_close = _safe_int(items[0].get("clpr", 0))
        last_close = _safe_int(items[-1].get("clpr", 0))
        if first_close > 0:
            diff = last_close - first_close
            pct = diff / first_close * 100
            direction = "상승" if diff > 0 else "하락" if diff < 0 else "보합"
            lines.append(f"\n  기간 수익률: {direction} {abs(diff):,}원 ({pct:+.2f}%)")
            lines.append(f"  {_format_date(items[0].get('basDt',''))} {first_close:,}원 → {_format_date(items[-1].get('basDt',''))} {last_close:,}원")

    result = "\n".join(lines)
    _cache_set(cache_key, result, "daily")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 5: 배당 정보 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def get_dividend_info(
    company_name: str = "",
    year: str = "",
    num_results: int = 20,
) -> str:
    """주식 배당 정보를 조회합니다.

    Args:
        company_name: 회사명 (예: "삼성전자", "SK하이닉스")
        year: 결산년도 (예: "2025"). 빈 문자열이면 최근.
        num_results: 조회 건수 (기본 20, 최대 100)

    Returns:
        배당률, 배당금, 배당기준일, 배당지급일
    """
    num_results = min(num_results, 100)

    cache_key = f"dividend:{company_name}:{year}:{num_results}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    params = {"numOfRows": num_results}

    if company_name:
        params["juqtNm"] = company_name
    if year:
        params["stacYymm"] = f"{year}12"  # 12월 결산 기본

    # 날짜 범위로 조회
    if not year:
        now = datetime.now()
        params["beginBasDt"] = (now - timedelta(days=365)).strftime("%Y%m%d")
        params["endBasDt"] = now.strftime("%Y%m%d")

    data = await _fetch_api(API_DIVIDEND, params)
    if "error" in data:
        return f"API 오류: {data['error']}"

    items = _extract_items(data)
    total = _get_total_count(data)

    if not items:
        return f"'{company_name}' 배당 정보가 없습니다. 회사명을 정확히 입력했는지 확인하세요."

    lines = [f"[배당 정보] '{company_name or '전체'}' (조회 {len(items)}건 / 전체 {total:,}건)\n"]

    for item in items:
        corp = item.get("juqtNm", "")
        divi_type = item.get("diviDnm", "")
        divi_rt = _safe_float(item.get("diviRt", 0))
        divi_amt = _safe_int(item.get("diviAmt", 0))
        stk_kind = item.get("stkKndNm", "")
        base_dt = item.get("diviBsDt", "")
        pay_dt = item.get("cshDiviPayDt", "")
        stac = item.get("stacYymm", "")

        lines.append(f"  {corp} ({stk_kind})")
        lines.append(f"    결산: {stac} | 구분: {divi_type}")
        if divi_rt > 0:
            lines.append(f"    배당률: {divi_rt:.2f}%")
        if divi_amt > 0:
            lines.append(f"    배당금: {divi_amt:,}원")
        if base_dt:
            lines.append(f"    배당기준일: {_format_date(base_dt)}")
        if pay_dt:
            lines.append(f"    지급일: {_format_date(pay_dt)}")
        lines.append("")

    result = "\n".join(lines)
    _cache_set(cache_key, result, "daily")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 6: 종목 비교
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def compare_stocks(
    stock_names: str,
    date: str = "",
) -> str:
    """여러 종목의 시세를 비교합니다.

    Args:
        stock_names: 비교할 종목명 (쉼표 구분, 예: "삼성전자,SK하이닉스,NAVER")
        date: 조회일 (YYYY-MM-DD). 빈 문자열이면 최근 영업일.

    Returns:
        종목별 시세 비교 (종가, 등락률, 거래량, 시가총액)
    """
    names = [n.strip() for n in stock_names.split(",") if n.strip()]
    if not names:
        return "비교할 종목명을 쉼표로 구분하여 입력하세요. 예: '삼성전자,SK하이닉스,NAVER'"

    if len(names) > 10:
        names = names[:10]

    base_dt = _to_yyyymmdd(date)

    cache_key = f"compare:{','.join(sorted(names))}:{base_dt}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    results = []
    for name in names:
        code = POPULAR_STOCKS.get(name, "")
        params = {"numOfRows": 1, "basDt": base_dt}
        if code:
            params["likeSrtnCd"] = code
        else:
            params["itmsNm"] = name

        data = await _fetch_api(API_STOCK_PRICE, params)
        if "error" in data:
            results.append({"name": name, "error": data["error"]})
            continue

        items = _extract_items(data)
        if not items:
            # 하루 전 재시도
            prev_dt = (datetime.strptime(base_dt, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
            params["basDt"] = prev_dt
            data = await _fetch_api(API_STOCK_PRICE, params)
            items = _extract_items(data) if "error" not in data else []

        if items:
            item = items[0]
            results.append({
                "name": item.get("itmsNm", name),
                "code": item.get("srtnCd", ""),
                "market": item.get("mrktCtg", ""),
                "date": item.get("basDt", base_dt),
                "close": _safe_int(item.get("clpr", 0)),
                "vs": _safe_int(item.get("vs", 0)),
                "flt_rt": _safe_float(item.get("fltRt", 0)),
                "volume": _safe_int(item.get("trqu", 0)),
                "cap": _safe_int(item.get("mrktTotAmt", 0)),
            })
        else:
            results.append({"name": name, "error": "데이터 없음"})

    lines = [f"[종목 비교] {_format_date(base_dt)} ({len(names)}종목)\n"]
    lines.append(f"  {'종목명':12} {'종가':>10} {'전일비':>8} {'등락률':>8} {'거래량':>12} {'시가총액(억)':>14}")
    lines.append(f"  {'-'*75}")

    for r in results:
        if "error" in r:
            lines.append(f"  {r['name']:12} — {r['error']}")
            continue

        sign = "+" if r["vs"] > 0 else ""
        flt_sign = "+" if r["flt_rt"] > 0 else ""
        cap_eok = r["cap"] // 100_000_000 if r["cap"] > 0 else 0

        lines.append(
            f"  {r['name']:12} {r['close']:>10,}원 {sign}{r['vs']:>7,} {flt_sign}{r['flt_rt']:>6.2f}% "
            f"{r['volume']:>12,} {cap_eok:>14,}"
        )

    # 등락률 순위
    valid = [r for r in results if "error" not in r and r.get("flt_rt") is not None]
    if len(valid) >= 2:
        best = max(valid, key=lambda x: x["flt_rt"])
        worst = min(valid, key=lambda x: x["flt_rt"])
        lines.append(f"\n  최고 등락: {best['name']} ({best['flt_rt']:+.2f}%)")
        lines.append(f"  최저 등락: {worst['name']} ({worst['flt_rt']:+.2f}%)")

    result = "\n".join(lines)
    _cache_set(cache_key, result, "realtime")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Tool 7: 주요 종목 코드 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@mcp.tool()
async def get_popular_stocks() -> str:
    """주요 인기 종목의 코드 목록을 조회합니다. 종목 코드를 모를 때 참고하세요.

    Returns:
        주요 종목명과 6자리 코드 목록
    """
    lines = ["[주요 종목 코드 목록]\n"]
    for name, code in sorted(POPULAR_STOCKS.items()):
        lines.append(f"  {code}: {name}")
    lines.append(f"\n총 {len(POPULAR_STOCKS)}개 종목")
    lines.append("\n참고: 위 목록에 없는 종목은 search_stock 도구로 검색하세요.")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 서버 실행
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    transport = os.getenv("MCP_TRANSPORT", "stdio")

    if transport == "stdio":
        mcp.run()
    else:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
        mcp.run(transport=transport)
