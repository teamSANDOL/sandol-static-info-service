"""셔틀버스 시간표 PDF 파서 (결정론적, 외부 API 호출 없음).

iBook이 제공하는 원본 PDF(`/web/RawFileList`)를 좌표 기반으로 읽습니다.
전부 한글(HWP)에서 export된 텍스트 PDF라 OCR이 필요 없습니다.

레이아웃:
  (A) 『정왕역 ↔ 본교』 시(hour)-분(minute) 격자표.
      분 셀에는 세로 괘선이 없어 extract_tables로는 읽히지 않습니다.
      '시간' 머리글 좌우의 세로 괘선으로 시 열을 잡고, 좌측=하교 우측=등교로 가릅니다.
  (B) 『제2캠퍼스 ↔ 본교 ↔ 정왕역』 표. HH:MM 리터럴이라 열 머리글에서 방향만 정하면 됩니다.
  (C) '<장소> 출발' 열 머리글 + 'A ↔ B' 그룹 머리글을 쓰는 2캠 전용 문서.

2024년 이후 iBook 등록분 69건 전수 검증 기준으로 원문의 모든 출발 시각을 추출합니다.
방향을 6개 상수 중 하나로 확정하지 못한 건은 버리고 개수만 로그로 남깁니다
(표 머리글이 괘선에 잘려 한쪽 끝만 읽히는 문서가 소수 있습니다).
"""

import io
import re

import pdfplumber

from app.config.config import logger
from app.config.shuttle_routes import SHUTTLE_DIRECTIONS

TIME = re.compile(r"(\d{1,2}):(\d{2})")
INT = re.compile(r"^\d{1,2}$")
# '9.1 ~ 12.22', '6/23~8/31' 같은 운행 기간. 시각(HH:MM)과 구분자가 달라 섞이지 않습니다.
PERIOD = re.compile(r"(\d{1,2})[./](\d{1,2})\s*~\s*(\d{1,2})[./](\d{1,2})")

DIR_OUT = "본교 → 정왕역"  # 학교 출발 (하교)
DIR_IN = "정왕역 → 본교"  # 정왕역 출발 (등교)

# app/schemas/shuttle.py 의 DayType 과 같아야 합니다.
DAY_TOKENS = ["평일", "토요일", "일요일", "주말", "공휴일"]

# 긴 이름을 먼저 두어야 '제2캠퍼스'가 '2캠'으로 잘리지 않습니다.
PLACES = [
    ("정왕역", "정왕역"),
    ("제2캠퍼스", "제2캠퍼스"),
    ("2캠퍼스", "제2캠퍼스"),
    ("제2캠", "제2캠퍼스"),
    ("2캠", "제2캠퍼스"),
    ("본교", "본교"),
]


def _cx(w):
    return (w["x0"] + w["x1"]) / 2


def _vlines(page):
    return sorted({round(l["x0"], 1) for l in page.lines if abs(l["x0"] - l["x1"]) < 1.5})


def _hlines(page):
    return sorted(
        {round(l["top"], 1) for l in page.lines if abs(l["top"] - l["bottom"]) < 1.5}
    )


def _hhmm(h, m):
    return f"{int(h):02d}:{int(m):02d}"


def _row(words, top, tol=6):
    return [w for w in words if abs(w["top"] - top) < tol]


def _groups(row, gap=14):
    """같은 행의 단어를 x 인접성으로 묶습니다."""
    out = []
    for w in sorted(row, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1][-1]["x1"] < gap:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def _day_type(text):
    """제목 괄호의 운행일. 운행일 단어가 없으면 본문에서 찾습니다."""
    for m in re.finditer(r"』\s*시간표\s*\(([^)]*)\)", text):
        g = m.group(1).replace(" ", "")
        for d in DAY_TOKENS:
            if d in g:
                return d
    flat = text.replace(" ", "")
    for d in DAY_TOKENS:
        if d in flat:
            return d
    return "평일"


def _norm_day(text, fallback):
    """머리글 조각에서 운행일을 고릅니다. 병합 셀 때문에 잘렸으면 fallback."""
    t = (text or "").replace(" ", "")
    for d in DAY_TOKENS:
        if d in t:
            return d
    return fallback


def _period(text):
    """페이지 텍스트의 운행 기간을 (MM-DD, MM-DD)로. 없으면 (None, None)."""
    m = PERIOD.search(text)
    if not m:
        return None, None
    a, b, c, d = (int(x) for x in m.groups())
    if not (1 <= a <= 12 and 1 <= c <= 12 and 1 <= b <= 31 and 1 <= d <= 31):
        return None, None
    return f"{a:02d}-{b:02d}", f"{c:02d}-{d:02d}"


def _canon_direction(text):
    """머리글 조각에서 장소를 등장 순서대로 뽑아 'A → B'로 만듭니다."""
    t = (text or "").replace(" ", "")
    hits, taken = [], set()
    for pat, name in PLACES:
        for m in re.finditer(re.escape(pat), t):
            span = set(range(m.start(), m.end()))
            if span & taken:
                continue
            taken |= span
            hits.append((m.start(), name))
    hits.sort()
    seq = []
    for _, name in hits:
        if not seq or seq[-1] != name:
            seq.append(name)
    # 정왕역↔제2캠퍼스 직행은 없습니다. 문서에 '정왕역 ↔ 제2캠퍼스'로만 적혀 있어도
    # 실제로는 본교 운동장 옆(서문) 정류장을 경유하므로 경유지를 채워 넣습니다.
    if seq == ["정왕역", "제2캠퍼스"] or seq == ["제2캠퍼스", "정왕역"]:
        seq.insert(1, "본교")
    return " → ".join(seq) if len(seq) >= 2 else None


def _trip(direction, day_type, departure_time, end=None, note=None):
    """ParsedTrip 과 같은 모양의 dict. 운행 기간은 페이지 단위로 나중에 채웁니다."""
    return {
        "direction": direction,
        "day_type": day_type,
        "day_note": None,
        "departure_time": departure_time,
        "departure_time_end": end,
        "valid_from": None,
        "valid_to": None,
        "period_note": None,
        "boarding_place": None,
        "note": note,
    }


# ---------------- (A) 시-분 격자표 ----------------


def _hour_column(page, words):
    """'시간' 머리글 좌우의 세로 괘선 → 시 열 x범위."""
    for h in [w for w in words if w["text"].strip() == "시간"]:
        cx = _cx(h)
        vs = _vlines(page)
        left = [v for v in vs if v < cx]
        right = [v for v in vs if v > cx]
        if left and right:
            return max(left), min(right), h["top"]
    return None


def _grid_bottom(page, words, top0):
    """격자표 하단 = 2캠 표 머리글 위. 없으면 2캠 표 숫자까지 빨려 들어옵니다."""
    cam = [
        w["top"]
        for w in words
        if ("2캠" in w["text"] or "２캠" in w["text"]) and w["top"] > top0
    ]
    return min(cam, default=page.height)


def _parse_grid(page, words, day_type, out):
    hc = _hour_column(page, words)
    if not hc:
        return
    left_x, right_x, top0 = hc
    bottom = _grid_bottom(page, words, top0)
    # 마지막 괘선 아래 잔여 행(대개 막차 줄)도 밴드에 포함시킵니다.
    hs = sorted(set([h for h in _hlines(page) if top0 - 2 <= h <= bottom] + [bottom]))
    words = [w for w in words if top0 < w["top"] < bottom]
    cur_hour = None
    for a, b in zip(hs, hs[1:]):
        row = [w for w in words if a - 1 <= w["top"] < b - 1]
        if not row:
            continue
        mid = [w for w in row if left_x <= _cx(w) <= right_x]
        hours = [w["text"] for w in mid if INT.match(w["text"])]
        if hours:
            cur_hour = int(hours[0])
        if cur_hour is None:
            continue
        # 주석이 두 줄로 넘쳐 시 칸이 빈 밴드가 있으므로 cur_hour를 이월합니다.
        for side, direction in ((0, DIR_OUT), (1, DIR_IN)):
            cells = [
                w for w in row if (_cx(w) < left_x if side == 0 else _cx(w) > right_x)
            ]
            joined = " ".join(w["text"] for w in cells)
            label = (
                " ".join(w["text"] for w in cells if not INT.match(w["text"])).strip()
                or None
            )
            for rng in re.finditer(r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})", joined):
                out.append(
                    _trip(
                        direction,
                        day_type,
                        _hhmm(rng.group(1), rng.group(2)),
                        end=_hhmm(rng.group(3), rng.group(4)),
                        note=label or "수시운행",
                    )
                )
            # 범위 처리 후에도 같은 셀을 계속 읽어야
            # '만차시 선 출발함(17:30~18:30) 10' 같은 행의 분을 잃지 않습니다.
            for hh, mm in TIME.findall(joined):
                if re.search(rf"{hh}:{mm}\s*~|~\s*{hh}:{mm}", joined):
                    continue
                out.append(_trip(direction, day_type, _hhmm(hh, mm), note=label))
            last = re.search(r"~\s*(\d{1,2})분", joined)
            if last:
                out.append(
                    _trip(direction, day_type, _hhmm(cur_hour, last.group(1)), note="막차")
                )
            for w in cells:
                if INT.match(w["text"]) and int(w["text"]) <= 59:
                    out.append(_trip(direction, day_type, _hhmm(cur_hour, w["text"])))


# ---------------- (B)/(C) 2캠 표 ----------------


def _cam_direction(header):
    return _canon_direction(header) or (header or "").strip()


def _parse_departure_cols(page, words, out, day):
    """(C) '<장소> 출발' 열 머리글 + 'A ↔ B' 그룹 머리글."""
    dep = [w for w in words if w["text"].strip() == "출발"]
    if not dep:
        return
    hdr_top = min(w["top"] for w in dep)
    row = _row(words, hdr_top)
    cols = []
    for w in dep:
        if abs(w["top"] - hdr_top) >= 6:
            continue
        left = [x for x in row if x["x1"] <= w["x0"] + 1]
        if not left:
            continue
        place = max(left, key=lambda x: x["x1"])
        cols.append([place["x0"], w["x1"], place["text"].strip()])
    if not cols:
        return

    pair_rows = sorted(
        {w["top"] for w in words if "↔" in w["text"] and w["top"] < hdr_top - 3}
    )
    pairs = []
    if pair_rows:
        pr = [w for w in words if abs(w["top"] - pair_rows[-1]) < 6]
        for g in _groups(pr, gap=20):
            txt = "".join(x["text"] for x in g)
            ends = [e.strip() for e in txt.split("↔") if e.strip()]
            if len(ends) >= 2:
                pairs.append((g[0]["x0"] - 6, g[-1]["x1"] + 6, ends))

    for w in words:
        if w["top"] <= hdr_top:
            continue
        for hh, mm in TIME.findall(w["text"]):
            if int(hh) > 23 or int(mm) > 59:
                continue
            col = min(cols, key=lambda c: abs(_cx(w) - (c[0] + c[1]) / 2))
            origin, dest = col[2], None
            for x0, x1, ends in pairs:
                if x0 <= (col[0] + col[1]) / 2 <= x1:
                    other = [
                        e
                        for e in ends
                        if e.replace("제", "") not in origin
                        and origin.replace("제", "") not in e
                    ]
                    dest = other[0] if other else None
                    break
            raw = f"{origin} → {dest}" if dest else origin
            out.append(_trip(_canon_direction(raw) or raw, day, _hhmm(hh, mm)))


def _parse_cam(page, words, out, grid_bottom, page_day):
    cam = [
        w["top"]
        for w in words
        if ("2캠" in w["text"] or "２캠" in w["text"]) and w["top"] > (grid_bottom or 0)
    ]
    if not cam:
        _parse_departure_cols(page, words, out, page_day)
        return
    top = min(cam)
    region = [w for w in words if w["top"] > top]
    if not region:
        return

    # 방향 머리글은 '아래에 시각이 있는' 화살표 행만 인정합니다.
    # 하단 '탑승 장소 안내' 표의 → 를 머리글로 오인하지 않기 위함입니다.
    times_top = [w["top"] for w in region if TIME.search(w["text"])]
    arrows = [
        w
        for w in region
        if ("⇒" in w["text"] or "→" in w["text"]) and any(t > w["top"] for t in times_top)
    ]
    if not arrows:
        _parse_departure_cols(page, words, out, page_day)
        return
    hdr_top = min(w["top"] for w in arrows)
    hdr = _row(region, hdr_top)

    vs = sorted(
        {
            round(l["x0"], 1)
            for l in page.lines
            if abs(l["x0"] - l["x1"]) < 1.5 and max(l["top"], l["bottom"]) > top
        }
    )
    bounds = [0.0] + vs + [float(page.width)]

    # 괘선이 머리글 한가운데('본교' | '출발⇒제2캠')를 가르는 문서가 있어,
    # 머리글을 단어 간격으로 먼저 묶은 뒤 열에 배정합니다.
    hdr_groups = sorted(
        (sum(_cx(w) for w in g) / len(g), "".join(w["text"] for w in g))
        for g in _groups(hdr)
    )
    cols = []
    for x0, x1 in zip(bounds, bounds[1:]):
        inside = sorted(g for g in hdr_groups if x0 <= g[0] <= x1)
        # 한 밴드에 머리글이 여럿이면 괘선이 빠진 것이므로 각각 별도 열로 쪼갭니다.
        for i, (c, name) in enumerate(inside):
            lo = x0 if i == 0 else (inside[i - 1][0] + c) / 2
            hi = x1 if i == len(inside) - 1 else (inside[i + 1][0] + c) / 2
            cols.append((lo, hi, _cam_direction(name)))
    if not cols and hdr_groups:
        for i, (c, name) in enumerate(hdr_groups):
            lo = 0.0 if i == 0 else (hdr_groups[i - 1][0] + c) / 2
            hi = (
                float(page.width)
                if i == len(hdr_groups) - 1
                else (hdr_groups[i + 1][0] + c) / 2
            )
            cols.append((lo, hi, _cam_direction(name)))
    if not cols:
        return

    day_rows = [w for w in region if w["top"] < hdr_top - 3]
    for w in region:
        if w["top"] < hdr_top:
            continue
        for hh, mm in TIME.findall(w["text"]):
            if int(hh) > 23 or int(mm) > 59:
                continue
            col = next((c for c in cols if c[0] <= _cx(w) <= c[1]), None) or min(
                cols, key=lambda c: abs(_cx(w) - (c[0] + c[1]) / 2)
            )
            day = _norm_day(
                "".join(d["text"] for d in day_rows if col[0] <= _cx(d) <= col[1]),
                page_day,
            )
            out.append(_trip(col[2], day, _hhmm(hh, mm)))


def parse_shuttle_pdf(data: bytes) -> tuple[str | None, list[dict]]:
    """PDF 바이트 → (제목, ParsedTrip 모양 dict 목록)."""
    trips: list[dict] = []
    title = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            if not words:
                continue
            text = page.extract_text() or ""
            if title is None:
                lines = [x.strip() for x in text.strip().splitlines() if x.strip()]
                title = lines[0] if lines else None
            day = _day_type(text)
            valid_from, valid_to = _period(text)

            page_trips: list[dict] = []
            hc = _hour_column(page, words)
            grid_bottom = None
            if hc:
                _parse_grid(page, words, day, page_trips)
                grid_bottom = hc[2]
            _parse_cam(page, words, page_trips, grid_bottom, day)

            for t in page_trips:
                t["valid_from"] = valid_from
                t["valid_to"] = valid_to
            trips += page_trips

    kept = [t for t in trips if t["direction"] in SHUTTLE_DIRECTIONS]
    dropped = len(trips) - len(kept)
    if dropped:
        logger.warning("셔틀 PDF: 방향 확정 실패로 %d/%d건 제외", dropped, len(trips))
    return title, kept
