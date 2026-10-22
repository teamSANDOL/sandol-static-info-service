"""셔틀버스 시간표 PDF 파서 (결정론적, 외부 API 호출 없음).

iBook이 제공하는 원본 PDF(`/web/RawFileList`)를 좌표 기반으로 읽습니다.
전부 한글(HWP)에서 export된 텍스트 PDF라 OCR이 필요 없습니다.

레이아웃:
  (A) 『정왕역 ↔ 본교』 시(hour)-분(minute) 격자표.
      분 셀에는 세로 괘선이 없어 extract_tables로는 읽히지 않습니다.
      '시간' 머리글 좌우의 세로 괘선으로 시 열을 잡고, 좌측=하교 우측=등교로 가릅니다.
      '※ N시 이후 … 도착버스 탑승' + '~ MM분 (막차)'는 kind=arrival_board 구간 1건입니다.
  (B) 『제2캠퍼스 ↔ 본교 ↔ 정왕역』 표. HH:MM 리터럴이라 열 머리글에서 방향만 정하면 됩니다.
      운행일은 머리글 바로 위 병합 셀('평 일' | '토요일 (일학습병행학부)')로 열마다 정하고,
      셀 안 괄호('(오이도역 도착)', '(정왕역출발 8:55, 9시 )')는 그 열 운행편으로 세지 않습니다.
  (C) '<장소> 출발' 열 머리글 + 'A ↔ B' 그룹 머리글을 쓰는 2캠 전용 문서.

2024년 이후 iBook 등록분 69건 전수 검증 기준으로 원문의 모든 출발 시각을 추출합니다.
방향을 6개 상수 중 하나로 확정하지 못한 건은 버리고 개수만 로그로 남깁니다
(표 머리글이 괘선에 잘려 한쪽 끝만 읽히는 문서가 소수 있습니다).
"""

import io
import re
from itertools import pairwise

import pdfplumber

from app.config.config import logger
from app.config.shuttle_routes import SHUTTLE_DIRECTIONS

LINE_EPS = 1.5  # 이보다 x(또는 y) 차가 작은 선을 세로(가로) 괘선으로 봅니다.
TIME = re.compile(r"(\d{1,2}):(\d{2})")
VALID_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
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
    return sorted(
        {round(l["x0"], 1) for l in page.lines if abs(l["x0"] - l["x1"]) < 1.5}
    )


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
        "kind": "frequent" if end else "fixed",
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
    side_text = ["", ""]
    lasts: list[dict | None] = [None, None]
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
            side_text[side] += " " + joined
            label = (
                " ".join(w["text"] for w in cells if not INT.match(w["text"])).strip()
                or None
            )
            for rng in re.finditer(
                r"(\d{1,2}):(\d{2})\s*~\s*(\d{1,2}):(\d{2})", joined
            ):
                out.append(
                    _trip(
                        direction,
                        day_type,
                        _hhmm(rng.group(1), rng.group(2)),
                        end=_hhmm(rng.group(3), rng.group(4)),
                        note=re.sub(r"\(?[\d:\s~]+\)?", " ", label or "").strip()
                        or "수시운행",
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
                lasts[side] = _trip(
                    direction, day_type, _hhmm(cur_hour, last.group(1)), note="막차"
                )
                out.append(lasts[side])
            for w in cells:
                if INT.match(w["text"]) and int(w["text"]) <= 59:
                    out.append(_trip(direction, day_type, _hhmm(cur_hour, w["text"])))
    _arrival_board(side_text[0], lasts[0], DIR_OUT, day_type, out)
    _arrival_board(side_text[1], lasts[1], DIR_IN, day_type, out)


def _arrival_board(text, last, direction, day_type, out):
    """'※ 17시 이후 시간은 ★ 도착버스 탑승' + '~ 17분 (막차)' → 도착버스 구간 1건.

    정시 출발이 아니라 들어오는 버스를 타는 구간이라 막차 1편으로 두면 그 앞 시간대가
    통째로 사라집니다. 시작 시각('N시 이후')과 막차가 둘 다 있을 때만 바꿉니다.
    """
    start = re.search(r"(\d{1,2})시\s*이후", text)
    if "도착버스" not in text.replace(" ", "") or not start or last is None:
        return
    out.remove(last)
    t = _trip(
        direction,
        day_type,
        _hhmm(start.group(1), 0),
        end=last["departure_time"],
        note="도착버스 탑승",
    )
    t["kind"] = "arrival_board"
    out.append(t)


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
        if ("⇒" in w["text"] or "→" in w["text"])
        and any(t > w["top"] for t in times_top)
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

    cols = _cam_cols(hdr, bounds, float(page.width))
    if cols:
        days = _cam_days(page, region, hdr_top, cols, page_day)
        body = [w for w in region if w["top"] > hdr_top + 3]
        _cam_body(body, cols, days, out)


def _cam_cols(hdr, bounds, width):
    """2캠 표 머리글 → [(x0, x1, 방향)] 열 목록."""
    # 괘선이 머리글 한가운데('본교' | '출발⇒제2캠')를 가르는 문서가 있어,
    # 머리글을 단어 간격으로 먼저 묶은 뒤 열에 배정합니다.
    # 반대로 머리글 사이가 좁아 여러 열이 한 묶음이 되면(26년 2학기) 방향이 깨지므로,
    # 방향으로 확정되지 않는 묶음만 괘선 기준으로 다시 나눕니다.
    hdr_groups = []
    for g in _groups(hdr):
        parts = [g]
        if _canon_direction("".join(w["text"] for w in g)) not in SHUTTLE_DIRECTIONS:
            parts = [[w for w in g if a <= _cx(w) < b] for a, b in pairwise(bounds)]
        hdr_groups += [
            (sum(_cx(w) for w in p) / len(p), "".join(w["text"] for w in p))
            for p in parts
            if p
        ]
    hdr_groups.sort()
    cols = []
    for x0, x1 in pairwise(bounds):
        inside = sorted(g for g in hdr_groups if x0 <= g[0] <= x1)
        # 한 밴드에 머리글이 여럿이면 괘선이 빠진 것이므로 각각 별도 열로 쪼갭니다.
        for i, (c, name) in enumerate(inside):
            lo = x0 if i == 0 else (inside[i - 1][0] + c) / 2
            hi = x1 if i == len(inside) - 1 else (inside[i + 1][0] + c) / 2
            cols.append((lo, hi, _cam_direction(name)))
    if not cols and hdr_groups:
        for i, (c, name) in enumerate(hdr_groups):
            lo = 0.0 if i == 0 else (hdr_groups[i - 1][0] + c) / 2
            hi = width if i == len(hdr_groups) - 1 else (hdr_groups[i + 1][0] + c) / 2
            cols.append((lo, hi, _cam_direction(name)))
    return cols


def _col_of(cols, w):
    """단어가 속한 열 번호. 어느 열에도 안 들면 중심이 가장 가까운 열."""
    hit = next((i for i, c in enumerate(cols) if c[0] <= _cx(w) <= c[1]), None)
    if hit is not None:
        return hit
    return min(
        range(len(cols)), key=lambda i: abs(_cx(w) - (cols[i][0] + cols[i][1]) / 2)
    )


def _cam_body(body, cols, days, out):
    """머리글 아래 셀을 줄 단위로 읽습니다. 괄호 안 시각은 운행편으로 세지 않습니다."""

    def emit(i, direction, hh, mm):
        t = _trip(direction, days[i][0], _hhmm(hh, mm))
        t["day_note"] = days[i][1]
        out.append(t)
        return t

    for row in _rows(body):
        # 괄호는 셀(열) 안에서만 열고 닫습니다. 한 셀의 괄호가 다음 줄로 넘어가
        # 닫히지 않아도 같은 줄 다른 열의 시각은 그대로 읽습니다.
        parens: dict[int, list[dict]] = {}
        last: dict[int, dict] = {}
        notes: set[int] = set()  # 각주가 시작된 열: 그 열의 줄 끝까지 무시
        for k, w in enumerate(row):
            i = _col_of(cols, w)
            if i in notes:
                continue
            text = _unmark(row, k)
            if text is None:  # '★ 정왕역 출발은 꽃집앞 출발' 같은 각주
                notes.add(i)
                continue
            if not text:
                continue
            if i in parens or text.startswith("("):
                buf = parens.setdefault(i, [])
                buf.append({**w, "text": text})
                if sum(x["text"].count("(") - x["text"].count(")") for x in buf) <= 0:
                    _cam_paren(parens.pop(i), i, cols, last, emit)
                continue
            for hh, mm in _times(text):
                last[i] = emit(i, cols[i][2], hh, mm)


def _unmark(row, k):
    """단어 앞 ★·※ 표시를 뗀 텍스트. 각주(표시 뒤가 시각이 아님)면 None.

    '★8:30', '★ 8:30'처럼 표시가 붙은 운행 시각은 살리고, 표시 뒤에 안내문이
    오는 줄만 각주로 봅니다.
    """
    text = row[k]["text"]
    if not text.startswith(("★", "※")):
        return text
    rest = text.lstrip("★※ ")
    nxt = row[k + 1]["text"] if k + 1 < len(row) else ""
    if VALID_TIME.match(rest) or (not rest and VALID_TIME.match(nxt)):
        return rest
    return None


def _times(text):
    """텍스트 속 유효한 HH:MM 목록(시 0~23, 분 0~59)."""
    return VALID_TIME.findall(text)


def _rows(words, tol=4):
    """단어를 줄(top 근접) 단위로 묶어 x 순으로 정렬합니다."""
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and w["top"] - rows[-1][0]["top"] < tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _cam_paren(paren, i, cols, last, emit):
    """2캠 표 셀 안 괄호. 시각이 섞여 있어도 그 열의 운행편으로 세지 않습니다.

    - '(오이도역 도착)'처럼 시각이 없으면 같은 줄·같은 열 직전 운행편의 비고.
    - '(정왕역출발 8:55, 9시 )'는 그 열 방향의 출발지 앞에 '정왕역'을 붙인 방향의
      운행편입니다(본교 ⇒ 2캠 열이면 정왕역 → 본교 → 제2캠퍼스). 원문 각주
      '★ (정왕역출발 8:55, 9시 ) → 본교 운동장옆(서문) 건너편 … → 2캠퍼스'와 같습니다.
    """
    text = " ".join(w["text"] for w in paren)
    times = _times(text) + [
        (h, "00") for h in re.findall(r"(\d{1,2})시(?!\s*이후)", text)
    ]
    if not times:
        note = text.strip("() ")
        if i in last and note:
            last[i]["note"] = note
        return
    origin = re.search(r"\(\s*([^\s()]+?)\s*출발", text)
    direction = origin and _canon_direction(f"{origin.group(1)}⇒{cols[i][2]}")
    if direction in SHUTTLE_DIRECTIONS:
        for hh, mm in sorted(times, key=lambda t: (int(t[0]), int(t[1]))):
            emit(i, direction, hh, mm)


def _vlines_at(page, y):
    """y 높이를 가로지르는 세로 괘선의 x 목록."""
    return sorted(
        {
            round(ln["x0"], 1)
            for ln in page.lines
            if abs(ln["x0"] - ln["x1"]) < LINE_EPS and ln["top"] <= y <= ln["bottom"]
        }
    )


def _cam_days(page, region, hdr_top, cols, page_day):
    """열마다 (운행일, 운행일 비고). 머리글 바로 위 칸(병합 셀)을 괘선으로 나눠 읽습니다.

    '평 일'처럼 글자가 띄어져 있거나 '토요일 (일학습병행학부)'가 두 열에 걸쳐 있어,
    열 x범위만으로 고르면 표 제목 '(평일, 토요일)'이나 엉뚱한 칸을 읽습니다.
    칸 위·아래 가로 괘선이 없으면 운행일은 페이지 기본값입니다.
    """
    above = [h for h in _hlines(page) if h < hdr_top - 1]
    h1 = above[-1] if above else None
    h0 = max((h for h in above if h1 is not None and h < h1 - 1), default=None)
    if h0 is None or h1 is None:
        return [(page_day, None)] * len(cols)
    cuts = _vlines_at(page, (h0 + h1) / 2)
    cells = list(pairwise([0.0, *cuts, float(page.width)]))
    row = sorted((w for w in region if h0 - 1 <= w["top"] < h1), key=lambda w: w["x0"])
    out = []
    for lo, hi, _ in cols:
        c = (lo + hi) / 2
        a, b = next(((a, b) for a, b in cells if a <= c <= b), (c, c))
        text = "".join(w["text"] for w in row if a <= _cx(w) <= b)
        note = re.search(r"\(([^)]*)\)", text)
        out.append((_norm_day(text, page_day), note.group(1).strip() if note else None))
    return out


def _notes(text):
    """표 밖 안내문. '소요시간 …' 줄과 '•' 글머리 줄(표 제목 『』 제외)만 모읍니다."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if "소요시간" in line or (line.startswith("•") and "『" not in line):
            out.append(PERIOD.sub("", line).strip(" •"))
    return [n for n in out if n]


def _header_days(text):
    """표 머리에 적힌 운행일. 본문 안내('토요일 운휴' 등)는 세지 않습니다.

    표 제목 괄호('』시간표 (평일, 토요일)')와 운행일만 있는 줄('평 일 토요일')만 봅니다.
    """
    found = set()
    for m in re.finditer(r"』\s*시간표\s*\(([^)]*)\)", text):
        found |= {d for d in DAY_TOKENS if d in m.group(1).replace(" ", "")}
    for line in text.splitlines():
        flat = re.sub(r"\([^)]*\)", "", line).replace(" ", "")
        hits = {d for d in DAY_TOKENS if d in flat}
        if hits and not re.sub("|".join(DAY_TOKENS), "", flat).strip(",·/"):
            found |= hits
    return found


def parse_shuttle_pdf(
    data: bytes,
) -> tuple[str | None, list[dict], list[str], list[str]]:
    """PDF 바이트 → (제목, ParsedTrip 모양 dict 목록, 표 밖 안내문, 본문에 나온 운행일).

    운행일 목록은 표 머리에 적힌 것만 모으며, 파싱 직후 검증('토요일' 머리글이 있으면
    토요일 편이 있어야 함)에 씁니다.
    """
    trips: list[dict] = []
    notes: list[str] = []
    days: set[str] = set()
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
            notes += _notes(text)
            days |= _header_days(text)
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
    return title, kept, notes, sorted(days)
