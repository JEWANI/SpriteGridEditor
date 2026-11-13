"""
mask_convert.py
-----------------
컬러 옷 시트 → **회색 원본 + 인덱스 마스크(_M)** 변환과 관련 계산 (기획서 §4.3, §4.5-3·4). Qt 의존 없음.

마스크 규격은 CHR-ART.5 §3.3 그대로다. 메모리에서는 **인덱스(0~4)** 로 들고 있고 저장할 때만 PNG 값으로 바꾼다.

| PNG 값 | 인덱스 | 영역 |
|---|---|---|
| 0   | 0 | 영역 아님 (투명·고정색) |
| 64  | 1 | 주색 |
| 128 | 2 | 보조색 |
| 192 | 3 | 포인트 |
| 255 | 4 | 패치 |

- 회색: 상대 휘도 Y(선형 RGB)를 구해 같은 Y의 회색으로 바꾼다 → Lab L*가 그대로 보존된다(L*는 Y만의 함수).
- 밝기 정규화(CHR-ART.5 §3.1): 불투명 픽셀 밝기 2~98 퍼센타일 → 0.42~1.0 선형 매핑 후, 중앙값이 0.72가 되도록 감마 하나 조정.
- 영역 분할: 시트 **전체** 불투명 픽셀을 Lab k-means로 묶는다(시트 공통 중심 → 측면·후면 셀도 같은 번호).
- 미리보기 틴트: Unity `OutfitTintUI` 셰이더와 같은 식 — ``saturate(회색 × Gain) × 영역 색``, 인덱스 0은 회색 그대로.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from sheet_model import GridSpec

# --------------------------------------------------------------------------- #
#  인덱스 ↔ PNG 값
# --------------------------------------------------------------------------- #

MASK_VALUES = np.array([0, 64, 128, 192, 255], dtype=np.uint8)  # 인덱스 → PNG 값
REGION_COUNT = 4  # 인덱스 1~4
REGION_COLORS = {  # 마스크 오버레이 의사색 (기획서 §4.3-3: 1=빨강, 2=파랑, 3=초록, 4=노랑)
    1: (255, 64, 64),
    2: (64, 128, 255),
    3: (64, 220, 96),
    4: (255, 220, 48),
}


def index_to_png(index: np.ndarray) -> np.ndarray:
    return MASK_VALUES[np.clip(index, 0, 4)]


def png_to_index(values: np.ndarray) -> tuple[np.ndarray, int]:
    """PNG 값 → 인덱스. 규격 밖 값은 가장 가까운 값으로 스냅. (인덱스, 스냅한 픽셀 수)."""
    v = values.astype(np.int16)
    idx = np.abs(v[..., None] - MASK_VALUES.astype(np.int16)).argmin(axis=-1).astype(np.uint8)
    snapped = int((~np.isin(values, MASK_VALUES)).sum())
    return idx, snapped


# --------------------------------------------------------------------------- #
#  회색 / 밝기 정규화
# --------------------------------------------------------------------------- #

BRIGHT_LO = 0.42
BRIGHT_HI = 1.0
BRIGHT_MEDIAN = 0.72


def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = c.astype(np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(y: np.ndarray) -> np.ndarray:
    y = np.clip(y, 0.0, 1.0)
    return np.where(y <= 0.0031308, y * 12.92, 1.055 * np.power(y, 1 / 2.4) - 0.055)


def to_gray(arr: np.ndarray) -> np.ndarray:
    """RGBA → 회색 값 (H, W) uint8. 같은 상대 휘도(= 같은 L*)의 회색."""
    lin = _srgb_to_linear(arr[..., :3])
    y = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722
    return np.round(_linear_to_srgb(y) * 255).astype(np.uint8)


def opaque_mask(arr: np.ndarray) -> np.ndarray:
    return arr[..., 3] > 0


@dataclass
class BrightnessStats:
    p2: float
    p98: float
    median: float
    is_gray: bool  # 채도가 거의 없음 (이미 회색 시트)

    @property
    def in_rule(self) -> bool:
        """CHR-ART.5 §3.1 범위(0.42~1.0, 중앙값 0.72 근처) 안인가."""
        return self.p2 >= BRIGHT_LO - 0.03 and abs(self.median - BRIGHT_MEDIAN) <= 0.05


def brightness_stats(arr: np.ndarray, gray: np.ndarray | None = None) -> BrightnessStats:
    op = opaque_mask(arr)
    if not op.any():
        return BrightnessStats(0.0, 0.0, 0.0, True)
    g = (to_gray(arr) if gray is None else gray)[op].astype(np.float64) / 255.0
    rgb = arr[..., :3][op].astype(np.int16)
    spread = rgb.max(axis=1) - rgb.min(axis=1)
    return BrightnessStats(float(np.percentile(g, 2)), float(np.percentile(g, 98)),
                           float(np.median(g)), bool((spread <= 4).mean() >= 0.98))


def should_normalize(stats: BrightnessStats) -> bool:
    """기본값: 켬. 이미 회색이고 규칙 범위 안이면 끈다 (§4.3-1 자동 판단)."""
    return not (stats.is_gray and stats.in_rule)


def normalize_brightness(gray: np.ndarray, opaque: np.ndarray) -> tuple[np.ndarray, float]:
    """불투명 픽셀 밝기를 0.42~1.0 / 중앙값 0.72로 재매핑. (새 회색, 쓴 감마)."""
    if not opaque.any():
        return gray.copy(), 1.0
    b = gray.astype(np.float64) / 255.0
    vals = b[opaque]
    p2, p98 = np.percentile(vals, 2), np.percentile(vals, 98)
    span = max(p98 - p2, 1e-6)
    t = np.clip((b - p2) / span, 0.0, 1.0)
    tm = float(np.median(t[opaque]))
    target = (BRIGHT_MEDIAN - BRIGHT_LO) / (BRIGHT_HI - BRIGHT_LO)
    gamma = 1.0
    if 0.0 < tm < 1.0:
        gamma = float(np.clip(np.log(target) / np.log(tm), 0.25, 4.0))
    out = BRIGHT_LO + (BRIGHT_HI - BRIGHT_LO) * np.power(t, gamma)
    res = gray.copy()
    res[opaque] = np.round(out[opaque] * 255).astype(np.uint8)
    return res, gamma


def make_gray_rgba(arr: np.ndarray, normalize: bool) -> np.ndarray:
    """회색 원본 RGBA (알파 유지, 투명 픽셀 RGB는 0)."""
    gray = to_gray(arr)
    op = opaque_mask(arr)
    if normalize:
        gray, _g = normalize_brightness(gray, op)
    out = np.zeros_like(arr)
    out[..., 0] = out[..., 1] = out[..., 2] = np.where(op, gray, 0)
    out[..., 3] = arr[..., 3]
    return out


# --------------------------------------------------------------------------- #
#  Lab / k-means
# --------------------------------------------------------------------------- #

_D65 = np.array([0.95047, 1.0, 1.08883])
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """(..., 3) uint8 sRGB → (..., 3) float Lab (D65)."""
    xyz = _srgb_to_linear(rgb) @ _M_RGB2XYZ.T / _D65
    eps = 216 / 24389
    f = np.where(xyz > eps, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _weighted(lab: np.ndarray, l_weight: float) -> np.ndarray:
    w = lab.copy()
    w[..., 0] *= l_weight
    return w


@dataclass
class Clusters:
    """k-means 결과. 클러스터는 픽셀 수 내림차순으로 정렬돼 있다."""

    labels: np.ndarray  # (H, W) int16, 투명 = -1
    centers_rgb: list[tuple[int, int, int]]  # 대표색 (구성 픽셀 평균)
    sizes: list[int]  # 픽셀 수
    centers_lab: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))

    @property
    def k(self) -> int:
        return len(self.sizes)


def _unique_colors(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """불투명 픽셀의 고유 색 (색 (U,3), 개수, 픽셀→고유색 인덱스, 불투명 마스크)."""
    op = opaque_mask(arr)
    rgb = arr[..., :3][op]
    packed = (rgb[:, 0].astype(np.uint32) << 16) | (rgb[:, 1].astype(np.uint32) << 8) | rgb[:, 2]
    uniq, inverse, counts = np.unique(packed, return_inverse=True, return_counts=True)
    colors = np.stack([(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], axis=1).astype(np.uint8)
    return colors, counts, inverse, op


def _assemble(arr, colors, counts, inverse, op, assign_u, k) -> Clusters:
    """고유색 단위 배정(assign_u) → 픽셀 라벨 + 크기순 정렬."""
    sizes = np.bincount(assign_u, weights=counts, minlength=k)
    order = np.argsort(-sizes, kind="stable")
    remap = np.empty(k, dtype=np.int16)
    remap[order] = np.arange(k, dtype=np.int16)
    assign_sorted = remap[assign_u]
    labels = np.full(arr.shape[:2], -1, dtype=np.int16)
    labels[op] = assign_sorted[inverse]
    centers_rgb, out_sizes, centers_lab = [], [], []
    for new in range(k):
        sel = assign_sorted == new
        w = counts[sel].astype(np.float64)
        n = int(w.sum())
        if n == 0:
            centers_rgb.append((0, 0, 0))
            centers_lab.append(np.zeros(3))
        else:
            c = (colors[sel].astype(np.float64) * w[:, None]).sum(axis=0) / n
            centers_rgb.append(tuple(int(round(v)) for v in c))
            centers_lab.append(rgb_to_lab(np.array(centers_rgb[-1], dtype=np.uint8)))
        out_sizes.append(n)
    return Clusters(labels, centers_rgb, out_sizes, np.array(centers_lab))


def kmeans(arr: np.ndarray, k: int, l_weight: float = 0.5, iters: int = 40, seed: int = 0) -> Clusters:
    """시트 전체 불투명 픽셀 Lab k-means (고유색 + 개수 가중으로 빠르게). l_weight: 명암(L) 비중."""
    colors, counts, inverse, op = _unique_colors(arr)
    if len(colors) == 0:
        return Clusters(np.full(arr.shape[:2], -1, np.int16), [], [])
    k = max(1, min(int(k), len(colors)))
    pts = _weighted(rgb_to_lab(colors), l_weight)
    w = counts.astype(np.float64)
    rng = np.random.default_rng(seed)
    # k-means++ 초기화 (가중)
    centers = [pts[int(np.argmax(w))]]
    for _ in range(1, k):
        d2 = np.min(((pts[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        prob = d2 * w
        if prob.sum() <= 0:
            break
        centers.append(pts[rng.choice(len(pts), p=prob / prob.sum())])
    c = np.array(centers)
    assign = np.zeros(len(pts), dtype=np.int64)
    for _ in range(iters):
        d = ((pts[:, None, :] - c[None]) ** 2).sum(-1)
        new_assign = d.argmin(axis=1)
        if _ > 0 and np.array_equal(new_assign, assign):
            break
        assign = new_assign
        for j in range(len(c)):
            sel = assign == j
            if sel.any():
                c[j] = (pts[sel] * w[sel, None]).sum(0) / w[sel].sum()
    return _assemble(arr, colors, counts, inverse, op, assign, len(c))


def assign_to_colors(arr: np.ndarray, rep_colors: list[tuple[int, int, int]],
                     l_weight: float = 0.5) -> Clusters:
    """스포이트로 찍은 대표색들로 다시 분할 (가장 가까운 대표색에 배정, 반복 없음)."""
    colors, counts, inverse, op = _unique_colors(arr)
    k = len(rep_colors)
    if len(colors) == 0 or k == 0:
        return Clusters(np.full(arr.shape[:2], -1, np.int16), list(rep_colors), [0] * k)
    pts = _weighted(rgb_to_lab(colors), l_weight)
    reps = _weighted(rgb_to_lab(np.array(rep_colors, dtype=np.uint8)), l_weight)
    assign = ((pts[:, None, :] - reps[None]) ** 2).sum(-1).argmin(axis=1)
    sizes = np.bincount(assign, weights=counts, minlength=k)
    labels = np.full(arr.shape[:2], -1, dtype=np.int16)
    labels[op] = assign[inverse].astype(np.int16)
    # 대표색 순서는 사용자가 찍은 그대로 유지 (칩 위치가 바뀌지 않게)
    return Clusters(labels, [tuple(int(v) for v in c) for c in rep_colors],
                    [int(s) for s in sizes], reps)


def labels_to_index(labels: np.ndarray, mapping: list[int]) -> np.ndarray:
    """클러스터 라벨 → 마스크 인덱스. mapping[i] = 클러스터 i의 인덱스(0~4). 투명(-1) → 0."""
    lut = np.zeros(len(mapping) + 1, dtype=np.uint8)
    lut[1:] = np.clip(mapping, 0, 4)
    return lut[labels.astype(np.int32) + 1]


def horizontal_split(arr: np.ndarray, grid: GridSpec, y_line: int,
                     upper: int = 1, lower: int = 2) -> np.ndarray:
    """수평 분할 (§4.3-2 보조 모드): 셀 안의 y 기준선 위 = upper, 아래 = lower (불투명 픽셀만)."""
    h, w = arr.shape[:2]
    y_in_cell = (np.arange(h) % max(1, grid.cell_h))[:, None]
    idx = np.where(y_in_cell < y_line, upper, lower).astype(np.uint8)
    idx = np.broadcast_to(idx, (h, w)).copy()
    idx[~opaque_mask(arr)] = 0
    return idx


# --------------------------------------------------------------------------- #
#  후처리
# --------------------------------------------------------------------------- #

_NEIGHBORS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def _shift(a: np.ndarray, dy: int, dx: int, fill) -> np.ndarray:
    out = np.full_like(a, fill)
    h, w = a.shape
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = a[ys, xs]
    return out


def despeckle(index: np.ndarray, opaque: np.ndarray, passes: int = 2) -> np.ndarray:
    """고립된 1~2픽셀 잡티를 이웃(8방향, 불투명만) 다수 인덱스로 흡수한다."""
    idx = index.copy()
    for _ in range(passes):
        counts = np.zeros((5,) + idx.shape, dtype=np.int16)
        for dy, dx in _NEIGHBORS:
            nb = _shift(idx, dy, dx, 255)
            nop = _shift(opaque, dy, dx, False)
            for v in range(5):
                counts[v] += (nb == v) & nop
        same = np.take_along_axis(counts, idx[None].astype(np.int64), axis=0)[0]
        total = counts.sum(axis=0)
        majority = counts.argmax(axis=0).astype(np.uint8)
        fix = opaque & (same <= 1) & (total >= 3) & (majority != idx)
        if not fix.any():
            break
        idx[fix] = majority[fix]
    idx[~opaque] = 0
    return idx


def enforce_alpha(index: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, int]:
    """알파 0인 곳은 항상 인덱스 0. (정정한 마스크, 정정 픽셀 수)."""
    bad = (alpha == 0) & (index != 0)
    n = int(bad.sum())
    if n:
        index = index.copy()
        index[bad] = 0
    return index, n


# --------------------------------------------------------------------------- #
#  미리보기 (틴트 / 의사색)
# --------------------------------------------------------------------------- #


def parse_hex(text: str) -> tuple[int, int, int] | None:
    t = text.strip().lstrip("#")
    if len(t) != 6:
        return None
    try:
        return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)
    except ValueError:
        return None


def to_hex(c: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


def tint(gray_rgba: np.ndarray, index: np.ndarray, colors: list[tuple[int, int, int]],
         gain: float = 1.0) -> np.ndarray:
    """Unity OutfitTintUI와 같은 식: saturate(회색 × gain) × 영역 색 (sRGB 기준). 인덱스 0은 흰색(=그대로)."""
    lut = np.ones((5, 3), dtype=np.float64)
    for i, c in enumerate(colors[:4]):
        lut[i + 1] = np.array(c, dtype=np.float64) / 255.0
    base = np.clip(gray_rgba[..., :3].astype(np.float64) / 255.0 * gain, 0.0, 1.0)
    rgb = base * lut[np.clip(index, 0, 4)]
    out = gray_rgba.copy()
    out[..., :3] = np.round(rgb * 255).astype(np.uint8)
    return out


def pseudo_color(index: np.ndarray, opaque: np.ndarray) -> np.ndarray:
    """마스크 의사색 RGBA (영역 1~4만 색, 나머지는 투명)."""
    out = np.zeros(index.shape + (4,), dtype=np.uint8)
    for v, c in REGION_COLORS.items():
        sel = index == v
        out[sel, :3] = c
        out[sel, 3] = 255
    out[~opaque] = 0
    return out


def missing_overlay(index: np.ndarray, opaque: np.ndarray) -> np.ndarray:
    """알파는 있는데 인덱스 0인 픽셀(영역 누락 의심) 강조 RGBA (마젠타)."""
    out = np.zeros(index.shape + (4,), dtype=np.uint8)
    sel = opaque & (index == 0)
    out[sel] = (255, 0, 255, 255)
    return out


def hsv_value(c: tuple[int, int, int]) -> float:
    return max(c) / 255.0


V_MIN = 0.30  # CHR-ART.5 §4.4 사용자 정의 색 밝기 하한
SHADE_MIN_RANGE = 16  # 영역 안 가장 어두운 곳~가장 밝은 곳 차이가 이보다 작으면 음영이 사라졌다고 본다


@dataclass
class ShadeInfo:
    region: int
    darkest: int  # 0~255 (HSV V)
    brightest: int
    color_v: float
    warn: bool


def shade_report(tinted: np.ndarray, index: np.ndarray, colors: list[tuple[int, int, int]]) -> list[ShadeInfo]:
    """영역별 가장 어두운 음영/가장 밝은 곳(HSV V) + 경고 (§4.5-3)."""
    v = tinted[..., :3].max(axis=-1)
    res: list[ShadeInfo] = []
    for r in range(1, 5):
        sel = (index == r) & (tinted[..., 3] > 0)
        if not sel.any():
            continue
        vals = v[sel]
        lo, hi = int(vals.min()), int(vals.max())
        cv = hsv_value(colors[r - 1]) if r - 1 < len(colors) else 1.0
        res.append(ShadeInfo(r, lo, hi, cv, cv < V_MIN or (hi - lo) < SHADE_MIN_RANGE))
    return res


# --------------------------------------------------------------------------- #
#  통계 / 검증 (§4.5-4, §4.3-5)
# --------------------------------------------------------------------------- #


@dataclass
class MaskStats:
    cell_counts: np.ndarray  # (rows, cols, 5) 인덱스별 픽셀 수 (불투명만)
    cell_opaque: np.ndarray  # (rows, cols)
    total: np.ndarray  # (5,)
    opaque: int
    missing: int  # 알파는 있는데 인덱스 0
    inconsistent: list[tuple[int, int, int]]  # (영역, 행, 열) — 다른 셀보다 크게 적거나 없음

    def ratio(self, region: int) -> float:
        return float(self.total[region]) / self.opaque if self.opaque else 0.0

    @property
    def missing_ratio(self) -> float:
        return self.missing / self.opaque if self.opaque else 0.0


CONSISTENCY_RATIO = 0.3  # 셀의 영역 픽셀 수가 (그 영역이 있는 셀들의) 중앙값의 30% 미만이면 급감으로 본다


def mask_stats(index: np.ndarray, alpha: np.ndarray, grid: GridSpec) -> MaskStats:
    op = alpha > 0
    rows, cols = grid.rows, grid.cols
    h, w = rows * grid.cell_h, cols * grid.cell_w
    idx = np.where(op, index, 255)[:h, :w]
    cells = idx.reshape(rows, grid.cell_h, cols, grid.cell_w).transpose(0, 2, 1, 3).reshape(rows, cols, -1)
    counts = np.stack([(cells == v).sum(axis=-1) for v in range(5)], axis=-1)
    cell_op = counts.sum(axis=-1)
    total = counts.reshape(-1, 5).sum(axis=0)
    opaque = int(cell_op.sum())
    inconsistent: list[tuple[int, int, int]] = []
    for r in range(1, 5):
        c = counts[..., r]
        present = c[c > 0]
        if len(present) == 0:
            continue
        med = float(np.median(present))
        for (ri, ci), n in np.ndenumerate(c):
            if cell_op[ri, ci] > 0 and n < CONSISTENCY_RATIO * med:
                inconsistent.append((r, ri, ci))
    return MaskStats(counts, cell_op, total, opaque, int(total[0]), inconsistent)


@dataclass
class MaskIssue:
    level: str  # "error" | "warn" | "info"
    key: str
    fmt: dict


def validate_mask(rgba: np.ndarray, index: np.ndarray, snapped: int, missing_warn_pct: float) -> tuple[np.ndarray, list[MaskIssue]]:
    """저장 전 검증 (§4.3-5). 정정한 인덱스 마스크와 문제 목록을 돌려준다."""
    issues: list[MaskIssue] = []
    if index.shape != rgba.shape[:2]:
        return index, [MaskIssue("error", "validate.mask_size",
                                 {"mw": index.shape[1], "mh": index.shape[0],
                                  "w": rgba.shape[1], "h": rgba.shape[0]})]
    if snapped:
        issues.append(MaskIssue("warn", "validate.mask_snapped", {"n": snapped}))
    fixed, n = enforce_alpha(index, rgba[..., 3])
    if n:
        issues.append(MaskIssue("info", "validate.mask_alpha_fixed", {"n": n}))
    op = rgba[..., 3] > 0
    total = int(op.sum())
    if total:
        miss = int((op & (fixed == 0)).sum())
        pct = miss * 100.0 / total
        if pct >= missing_warn_pct:
            issues.append(MaskIssue("warn", "validate.mask_missing",
                                    {"n": miss, "pct": round(pct, 1), "limit": missing_warn_pct}))
    return fixed, issues


def mask_path_for(png_path: str) -> str:
    """`H1WK_P001F.png` → `H1WK_P001F_M.png`."""
    root, _ext = os.path.splitext(png_path)
    return f"{root}_M.png"


def is_mask_name(stem: str) -> bool:
    return stem.endswith("_M")
