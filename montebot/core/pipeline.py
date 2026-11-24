"""
Pipeline de conversão SVG -> G-code 100% nativo em Python utilizando vpype.
Realiza leitura vetorial, tratamento de escala/página, união de linhas (linemerge),
simplificação de micro-segmentos (linesimplify), ordenação de rotas (linesort)
e geração direta de G-code padronizado.
"""

import math
import os
import re
import tempfile
import unicodedata
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from lxml import etree
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import Voronoi
import shapely
from shapely import STRtree, make_valid
from shapely.geometry import LineString, MultiLineString, Polygon
from shapely.prepared import prep
import vpype
from vpype import text_line
import vpype_cli

from montebot.config import A4_HEIGHT_MM, A4_WIDTH_MM, VPYPE_MM_TO_PX, VPYPE_PX_TO_MM

LogCallback = Optional[Callable[[str], None]]


def _normalize_length_str(value: str, default: str = "0.2mm") -> str:
    """Garante que valores de tolerância possuam unidade válida para o vpype (ex: '0.2' -> '0.2mm')."""
    val = str(value).strip().replace(",", ".")
    if not val:
        return default
    if re.match(r"^[-+]?\d*\.?\d+$", val):
        return f"{val}mm"
    return val


def _parse_numeric_mm(value: str, default: float = 0.0) -> float:
    """Extrai o valor numérico em mm de uma string com ou sem unidade."""
    val = str(value).strip().replace(",", ".")
    m = re.match(r"^([-+]?\d*\.?\d+)", val)
    if not m:
        return default
    try:
        return float(m.group(1))
    except ValueError:
        return default


def _format_num(value: float, keep_decimal: bool = False) -> str:
    """Formata números de parâmetros G-code sem zeros desnecessários."""
    if not keep_decimal and float(value).is_integer():
        return str(int(round(value)))
    s = f"{float(value):.4f}".rstrip("0").rstrip(".")
    if keep_decimal and "." not in s:
        s += ".0"
    return s


def _quote_vpype_path(path: str) -> str:
    """Escapa caminho de arquivo para o parser CLI interno do vpype."""
    escaped = os.path.abspath(path).replace("\\", "/")
    return f'"{escaped}"'


# ─────────────────────────────────────────────
#  SUAVIZAÇÃO ANTI-VIBRAÇÃO (JPG -> SVG)
# ─────────────────────────────────────────────


def _point_at_arc_distance(pts: np.ndarray, start_idx: int, target_dist: float, direction: int, closed: bool) -> complex:
    """Caminha ao longo da polilinha a partir de start_idx até atingir target_dist."""
    n = len(pts)
    acc = 0.0
    cur = start_idx
    steps = 0
    max_steps = n if closed else (start_idx if direction < 0 else (n - 1 - start_idx))

    while steps < max_steps:
        nxt = (cur + direction) % n if closed else (cur + direction)
        seg = pts[nxt] - pts[cur]
        seg_len = abs(seg)
        if seg_len > 1e-9:
            if acc + seg_len >= target_dist:
                t = (target_dist - acc) / seg_len
                return pts[cur] + t * seg
            acc += seg_len
        cur = nxt
        steps += 1

    return pts[cur]


def _angle_between_deg(v1: complex, v2: complex) -> float:
    """Retorna o ângulo de deflexão em graus (0..180) entre dois vetores complexos."""
    n1 = abs(v1)
    n2 = abs(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0
    cos_a = max(-1.0, min(1.0, (v1.real * v2.real + v1.imag * v2.imag) / (n1 * n2)))
    return math.degrees(math.acos(cos_a))


def _detect_sharp_corners(pts: np.ndarray, closed: bool, window_px: float) -> List[int]:
    """
    Identifica índices de quinas vivas reais, ignorando micro-degraus de serrilhado JPG.
    Um vértice é considerado quina apenas se o ângulo local (>45°) e o ângulo de corda (>55°) concordarem.
    """
    n = len(pts)
    if n < 3:
        return []

    start = 0 if closed else 1
    end = n if closed else (n - 1)
    candidates: List[Tuple[int, float]] = []

    for i in range(start, end):
        prev_pt = pts[(i - 1) % n]
        next_pt = pts[(i + 1) % n]
        local_ang = _angle_between_deg(pts[i] - prev_pt, next_pt - pts[i])
        if local_ang <= 45.0:
            continue

        back_pt = _point_at_arc_distance(pts, i, window_px, -1, closed)
        fwd_pt = _point_at_arc_distance(pts, i, window_px, 1, closed)
        chord_ang = _angle_between_deg(pts[i] - back_pt, fwd_pt - pts[i])
        if chord_ang > 55.0:
            candidates.append((i, chord_ang))

    # Evita marcar dois vértices colados como quinas separadas
    corners: List[int] = []
    min_sep = max(0.3 * VPYPE_MM_TO_PX, window_px * 0.4)
    for idx, ang in candidates:
        if corners and abs(pts[idx] - pts[corners[-1]]) < min_sep:
            continue
        corners.append(idx)

    if closed and len(corners) >= 2 and abs(pts[corners[0]] - pts[corners[-1]]) < min_sep:
        corners.pop()

    return corners


def _resample_and_gaussian_open(seg: np.ndarray, step_px: float, sigma_pts: float) -> np.ndarray:
    """Reamostra um trecho aberto por comprimento de arco e aplica filtro Gaussiano 1D mantendo pontas fixas."""
    if len(seg) < 3:
        return seg
    diffs = np.diff(seg)
    seg_lens = np.abs(diffs)
    total = float(np.sum(seg_lens))
    if total < 2.0 * step_px:
        return seg

    cum_s = np.concatenate(([0.0], np.cumsum(seg_lens)))
    m = max(4, int(round(total / step_px)) + 1)
    t = np.linspace(0.0, total, m, endpoint=True)
    rx = np.interp(t, cum_s, seg.real)
    ry = np.interp(t, cum_s, seg.imag)

    sx = gaussian_filter1d(rx, sigma=sigma_pts, mode="nearest")
    sy = gaussian_filter1d(ry, sigma=sigma_pts, mode="nearest")
    sx[0], sy[0] = seg[0].real, seg[0].imag
    sx[-1], sy[-1] = seg[-1].real, seg[-1].imag
    return sx + 1j * sy


def _smooth_single_polyline(line: np.ndarray, sigma_px: float, step_px: float) -> np.ndarray:
    """Suaviza uma polilinha individual preservando quinas vivas e fechamento de anéis."""
    if len(line) < 3:
        return line

    # Remove pontos consecutivos idênticos
    keep = [line[0]]
    for pt in line[1:]:
        if abs(pt - keep[-1]) > 1e-6:
            keep.append(pt)
    pts = np.array(keep, dtype=np.complex128)
    if len(pts) < 3:
        return line

    total_len = float(np.sum(np.abs(np.diff(pts))))
    if total_len < 2.0 * sigma_px:
        return line

    is_closed = abs(pts[0] - pts[-1]) <= 0.1 * VPYPE_MM_TO_PX
    sigma_pts = max(0.6, sigma_px / max(1e-6, step_px))
    window_px = max(1.5 * sigma_px, 0.45 * VPYPE_MM_TO_PX)

    if is_closed:
        ring = pts[:-1] if abs(pts[0] - pts[-1]) <= 0.05 * VPYPE_MM_TO_PX else pts
        if len(ring) < 3:
            return line
        corners = _detect_sharp_corners(ring, closed=True, window_px=window_px)
        if not corners:
            ring_closed = np.append(ring, ring[0])
            diffs = np.abs(np.diff(ring_closed))
            loop_len = float(np.sum(diffs))
            if loop_len < 2.0 * sigma_px:
                return line
            cum_s = np.concatenate(([0.0], np.cumsum(diffs)))
            n_samples = max(16, int(round(loop_len / step_px)))
            t = np.linspace(0.0, loop_len, n_samples, endpoint=False)
            rx = np.interp(t, cum_s, ring_closed.real)
            ry = np.interp(t, cum_s, ring_closed.imag)
            sx = gaussian_filter1d(rx, sigma=sigma_pts, mode="wrap")
            sy = gaussian_filter1d(ry, sigma=sigma_pts, mode="wrap")
            out = sx + 1j * sy
            return np.append(out, out[0])

        # Anel fechado com quinas vivas: rotaciona para começar na 1ª quina e suaviza cada trecho
        c0 = corners[0]
        rot_ring = np.concatenate((ring[c0:], ring[:c0], [ring[c0]]))
        rel_corners = [(c - c0) % len(ring) for c in corners] + [len(ring)]
        out_parts: List[np.ndarray] = []
        for k in range(len(rel_corners) - 1):
            sub = rot_ring[rel_corners[k] : rel_corners[k + 1] + 1]
            sm = _resample_and_gaussian_open(sub, step_px, sigma_pts)
            out_parts.append(sm[:-1] if k < len(rel_corners) - 2 else sm)
        return np.concatenate(out_parts)

    corners = _detect_sharp_corners(pts, closed=False, window_px=window_px)
    split_idxs = [0] + corners + [len(pts) - 1]
    out_parts = []
    for k in range(len(split_idxs) - 1):
        sub = pts[split_idxs[k] : split_idxs[k + 1] + 1]
        sm = _resample_and_gaussian_open(sub, step_px, sigma_pts)
        out_parts.append(sm[:-1] if k < len(split_idxs) - 2 else sm)
    return np.concatenate(out_parts)


def smooth_document_paths(
    doc: vpype.Document,
    smooth_mm: float,
    log_callback: LogCallback = None,
) -> vpype.Document:
    """
    Aplica filtro passa-baixa Gaussiano com preservação de quinas em todas as camadas do documento
    para eliminar micro-ondulações ("margens tortas") típicas de vetorização JPG -> SVG.
    """
    if smooth_mm <= 0:
        return doc

    sigma_px = float(smooth_mm) * VPYPE_MM_TO_PX
    step_px = max(0.08 * VPYPE_MM_TO_PX, min(0.20 * VPYPE_MM_TO_PX, sigma_px * 0.5))

    if log_callback:
        log_callback(f"▶ Suavizando traços anti-vibração (raio {smooth_mm:.2f} mm, preservação de quinas)...")

    for layer_id in list(doc.layers.keys()):
        lc = doc.layers[layer_id]
        meta = dict(lc.metadata)
        new_lines = [_smooth_single_polyline(line, sigma_px, step_px) for line in lc]
        doc.pop(layer_id)
        new_lc = vpype.LineCollection(new_lines)
        new_lc.metadata.update(meta)
        doc.add(new_lc, layer_id)

    return doc


# ─────────────────────────────────────────────
#  MODO FILL (PREENCHIMENTO PAR-ÍMPAR + ZIG-ZAG)
# ─────────────────────────────────────────────


def _extract_valid_polygons(geom) -> List[Polygon]:
    """Extrai polígonos simples não-vazios de qualquer geometria Shapely."""
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom] if geom.area > 0 else []
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        out: List[Polygon] = []
        for g in geom.geoms:
            out.extend(_extract_valid_polygons(g))
        return out
    return []


def _extract_linestrings(geom) -> List[LineString]:
    """Extrai todos os LineStrings de comprimento positivo de um resultado de interseção."""
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom] if geom.length > 1e-5 else []
    if geom.geom_type in ("MultiLineString", "GeometryCollection"):
        out: List[LineString] = []
        for g in geom.geoms:
            out.extend(_extract_linestrings(g))
        return out
    return []


def _hatch_polygon(poly: Polygon, spacing_px: float) -> List[np.ndarray]:
    """
    Gera hachura horizontal com passo `spacing_px` conectando segmentos adjacentes em zig-zag contínuo
    sem cruzar furos nem extrapolar as bordas do polígono.
    """
    if poly.is_empty or poly.area <= 1e-4:
        return []

    inset_poly = poly.buffer(-spacing_px * 0.35)
    if inset_poly.is_empty:
        inset_poly = poly

    minx, miny, maxx, maxy = inset_poly.bounds
    height = maxy - miny
    if height <= 1e-6 or (maxx - minx) <= 1e-6:
        return []

    if height <= spacing_px:
        ys = np.array([0.5 * (miny + maxy)], dtype=float)
    else:
        ys = np.arange(miny + 0.5 * spacing_px, maxy, spacing_px, dtype=float)
        if len(ys) == 0:
            ys = np.array([0.5 * (miny + maxy)], dtype=float)

    scanlines = MultiLineString([[(minx - 2.0, float(y)), (maxx + 2.0, float(y))] for y in ys])
    clipped = inset_poly.intersection(scanlines)
    segs = _extract_linestrings(clipped)
    if not segs:
        return []

    # Agrupa segmentos por índice de linha de varredura
    rows: Dict[int, List[Tuple[Tuple[float, float], Tuple[float, float]]]] = {}
    y0 = float(ys[0])
    for seg in segs:
        coords = list(seg.coords)
        if len(coords) < 2:
            continue
        p_a = (float(coords[0][0]), float(coords[0][1]))
        p_b = (float(coords[-1][0]), float(coords[-1][1]))
        if p_a[0] > p_b[0]:
            p_a, p_b = p_b, p_a
        row_idx = int(round((p_a[1] - y0) / spacing_px)) if len(ys) > 1 else 0
        rows.setdefault(row_idx, []).append((p_a, p_b))

    for r_idx in rows:
        rows[r_idx].sort(key=lambda s: s[0][0])

    conn_region = prep(poly.buffer(spacing_px * 0.25))
    max_link_dist = 2.5 * spacing_px

    hatch_lines: List[np.ndarray] = []
    sorted_row_keys = sorted(rows.keys())

    while rows:
        # Encontra a primeira linha ainda com segmentos pendentes
        first_row = next((rk for rk in sorted_row_keys if rk in rows), None)
        if first_row is None:
            break

        p_a, p_b = rows[first_row].pop(0)
        if not rows[first_row]:
            del rows[first_row]

        chain: List[Tuple[float, float]] = [p_a, p_b]
        cur_row = first_row

        while True:
            nxt_row = cur_row + 1
            cands = rows.get(nxt_row)
            if not cands:
                break

            cur_end = chain[-1]
            best_idx = -1
            best_dist = float("inf")
            best_flip = False

            for idx, (s_left, s_right) in enumerate(cands):
                d_left = math.hypot(cur_end[0] - s_left[0], cur_end[1] - s_left[1])
                if d_left < best_dist:
                    best_dist = d_left
                    best_idx = idx
                    best_flip = False

                d_right = math.hypot(cur_end[0] - s_right[0], cur_end[1] - s_right[1])
                if d_right < best_dist:
                    best_dist = d_right
                    best_idx = idx
                    best_flip = True

            if best_idx < 0 or best_dist > max_link_dist:
                break

            s_left, s_right = cands[best_idx]
            nxt_start, nxt_end = (s_right, s_left) if best_flip else (s_left, s_right)
            if not conn_region.contains(LineString([cur_end, nxt_start])):
                break

            cands.pop(best_idx)
            if not cands:
                del rows[nxt_row]
            chain.append(nxt_start)
            chain.append(nxt_end)
            cur_row = nxt_row

        hatch_lines.append(np.array([complex(x, y) for x, y in chain], dtype=np.complex128))

    return hatch_lines


def _ring_to_complex_array(ring) -> np.ndarray:
    """Converte um LinearRing do Shapely em array numpy complexo fechado."""
    coords = list(ring.coords)
    if len(coords) < 2:
        return np.empty(0, dtype=np.complex128)
    return np.array([complex(float(x), float(y)) for x, y in coords], dtype=np.complex128)


def _contour_fill_polygon(
    poly: Polygon,
    spacing_px: float,
    max_shells: Optional[int] = None,
) -> Tuple[List[np.ndarray], List[Polygon]]:
    """
    Gera preenchimento que acompanha internamente a curvatura e o eixo do desenho:
      1. Cria anéis concêntricos internos (offset / buffer negativo) com passo `spacing_px`.
      2. Quando a faixa restante entre o último anel e o eixo central fica fina (<= 1.35 * spacing_px),
         extrai a Linha Central (Centerline de Voronoi) para preencher o núcleo curvo em um único traço
         contínuo em vez de dezenas de micro-ziguezagues horizontais.
    Se `max_shells` for definido (usado no modo híbrido), para após `max_shells` camadas e retorna
    a lista de sub-polígonos de núcleo amplo restantes para preenchimento horizontal.
    """
    if poly.is_empty or poly.area <= 1e-4:
        return [], []

    fill_lines: List[np.ndarray] = []
    remaining_cores: List[Polygon] = []

    current_polys = _extract_valid_polygons(poly)
    shell_count = 0

    while current_polys:
        if max_shells is not None and shell_count >= max_shells:
            remaining_cores.extend(current_polys)
            break

        next_polys: List[Polygon] = []
        for sub_p in current_polys:
            if sub_p.is_empty or sub_p.area <= 1e-4:
                continue

            # No 1º passo recua 0.75 * spacing_px da borda (já desenhada pelo contorno); nos demais, 1.0 * spacing_px
            step_inset = spacing_px * 0.75 if shell_count == 0 else spacing_px
            eroded = sub_p.buffer(-step_inset, join_style=2)
            eroded_parts = [ep for ep in _extract_valid_polygons(eroded) if ep.area > 1e-4]

            if not eroded_parts:
                # A região é uma linha/faixa fina ou curva estreita: preenche seguindo o eixo medial (Centerline)
                center_strokes = _polygon_to_centerlines(sub_p, spacing_px)
                fill_lines.extend(center_strokes)
                continue

            for ep in eroded_parts:
                ext_arr = _ring_to_complex_array(ep.exterior)
                if len(ext_arr) >= 2:
                    fill_lines.append(ext_arr)
                for hole in ep.interiors:
                    hole_arr = _ring_to_complex_array(hole)
                    if len(hole_arr) >= 2:
                        fill_lines.append(hole_arr)

                # Verifica se o núcleo interno restante já é fino o suficiente para fechar com 1 traço central
                deeper = ep.buffer(-spacing_px * 0.85, join_style=2)
                if deeper.is_empty or deeper.area <= 1e-4:
                    deeper_half = ep.buffer(-spacing_px * 0.45, join_style=2)
                    if not deeper_half.is_empty and deeper_half.area > 1e-4:
                        for dh in _extract_valid_polygons(deeper_half):
                            fill_lines.extend(_polygon_to_centerlines(dh, spacing_px))
                else:
                    next_polys.append(ep)

        current_polys = next_polys
        shell_count += 1

    return fill_lines, remaining_cores


def _hybrid_fill_polygon(poly: Polygon, spacing_px: float) -> List[np.ndarray]:
    """
    Esquema Híbrido inteligente de preenchimento:
      - Classifica cada sub-polígono pela espessura local máxima (via erosão de teste e razão Área/Perímetro).
      - Traços grossos, eixos curvos e áreas estreitas/pequenas seguem a linha do desenho internamente
        (anéis concêntricos + eixo central contínuo).
      - Áreas grandes e largas recebem anéis de contorno internos (paredes suaves que acompanham a borda curva)
        e preenchimento horizontal rápido em zig-zag no miolo aberto.
    """
    if poly.is_empty or poly.area <= 1e-4:
        return []

    # Limiar de espessura: se uma região desaparece após recuar ~3.2x a largura da caneta de cada lado
    # (espessura total <= ~6.4x a caneta), ela é um traço grosso/curvo ou área pequena -> 100% Seguindo o Eixo
    thin_probe_dist = spacing_px * 3.2
    core_test = poly.buffer(-thin_probe_dist, join_style=2)

    if core_test.is_empty or core_test.area <= (poly.area * 0.08):
        lines, _ = _contour_fill_polygon(poly, spacing_px, max_shells=None)
        return lines

    # Região ampla ou mista (ex: área grande com braços curvos estreitos):
    # Faz 2 anéis concêntricos que preenchem os braços curvos e suavizam a borda, e aplica horizontal no miolo amplo
    contour_lines, wide_cores = _contour_fill_polygon(poly, spacing_px, max_shells=2)
    fill_lines = list(contour_lines)

    for core_poly in wide_cores:
        # Se um sub-núcleo que sobrou for estreito, fecha seguindo o eixo; se for amplo, faz hachura horizontal
        sub_test = core_poly.buffer(-spacing_px * 1.8, join_style=2)
        if sub_test.is_empty or sub_test.area <= 1e-4:
            sub_contour, _ = _contour_fill_polygon(core_poly, spacing_px, max_shells=None)
            fill_lines.extend(sub_contour)
        else:
            # Expande levemente o núcleo para que a hachura horizontal encoste perfeitamente no último anel concêntrico
            hatch_target = core_poly.buffer(spacing_px * 0.30, join_style=2)
            for hp in _extract_valid_polygons(hatch_target):
                fill_lines.extend(_hatch_polygon(hp, spacing_px))

    return fill_lines


def _fill_single_polygon(poly: Polygon, spacing_px: float, fill_pattern: str = "hybrid") -> List[np.ndarray]:
    """Despacha o preenchimento de um polígono conforme o padrão escolhido ('horizontal', 'contour', 'hybrid')."""
    pat = str(fill_pattern).strip().lower()
    if pat == "contour":
        lines, _ = _contour_fill_polygon(poly, spacing_px, max_shells=None)
        return lines
    if pat == "hybrid":
        return _hybrid_fill_polygon(poly, spacing_px)
    return _hatch_polygon(poly, spacing_px)


def _sort_lines_from_pos(
    lines: List[np.ndarray],
    start_pos: complex,
) -> Tuple[List[np.ndarray], complex]:
    """Ordena polilinhas por vizinho mais próximo (permitindo inversão de sentido) a partir de start_pos."""
    if not lines:
        return [], start_pos

    remaining = list(lines)
    ordered: List[np.ndarray] = []
    cur = start_pos

    while remaining:
        best_idx = 0
        best_dist = float("inf")
        best_rev = False

        for idx, ln in enumerate(remaining):
            d_fwd = abs(ln[0] - cur)
            if d_fwd < best_dist:
                best_dist = d_fwd
                best_idx = idx
                best_rev = False
            d_rev = abs(ln[-1] - cur)
            if d_rev < best_dist:
                best_dist = d_rev
                best_idx = idx
                best_rev = True

        chosen = remaining.pop(best_idx)
        if best_rev:
            chosen = chosen[::-1]
        ordered.append(chosen)
        cur = chosen[-1]

    return ordered, cur


def _build_layer_regions(
    lc: vpype.LineCollection,
    close_tol_px: float,
) -> Tuple[List[Tuple[Polygon, List[np.ndarray], List[np.ndarray]]], List[np.ndarray]]:
    """
    Separa caminhos fechados e abertos da camada, constrói polígonos com furos via regra Par-Ímpar
    (Even-Odd) e retorna:
      - regions: lista de (poligono_com_furos, linhas_contorno_da_regiao, [])
      - open_paths: linhas abertas ou degeneradas que não formam regiões fechadas
    """
    min_area_px2 = 0.01 * (VPYPE_MM_TO_PX ** 2)
    rings: List[Tuple[Polygon, np.ndarray]] = []
    open_paths: List[np.ndarray] = []

    for line in lc:
        if len(line) >= 4 and abs(line[0] - line[-1]) <= close_tol_px:
            coords = [(float(pt.real), float(pt.imag)) for pt in line]
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            try:
                poly = Polygon(coords)
                if not poly.is_valid:
                    poly = make_valid(poly)
                parts = _extract_valid_polygons(poly)
            except Exception:
                parts = []

            valid_parts = [p for p in parts if p.area > min_area_px2]
            if valid_parts:
                # Associa a linha de contorno original ao maior sub-polígono gerado
                valid_parts.sort(key=lambda p: p.area, reverse=True)
                for p_idx, vp in enumerate(valid_parts):
                    rings.append((vp, line if p_idx == 0 else np.empty(0, dtype=np.complex128)))
                continue

        open_paths.append(line)

    if not rings:
        return [], open_paths

    rings.sort(key=lambda item: item[0].area, reverse=True)
    tree = STRtree([r_poly for r_poly, _ in rings])
    parents = [-1] * len(rings)
    depths = [0] * len(rings)

    for i, (poly_i, _) in enumerate(rings):
        rep_pt = poly_i.representative_point()
        cands = [int(j) for j in tree.query(rep_pt, predicate="within") if int(j) < i]
        if cands:
            parent_idx = max(cands)
            parents[i] = parent_idx
            depths[i] = depths[parent_idx] + 1

    regions: List[Tuple[Polygon, List[np.ndarray], List[np.ndarray]]] = []
    for i, (shell_poly, shell_line) in enumerate(rings):
        if depths[i] % 2 != 0:
            continue

        hole_indices = [j for j in range(i + 1, len(rings)) if parents[j] == i]
        holes_coords = [list(rings[h][0].exterior.coords) for h in hole_indices]
        try:
            region_poly = Polygon(shell_poly.exterior.coords, holes_coords)
            if not region_poly.is_valid:
                region_poly = make_valid(region_poly)
        except Exception:
            region_poly = shell_poly

        outline_lines: List[np.ndarray] = []
        if len(shell_line) >= 2:
            outline_lines.append(shell_line)
        for h in hole_indices:
            h_line = rings[h][1]
            if len(h_line) >= 2:
                outline_lines.append(h_line)

        regions.append((region_poly, outline_lines, []))

    return regions, open_paths


def apply_fill_to_document(
    doc: vpype.Document,
    pen_width_mm: float,
    fill_mode: str = "after_outline",
    fill_pattern: str = "hybrid",
    log_callback: LogCallback = None,
) -> vpype.Document:
    """
    Gera hachuras de preenchimento (Fill) com espaçamento igual à largura efetiva da caneta
    respeitando furos pela regra Par-Ímpar e organizando nos modos:
      - 'after_outline': todos os contornos ordenados primeiro e todo o fill ao final
      - 'while_drawing': contorno + preenchimento imediato de cada região por vez
    E nos esquemas de padrão (`fill_pattern`):
      - 'horizontal': hachura horizontal tradicional em zig-zag
      - 'contour': segue o eixo/curvatura do desenho (anéis concêntricos + linha central)
      - 'hybrid': áreas grandes em horizontal + traços curvos/pequenos seguindo a linha do desenho
    """
    spacing_mm = max(0.05, float(pen_width_mm))
    spacing_px = spacing_mm * VPYPE_MM_TO_PX
    close_tol_px = 0.25 * VPYPE_MM_TO_PX
    is_while = str(fill_mode).strip().lower() == "while_drawing"
    pat_norm = str(fill_pattern or "hybrid").strip().lower()

    if not is_while:
        doc = vpype_cli.execute("linesort", document=doc)

    bounds = doc.bounds()
    origin_y_px = bounds[3] if bounds else (A4_HEIGHT_MM * VPYPE_MM_TO_PX)
    start_pos = complex(0.0, origin_y_px)

    total_fill_lines = 0
    total_regions = 0

    if log_callback:
        mode_lbl = "enquanto desenha" if is_while else "ao completar desenho"
        pat_labels = {
            "horizontal": "Horizontal (Zig-Zag)",
            "contour": "Seguir Eixo / Curvas",
            "hybrid": "Híbrido (Grandes: Horiz. | Curvas/Peq.: Eixo)",
        }
        pat_lbl = pat_labels.get(pat_norm, pat_labels["hybrid"])
        log_callback(
            f"▶ Gerando preenchimento [{pat_lbl}] (caneta {spacing_mm:.2f} mm, ordem: {mode_lbl})..."
        )

    for layer_id in list(doc.layers.keys()):
        lc = doc.layers[layer_id]
        meta = dict(lc.metadata)
        regions, open_paths = _build_layer_regions(lc, close_tol_px=close_tol_px)
        total_regions += len(regions)

        if not regions:
            if is_while and open_paths:
                ordered_open, _ = _sort_lines_from_pos(open_paths, start_pos)
                doc.pop(layer_id)
                new_lc = vpype.LineCollection(ordered_open)
                new_lc.metadata.update(meta)
                doc.add(new_lc, layer_id)
            continue

        # Gera as rotas de preenchimento para cada região
        populated_regions: List[Tuple[List[np.ndarray], List[np.ndarray]]] = []
        for region_poly, outlines, _ in regions:
            fills: List[np.ndarray] = []
            for sub_poly in _extract_valid_polygons(region_poly):
                fills.extend(_fill_single_polygon(sub_poly, spacing_px, fill_pattern=pat_norm))
            total_fill_lines += len(fills)
            populated_regions.append((outlines, fills))

        if not is_while:
            # Contornos já foram ordenados pelo linesort; gera e ordena o bloco de fill separado
            all_fills = [fl for _out, fills in populated_regions for fl in fills]
            if all_fills:
                fill_doc = vpype.Document()
                fill_doc.add(vpype.LineCollection(all_fills), 1)
                fill_doc = vpype_cli.execute("linesort", document=fill_doc)
                sorted_fills = list(fill_doc.layers[1])
            else:
                sorted_fills = []

            combined = list(lc) + sorted_fills
            doc.pop(layer_id)
            new_lc = vpype.LineCollection(combined)
            new_lc.metadata.update(meta)
            doc.add(new_lc, layer_id)
        else:
            # Ordena regiões espacialmente por proximidade e emite Contorno -> Fill para cada uma
            cur_pos = start_pos
            pending = list(populated_regions)
            ordered_lines: List[np.ndarray] = []

            while pending:
                best_idx = 0
                best_dist = float("inf")
                for idx, (outlines, fills) in enumerate(pending):
                    probe = outlines if outlines else fills
                    for ln in probe:
                        d = min(abs(ln[0] - cur_pos), abs(ln[-1] - cur_pos))
                        if d < best_dist:
                            best_dist = d
                            best_idx = idx

                outlines, fills = pending.pop(best_idx)
                ord_out, cur_pos = _sort_lines_from_pos(outlines, cur_pos)
                ordered_lines.extend(ord_out)
                ord_fill, cur_pos = _sort_lines_from_pos(fills, cur_pos)
                ordered_lines.extend(ord_fill)

            if open_paths:
                ord_open, _ = _sort_lines_from_pos(open_paths, cur_pos)
                ordered_lines.extend(ord_open)

            doc.pop(layer_id)
            new_lc = vpype.LineCollection(ordered_lines)
            new_lc.metadata.update(meta)
            doc.add(new_lc, layer_id)

    if log_callback:
        log_callback(f"✓ Preenchimento gerado: {total_regions} regiões, {total_fill_lines} traços de hachura")

    return doc


# ─────────────────────────────────────────────
#  MODO TEXTO (SUPORTE A <text> SVG, LINHA ÚNICA E ORDEM DE ESCRITA)
# ─────────────────────────────────────────────


_MATH_CHAR_REPLACEMENTS: Dict[str, str] = {
    "×": "x",
    "÷": "/",
    "−": "-",
    "–": "-",
    "—": "-",
    "·": ".",
    "•": ".",
    "±": "+-",
    "∓": "-+",
    "≈": "~=",
    "≠": "!=",
    "≤": "<=",
    "≥": ">=",
    "→": "->",
    "⇒": "=>",
    "←": "<-",
    "↔": "<->",
    "Δ": "D",
    "∆": "D",
    "π": "pi",
    "√": "sqrt",
    "∞": "oo",
    "²": "^2",
    "³": "^3",
    "¹": "^1",
}


def render_single_stroke_line(
    text: str,
    font_name: str = "futural",
    size_px: float = 18.0,
    monospace: bool = False,
) -> vpype.LineCollection:
    """
    Renderiza uma linha de texto em fonte single-stroke (Hershey) com suporte completo
    a acentos, caracteres do Português (á, à, ã, â, é, ê, í, ó, õ, ô, ú, ü, ç, º, ª, °),
    símbolos matemáticos e modo monoespaçado opcional (crucial para contas armadas e tabelas).
    """
    valid_font = font_name if font_name in getattr(vpype, "FONT_NAMES", ()) else "futural"
    expanded_text: List[str] = []
    for ch in text:
        if ch in ("\r", "\n"):
            continue
        if ch == "\t":
            expanded_text.append("    ")
            continue
        if ch in _MATH_CHAR_REPLACEMENTS:
            expanded_text.append(_MATH_CHAR_REPLACEMENTS[ch])
        else:
            expanded_text.append(ch)

    norm_text = "".join(expanded_text)
    clean_chars: List[str] = []
    diacritics: List[Tuple[int, str, str]] = []

    for ch in norm_text:
        decomp = unicodedata.normalize("NFD", ch)
        base = decomp[0]
        marks = decomp[1:]
        if ord(base) > 127:
            if ch in ("“", "”", "«", "»"):
                base = '"'
            elif ch in ("‘", "’"):
                base = "'"
            elif ch in ("º", "°"):
                base = "o"
                marks = "\u02da"
            elif ch == "ª":
                base = "a"
                marks = "\u02da"
            else:
                base = " "
        pos = len(clean_chars)
        clean_chars.append(base)
        if marks:
            diacritics.append((pos, marks, base))

    base_str = "".join(clean_chars)
    if not base_str.strip():
        return vpype.LineCollection()

    lc_o = text_line("o", font_name=valid_font, size=size_px)
    b_o = lc_o.bounds() or (0.0, -size_px * 0.5, size_px * 0.4, 0.0)
    xh = max(1e-3, b_o[3] - b_o[1])

    def _append_marks_for_char(
        target_lines: List[np.ndarray],
        char_strokes: List[np.ndarray],
        marks_str: str,
        base_ch: str,
        fallback_x: float,
    ) -> None:
        if char_strokes:
            c_minx = min(float(np.min(s.real)) for s in char_strokes)
            c_maxx = max(float(np.max(s.real)) for s in char_strokes)
            c_miny = min(float(np.min(s.imag)) for s in char_strokes)
            c_maxy = max(float(np.max(s.imag)) for s in char_strokes)
        else:
            c_minx, c_miny, c_maxx, c_maxy = fallback_x, b_o[1], fallback_x + size_px * 0.4, b_o[3]

        cx = 0.5 * (c_minx + c_maxx)
        w = max(c_maxx - c_minx, size_px * 0.22)

        # Em 'í' minúsculo, remove o pingo original para dar lugar ao acento agudo
        if base_ch == "i" and len(char_strokes) >= 2:
            for idx_ln in range(len(target_lines) - 1, -1, -1):
                ln = target_lines[idx_ln]
                lx0, lx1 = float(np.min(ln.real)), float(np.max(ln.real))
                ly0, ly1 = float(np.min(ln.imag)), float(np.max(ln.imag))
                if (
                    abs(0.5 * (lx0 + lx1) - cx) < size_px * 0.2
                    and (ly1 - ly0) < xh * 0.35
                    and ly1 < (c_maxy - xh * 0.4)
                ):
                    target_lines.pop(idx_ln)
                    stem_ys = [
                        float(np.min(s.imag))
                        for s in char_strokes
                        if float(np.max(s.imag)) > c_maxy - xh * 0.3
                    ]
                    if stem_ys:
                        c_miny = min(stem_ys)
                    break

        top_y = c_miny - size_px * 0.08
        acc_h = size_px * 0.16
        acc_w = max(w * 0.35, size_px * 0.12)

        for mk in marks_str:
            if mk == "\u0301":  # Agudo (´)
                target_lines.append(
                    np.array(
                        [complex(cx - acc_w * 0.35, top_y), complex(cx + acc_w * 0.55, top_y - acc_h)],
                        dtype=np.complex128,
                    )
                )
            elif mk == "\u0300":  # Grave (`)
                target_lines.append(
                    np.array(
                        [complex(cx + acc_w * 0.35, top_y), complex(cx - acc_w * 0.55, top_y - acc_h)],
                        dtype=np.complex128,
                    )
                )
            elif mk == "\u0302":  # Circunflexo (^)
                target_lines.append(
                    np.array(
                        [
                            complex(cx - acc_w * 0.65, top_y),
                            complex(cx, top_y - acc_h),
                            complex(cx + acc_w * 0.65, top_y),
                        ],
                        dtype=np.complex128,
                    )
                )
            elif mk == "\u0303":  # Til (~)
                ts = np.linspace(-1.0, 1.0, 9)
                xs = cx + ts * (acc_w * 0.75)
                ys = (top_y - 0.5 * acc_h) - np.sin(ts * np.pi) * (acc_h * 0.38)
                target_lines.append(np.array([complex(x, y) for x, y in zip(xs, ys)], dtype=np.complex128))
            elif mk == "\u0308":  # Trema (¨)
                d = acc_w * 0.35
                dy = top_y - acc_h * 0.4
                target_lines.append(
                    np.array([complex(cx - d, dy), complex(cx - d, dy - acc_h * 0.25)], dtype=np.complex128)
                )
                target_lines.append(
                    np.array([complex(cx + d, dy), complex(cx + d, dy - acc_h * 0.25)], dtype=np.complex128)
                )
            elif mk == "\u02da":  # Indicador ordinal / grau (º, ª, °)
                sub_line = np.array(
                    [complex(c_minx, c_maxy + size_px * 0.08), complex(c_maxx, c_maxy + size_px * 0.08)],
                    dtype=np.complex128,
                )
                target_lines.append(sub_line)
            elif mk == "\u0327":  # Cedilha (¸)
                by = c_maxy
                ch_h = size_px * 0.16
                target_lines.append(
                    np.array(
                        [
                            complex(cx, by),
                            complex(cx - acc_w * 0.15, by + ch_h * 0.45),
                            complex(cx + acc_w * 0.30, by + ch_h * 0.75),
                            complex(cx - acc_w * 0.25, by + ch_h),
                        ],
                        dtype=np.complex128,
                    )
                )

    if monospace:
        # Renderiza caractere por caractere em células fixas (ideal para Courier/monospace e contas armadas)
        cell_w = size_px * 0.54
        diac_map = {pos: (marks, base_ch) for pos, marks, base_ch in diacritics}
        mono_lines: List[np.ndarray] = []
        for idx, ch in enumerate(clean_chars):
            if ch == " ":
                continue
            lc_ch = text_line(ch, font_name=valid_font, size=size_px)
            b_ch = lc_ch.bounds()
            if not b_ch:
                continue
            ch_w = b_ch[2] - b_ch[0]
            dx = idx * cell_w + 0.5 * (cell_w - ch_w) - b_ch[0]
            shifted_strokes: List[np.ndarray] = []
            for ln in lc_ch:
                arr = np.array(ln, dtype=np.complex128) + complex(dx, 0.0)
                mono_lines.append(arr)
                shifted_strokes.append(arr)
            if idx in diac_map:
                mk_str, b_ch_str = diac_map[idx]
                _append_marks_for_char(mono_lines, shifted_strokes, mk_str, b_ch_str, idx * cell_w)
        return vpype.LineCollection(mono_lines)

    lc = text_line(base_str, font_name=valid_font, size=size_px)
    if not diacritics:
        return lc

    lines_out = [np.array(ln, dtype=np.complex128) for ln in lc]

    for pos, marks, base_ch in diacritics:
        prefix_before = base_str[:pos]
        prefix_with = base_str[: pos + 1]
        lc_with = text_line(prefix_with, font_name=valid_font, size=size_px)
        b_with = lc_with.bounds()
        if not b_with:
            continue

        lc_before = (
            text_line(prefix_before, font_name=valid_font, size=size_px)
            if prefix_before.strip()
            else vpype.LineCollection()
        )
        n_before = len(lc_before)
        char_strokes = [np.array(lc_with[k], dtype=np.complex128) for k in range(n_before, len(lc_with))]
        _append_marks_for_char(lines_out, char_strokes, marks, base_ch, b_with[2] - size_px * 0.4)

    return vpype.LineCollection(lines_out)


def _parse_css_style(style_str: str) -> Dict[str, str]:
    """Extrai pares chave:valor de um atributo style CSS inline."""
    out: Dict[str, str] = {}
    if not style_str:
        return out
    for part in style_str.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _parse_svg_stylesheet_rules(root) -> Dict[str, Dict[str, str]]:
    """
    Extrai regras CSS de blocos <style> do SVG (ex: `.titulo { font-size: 6px; }`, `text { ... }`)
    para que classes CSS aplicadas em <text>, <line>, <rect>, <path> sejam respeitadas.
    """
    rules: Dict[str, Dict[str, str]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str) or el.tag.split("}")[-1] != "style":
            continue
        css_text = "".join(el.itertext())
        if not css_text:
            continue
        css_clean = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)
        for m in re.finditer(r"([^{]+)\{([^}]*)\}", css_clean):
            selectors_raw = m.group(1).strip()
            props = _parse_css_style(m.group(2))
            if not props:
                continue
            for sel in selectors_raw.split(","):
                s_key = sel.strip()
                if s_key:
                    rules.setdefault(s_key, {}).update(props)
    return rules


def _resolve_element_style(el, css_rules: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    """Combina regras de <style> (por tag, classe e id) com atributos XML e style inline."""
    resolved: Dict[str, str] = {}
    if not isinstance(el.tag, str):
        return resolved

    local_tag = el.tag.split("}")[-1]
    if local_tag in css_rules:
        resolved.update(css_rules[local_tag])

    class_attr = el.get("class", "")
    if class_attr:
        for cls_name in class_attr.split():
            for key in (f".{cls_name}", f"{local_tag}.{cls_name}"):
                if key in css_rules:
                    resolved.update(css_rules[key])

    el_id = el.get("id", "")
    if el_id and f"#{el_id}" in css_rules:
        resolved.update(css_rules[f"#{el_id}"])

    for attr_name in ("font-size", "font-family", "font-weight", "text-anchor", "fill", "stroke", "stroke-width"):
        val = el.get(attr_name)
        if val is not None:
            resolved[attr_name] = val.strip()

    inline_style = _parse_css_style(el.get("style", ""))
    resolved.update(inline_style)
    return resolved


def _is_monospace_font(font_family: str) -> bool:
    """Detecta se a família de fonte solicitada no SVG é monoespaçada (ex: Courier New, Consolas, monospace)."""
    ff = (font_family or "").lower()
    return any(k in ff for k in ("monospace", "courier", "consolas", "menlo", "monaco", "fira code", "jetbrains"))


def _parse_svg_number(val: Optional[str], default: float = 0.0) -> float:
    """Converte um valor numérico de atributo SVG (ex: '16px', '10.5pt', '20') em float."""
    if not val:
        return default
    s = str(val).strip().lower().replace(",", ".")
    m = re.match(r"^([-+]?\d*\.?\d+)([a-z%]*)", s)
    if not m:
        return default
    num = float(m.group(1))
    unit = m.group(2)
    if unit == "pt":
        return num * (96.0 / 72.0)
    if unit == "mm":
        return num * VPYPE_MM_TO_PX
    if unit == "cm":
        return num * 10.0 * VPYPE_MM_TO_PX
    if unit == "in":
        return num * 96.0
    return num


def _wrap_text_to_width(text: str, font_name: str, size_val: float, max_width: float) -> List[str]:
    """Quebra parágrafos de texto em linhas para caber dentro de `max_width` (shape-inside)."""
    if max_width <= 0:
        return [text]
    words = text.split(" ")
    lines: List[str] = []
    cur_words: List[str] = []

    for word in words:
        cand = " ".join(cur_words + [word]) if cur_words else word
        lc_cand = render_single_stroke_line(cand, font_name=font_name, size_px=size_val)
        b = lc_cand.bounds()
        w = (b[2] - b[0]) if b else 0.0
        if cur_words and w > max_width:
            lines.append(" ".join(cur_words))
            cur_words = [word]
        else:
            cur_words.append(word)

    if cur_words:
        lines.append(" ".join(cur_words))
    return lines if lines else [""]


def _lc_to_svg_path_d(lc: vpype.LineCollection, offset_x: float, offset_y: float) -> str:
    """Converte um vpype.LineCollection deslocado por (offset_x, offset_y) em string SVG `d`."""
    parts: List[str] = []
    for ln in lc:
        if len(ln) < 2:
            continue
        p0 = ln[0]
        parts.append(f"M {p0.real + offset_x:.4f},{p0.imag + offset_y:.4f}")
        for pt in ln[1:]:
            parts.append(f"L {pt.real + offset_x:.4f},{pt.imag + offset_y:.4f}")
    return " ".join(parts)


def preprocess_svg_text_elements(
    input_svg: str,
    font_name: str = "futural",
    log_callback: LogCallback = None,
) -> Tuple[str, Optional[str], int]:
    """
    Inspeciona um arquivo SVG e converte automaticamente todas as tags <text>, <tspan> e <flowRoot>
    (incluindo classes CSS de blocos <style>, fontes monoespaçadas, espaços de indentação para contas
    armadas e caixas de texto `shape-inside:url(#rect)` do Inkscape) em elementos vetoriais <path>.
    Retorna (caminho_svg_para_uso, caminho_temporario_para_limpar, qtd_textos_convertidos).
    """
    try:
        parser = etree.XMLParser(remove_blank_text=False, recover=True)
        tree = etree.parse(input_svg, parser)
        root = tree.getroot()
    except Exception:
        return input_svg, None, 0

    css_rules = _parse_svg_stylesheet_rules(root)

    # Propaga estilos CSS de classes (<style>) para elementos geométricos (<rect>, <line>, <polyline>, <polygon>, <circle>, <ellipse>, <path>)
    # para que renderizadores como svglib não preencham retângulos com fill:none definido apenas via classe CSS
    geom_tags = {"rect", "line", "polyline", "polygon", "circle", "ellipse", "path"}
    styles_inlined = 0
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.split("}")[-1]
        if local in geom_tags and (el.get("class") or local in css_rules):
            resolved_g = _resolve_element_style(el, css_rules)
            if resolved_g:
                inline_parts = [f"{k}:{v}" for k, v in resolved_g.items()]
                el.set("style", ";".join(inline_parts))
                for prop_k in ("fill", "stroke", "stroke-width"):
                    if prop_k in resolved_g and el.get(prop_k) is None:
                        el.set(prop_k, resolved_g[prop_k])
                styles_inlined += 1

    # Mapeia retângulos por ID para suportar `shape-inside:url(#id)` do Inkscape
    rects_by_id: Dict[str, Tuple[float, float, float, float]] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.split("}")[-1]
        el_id = el.get("id")
        if local == "rect" and el_id:
            rx = _parse_svg_number(el.get("x"), 0.0)
            ry = _parse_svg_number(el.get("y"), 0.0)
            rw = _parse_svg_number(el.get("width"), 0.0)
            rh = _parse_svg_number(el.get("height"), 0.0)
            rects_by_id[el_id] = (rx, ry, rw, rh)

    text_elements = [
        el
        for el in root.iter()
        if isinstance(el.tag, str) and el.tag.split("}")[-1] in ("text", "flowRoot")
    ]
    if not text_elements and styles_inlined == 0:
        return input_svg, None, 0

    converted_count = 0
    svg_ns = "http://www.w3.org/2000/svg"

    for text_el in text_elements:
        raw_content = "".join(text_el.itertext())
        if not raw_content or not raw_content.strip():
            continue

        style_map = _resolve_element_style(text_el, css_rules)
        font_size_raw = style_map.get("font-size") or "12px"
        font_size = max(1.0, _parse_svg_number(font_size_raw, 12.0))
        is_mono = _is_monospace_font(style_map.get("font-family", ""))

        # Calibra o tamanho do vpype.text_line para que a altura de maiúscula ('A') seja ~0.72 * font_size
        vpype_size = font_size * (0.72 / 0.65625)
        baseline_shift = 0.28125 * vpype_size

        line_height_raw = style_map.get("line-height", "1.25")
        if line_height_raw.endswith("%"):
            line_spacing = font_size * (_parse_svg_number(line_height_raw, 125.0) / 100.0)
        elif any(line_height_raw.endswith(u) for u in ("px", "pt", "mm", "cm", "in")):
            line_spacing = _parse_svg_number(line_height_raw, font_size * 1.25)
        else:
            mult = _parse_svg_number(line_height_raw, 1.25)
            line_spacing = font_size * (mult if mult > 0 else 1.25)

        anchor = (
            style_map.get("text-anchor")
            or style_map.get("text-align")
            or "start"
        ).strip().lower()

        # Verifica se é caixa de texto Inkscape (`shape-inside:url(#rect...)` ou `<flowRoot>`)
        shape_inside = style_map.get("shape-inside", "")
        box_rect: Optional[Tuple[float, float, float, float]] = None
        m_url = re.search(r"url\(\s*#([^)\s]+)\s*\)", shape_inside)
        if m_url and m_url.group(1) in rects_by_id:
            box_rect = rects_by_id[m_url.group(1)]
        elif text_el.tag.split("}")[-1] == "flowRoot":
            for sub in text_el.iter():
                if isinstance(sub.tag, str) and sub.tag.split("}")[-1] == "rect":
                    box_rect = (
                        _parse_svg_number(sub.get("x"), 0.0),
                        _parse_svg_number(sub.get("y"), 0.0),
                        _parse_svg_number(sub.get("width"), 0.0),
                        _parse_svg_number(sub.get("height"), 0.0),
                    )
                    break

        path_d_segments: List[str] = []

        if box_rect is not None:
            bx, by, bw, _bh = box_rect
            paragraphs: List[str] = []
            children = [
                c
                for c in text_el
                if isinstance(c.tag, str) and c.tag.split("}")[-1] in ("tspan", "flowPara", "flowSpan")
            ]
            if children:
                for ch_el in children:
                    txt = "".join(ch_el.itertext())
                    for sub_line in txt.splitlines():
                        paragraphs.append(sub_line)
            else:
                paragraphs = raw_content.splitlines()

            wrapped_lines: List[str] = []
            for para in paragraphs:
                if not para.strip():
                    wrapped_lines.append("")
                else:
                    wrapped_lines.extend(_wrap_text_to_width(para, font_name, vpype_size, bw))

            cur_baseline_y = by + font_size
            for wline in wrapped_lines:
                if wline.strip():
                    lc_line = render_single_stroke_line(
                        wline, font_name=font_name, size_px=vpype_size, monospace=is_mono
                    )
                    b_ln = lc_line.bounds()
                    if b_ln:
                        w_ln = b_ln[2] - b_ln[0]
                        if anchor in ("middle", "center"):
                            ox = bx + 0.5 * (bw - w_ln) - b_ln[0]
                        elif anchor in ("end", "right"):
                            ox = bx + bw - w_ln - b_ln[0]
                        else:
                            ox = bx
                        oy = cur_baseline_y - baseline_shift
                        d_str = _lc_to_svg_path_d(lc_line, ox, oy)
                        if d_str:
                            path_d_segments.append(d_str)
                cur_baseline_y += line_spacing
        else:
            base_x = _parse_svg_number(text_el.get("x"), 0.0)
            base_y = _parse_svg_number(text_el.get("y"), 0.0)
            tspans = [
                c for c in text_el if isinstance(c.tag, str) and c.tag.split("}")[-1] == "tspan"
            ]

            if tspans:
                cur_x, cur_y = base_x, base_y
                for idx_t, ts in enumerate(tspans):
                    tx_attr = ts.get("x")
                    ty_attr = ts.get("y")
                    dx_val = _parse_svg_number(ts.get("dx"), 0.0)
                    dy_val = _parse_svg_number(ts.get("dy"), 0.0)
                    if tx_attr is not None:
                        cur_x = _parse_svg_number(tx_attr, cur_x)
                    if ty_attr is not None:
                        cur_y = _parse_svg_number(ty_attr, cur_y)
                    elif idx_t > 0 and dy_val == 0.0:
                        cur_y += line_spacing
                    cur_x += dx_val
                    cur_y += dy_val

                    ts_style = _resolve_element_style(ts, css_rules)
                    ts_fs_raw = ts_style.get("font-size")
                    ts_fs = _parse_svg_number(ts_fs_raw, font_size) if ts_fs_raw else font_size
                    ts_mono = (
                        _is_monospace_font(ts_style.get("font-family", ""))
                        if "font-family" in ts_style
                        else is_mono
                    )
                    ts_vp_size = ts_fs * (0.72 / 0.65625)
                    ts_base_shift = 0.28125 * ts_vp_size

                    txt = "".join(ts.itertext())
                    for sub_idx, sub_line in enumerate(txt.splitlines() or [txt]):
                        line_y = cur_y + sub_idx * line_spacing
                        if not sub_line.strip():
                            continue
                        lc_line = render_single_stroke_line(
                            sub_line, font_name=font_name, size_px=ts_vp_size, monospace=ts_mono
                        )
                        b_ln = lc_line.bounds()
                        if b_ln:
                            w_ln = b_ln[2] - b_ln[0]
                            if anchor in ("middle", "center"):
                                ox = cur_x - 0.5 * w_ln - b_ln[0]
                            elif anchor in ("end", "right"):
                                ox = cur_x - w_ln - b_ln[0]
                            else:
                                ox = cur_x
                            oy = line_y - ts_base_shift
                            d_str = _lc_to_svg_path_d(lc_line, ox, oy)
                            if d_str:
                                path_d_segments.append(d_str)
            else:
                for sub_idx, sub_line in enumerate(raw_content.splitlines()):
                    if not sub_line.strip():
                        continue
                    line_y = base_y + sub_idx * line_spacing
                    lc_line = render_single_stroke_line(
                        sub_line, font_name=font_name, size_px=vpype_size, monospace=is_mono
                    )
                    b_ln = lc_line.bounds()
                    if b_ln:
                        w_ln = b_ln[2] - b_ln[0]
                        if anchor in ("middle", "center"):
                            ox = base_x - 0.5 * w_ln - b_ln[0]
                        elif anchor in ("end", "right"):
                            ox = base_x - w_ln - b_ln[0]
                        else:
                            ox = base_x
                        oy = line_y - baseline_shift
                        d_str = _lc_to_svg_path_d(lc_line, ox, oy)
                        if d_str:
                            path_d_segments.append(d_str)

        if not path_d_segments:
            continue

        parent = text_el.getparent()
        if parent is None:
            continue

        path_el = etree.Element(f"{{{svg_ns}}}path")
        path_el.set("d", " ".join(path_d_segments))
        stroke_w = max(0.18, font_size * 0.055)
        path_el.set(
            "style",
            f"fill:none;stroke:#000000;stroke-width:{stroke_w:.3f};stroke-linecap:round;stroke-linejoin:round",
        )
        if text_el.get("transform"):
            path_el.set("transform", text_el.get("transform"))
        if text_el.get("id"):
            path_el.set("id", text_el.get("id") + "_path")

        parent.replace(text_el, path_el)
        converted_count += 1

    if converted_count == 0 and styles_inlined == 0:
        return input_svg, None, 0

    fd, temp_svg = tempfile.mkstemp(suffix=".svg", prefix="montebot_text_")
    os.close(fd)
    tree.write(temp_svg, encoding="utf-8", xml_declaration=True)

    if log_callback and converted_count > 0:
        log_callback(
            f"✓ {converted_count} elemento(s) <text> do SVG convertidos automaticamente em traços vetoriais!"
        )

    return temp_svg, temp_svg, converted_count


def _densify_ring(coords, step_px: float) -> List[Tuple[float, float]]:
    """Amostra uniformemente um anel de coordenadas com passo `step_px` para diagramas de Voronoi."""
    pts = [complex(x, y) for x, y in coords]
    if len(pts) < 2:
        return []
    diffs = np.diff(pts)
    lens = np.abs(diffs)
    total = float(np.sum(lens))
    if total <= step_px:
        return [(p.real, p.imag) for p in pts[:-1]]
    cum = np.concatenate(([0.0], np.cumsum(lens)))
    n = max(8, int(math.ceil(total / step_px)))
    t = np.linspace(0.0, total, n, endpoint=False)
    xs = np.interp(t, cum, [p.real for p in pts])
    ys = np.interp(t, cum, [p.imag for p in pts])
    return list(zip(xs, ys))


def _polygon_to_centerlines(poly: Polygon, pen_width_px: float) -> List[np.ndarray]:
    """
    Extrai a Linha Central (Centerline / Eixo Medial de Voronoi) de um polígono de letra oca,
    podando galhos espúrios de quina e encadeando os traços em polilinhas contínuas.
    """
    minx, miny, maxx, maxy = poly.bounds
    w = maxx - minx
    h = maxy - miny

    # Para pingos de 'i', pontos finais e sinais minúsculos, cria apenas um micro-traço no centro
    if max(w, h) <= 1.25 * pen_width_px:
        c = poly.centroid
        dx = max(0.15 * pen_width_px, min(w, pen_width_px * 0.35) * 0.5)
        return [np.array([complex(c.x - dx, c.y), complex(c.x + dx, c.y)], dtype=np.complex128)]

    step_px = max(0.22, min(0.45, pen_width_px * 0.22))
    boundary_pts = _densify_ring(poly.exterior.coords, step_px)
    for interior in poly.interiors:
        boundary_pts.extend(_densify_ring(interior.coords, step_px))

    if len(boundary_pts) < 6:
        return [np.array([complex(x, y) for x, y in poly.exterior.coords], dtype=np.complex128)]

    pts_arr = np.array(boundary_pts, dtype=float)
    try:
        vor = Voronoi(pts_arr)
    except Exception:
        return [np.array([complex(x, y) for x, y in poly.exterior.coords], dtype=np.complex128)]

    verts = vor.vertices
    in_bbox = (
        (verts[:, 0] >= minx)
        & (verts[:, 0] <= maxx)
        & (verts[:, 1] >= miny)
        & (verts[:, 1] <= maxy)
    )
    valid_v = np.zeros(len(verts), dtype=bool)
    cand_idxs = np.where(in_bbox)[0]
    if len(cand_idxs) > 0:
        valid_v[cand_idxs] = shapely.contains_xy(poly, verts[cand_idxs, 0], verts[cand_idxs, 1])

    rp = vor.ridge_points
    rv = np.asarray(vor.ridge_vertices, dtype=int)
    mask = (rv[:, 0] >= 0) & (rv[:, 1] >= 0)
    rv = rv[mask]
    rp = rp[mask]
    if len(rv) > 0:
        both_in = valid_v[rv[:, 0]] & valid_v[rv[:, 1]]
        rv = rv[both_in]
        rp = rp[both_in]

    if len(rv) > 0:
        mx = 0.5 * (verts[rv[:, 0], 0] + verts[rv[:, 1], 0])
        my = 0.5 * (verts[rv[:, 0], 1] + verts[rv[:, 1], 1])
        mid_ok = shapely.contains_xy(poly, mx, my)
        rv = rv[mid_ok]
        rp = rp[mid_ok]

    if len(rv) == 0:
        return [np.array([complex(x, y) for x, y in poly.exterior.coords], dtype=np.complex128)]

    v_radius = np.zeros(len(verts), dtype=float)
    adj: Dict[int, Set[int]] = {}
    for (p1, _p2), (v1, v2) in zip(rp, rv):
        i1, i2 = int(v1), int(v2)
        adj.setdefault(i1, set()).add(i2)
        adj.setdefault(i2, set()).add(i1)
        if v_radius[i1] == 0.0:
            v_radius[i1] = math.hypot(verts[i1, 0] - pts_arr[p1, 0], verts[i1, 1] - pts_arr[p1, 1])
        if v_radius[i2] == 0.0:
            v_radius[i2] = math.hypot(verts[i2, 0] - pts_arr[p1, 0], verts[i2, 1] - pts_arr[p1, 1])

    def _get_leaf_branch(leaf: int) -> Tuple[List[int], float, int]:
        path = [leaf]
        cur = leaf
        length = 0.0
        while True:
            nbrs = [n for n in adj.get(cur, ()) if len(path) < 2 or n != path[-2]]
            if len(nbrs) != 1:
                break
            nxt = nbrs[0]
            length += math.hypot(verts[nxt, 0] - verts[cur, 0], verts[nxt, 1] - verts[cur, 1])
            path.append(nxt)
            cur = nxt
            if len(adj.get(cur, ())) != 2:
                break
        return path, length, cur

    # Poda iterativa de espinhos de quina (spurs) em junções
    for _round in range(5):
        leaves = [u for u, nb in list(adj.items()) if len(nb) == 1]
        candidates: List[Tuple[float, List[int], int]] = []
        for lf in leaves:
            if lf not in adj or len(adj[lf]) != 1:
                continue
            branch, blen, anchor = _get_leaf_branch(lf)
            anchor_deg = len(adj.get(anchor, ()))
            if anchor_deg >= 3:
                thresh = max(1.5 * v_radius[anchor], 1.15 * pen_width_px)
                if blen <= thresh:
                    candidates.append((blen, branch, anchor))
        if not candidates:
            break
        candidates.sort(key=lambda x: x[0])
        for _blen, branch, anchor in candidates:
            if len(adj) <= 2:
                break
            if len(adj.get(anchor, ())) <= 1:
                continue
            for u in branch[:-1]:
                for nb in list(adj.get(u, ())):
                    adj[nb].discard(u)
                adj.pop(u, None)

    visited_edges: Set[Tuple[int, int]] = set()
    raw_chains: List[np.ndarray] = []

    def _edge_key(a: int, b: int) -> Tuple[int, int]:
        return (a, b) if a < b else (b, a)

    start_nodes = [u for u, nb in adj.items() if len(nb) != 2]
    for u in start_nodes:
        for nxt in list(adj.get(u, ())):
            ek = _edge_key(u, nxt)
            if ek in visited_edges:
                continue
            visited_edges.add(ek)
            chain = [u, nxt]
            cur, prev = nxt, u
            while len(adj.get(cur, ())) == 2:
                cands = [n for n in adj[cur] if n != prev]
                if not cands:
                    break
                n_node = cands[0]
                ek2 = _edge_key(cur, n_node)
                if ek2 in visited_edges:
                    break
                visited_edges.add(ek2)
                chain.append(n_node)
                prev, cur = cur, n_node
            pts_c = np.array([complex(verts[i, 0], verts[i, 1]) for i in chain], dtype=np.complex128)
            if len(pts_c) >= 2:
                raw_chains.append(pts_c)

    # Ciclos fechados (ex: letra 'o')
    for u in list(adj.keys()):
        for nxt in list(adj.get(u, ())):
            ek = _edge_key(u, nxt)
            if ek in visited_edges:
                continue
            visited_edges.add(ek)
            chain = [u, nxt]
            cur, prev = nxt, u
            while True:
                cands = [n for n in adj.get(cur, ()) if n != prev]
                if not cands:
                    break
                n_node = cands[0]
                ek2 = _edge_key(cur, n_node)
                if ek2 in visited_edges:
                    if n_node == u:
                        chain.append(u)
                    break
                visited_edges.add(ek2)
                chain.append(n_node)
                prev, cur = cur, n_node
            pts_c = np.array([complex(verts[i, 0], verts[i, 1]) for i in chain], dtype=np.complex128)
            if len(pts_c) >= 2:
                raw_chains.append(pts_c)

    # Une ramos conectados na mesma junção para minimizar levantamentos de caneta dentro da letra
    merged = True
    while merged and len(raw_chains) > 1:
        merged = False
        for i in range(len(raw_chains)):
            for j in range(i + 1, len(raw_chains)):
                a, b = raw_chains[i], raw_chains[j]
                if abs(a[-1] - b[0]) < 1e-4:
                    raw_chains[i] = np.concatenate([a, b[1:]])
                elif abs(a[-1] - b[-1]) < 1e-4:
                    raw_chains[i] = np.concatenate([a, b[-2::-1]])
                elif abs(a[0] - b[-1]) < 1e-4:
                    raw_chains[i] = np.concatenate([b, a[1:]])
                elif abs(a[0] - b[0]) < 1e-4:
                    raw_chains[i] = np.concatenate([b[::-1], a[1:]])
                else:
                    continue
                raw_chains.pop(j)
                merged = True
                break
            if merged:
                break

    return raw_chains


def _is_hollow_glyph_region(region_poly: Polygon, pen_width_px: float) -> bool:
    """
    Determina se um polígono fechado representa um glifo de letra oca (ex: fonte convertida em caminho
    duplo no Inkscape/Illustrator) que deve ser afinado para Linha Única (Centerline), ou se é um elemento
    geométrico estrutural (moldura de página, célula de tabela, caixa de gráfico, círculo de diagrama)
    cujo contorno original deve ser preservado intacto.
    """
    if region_poly.is_empty or region_poly.area <= 1e-4:
        return False

    minx, miny, maxx, maxy = region_poly.bounds
    w = maxx - minx
    h = maxy - miny

    # Elementos maiores que ~18 mm de largura ou altura são caixas, tabelas, gráficos ou molduras
    max_glyph_dim_px = 18.0 * VPYPE_MM_TO_PX
    if w > max_glyph_dim_px or h > max_glyph_dim_px:
        return False

    # Uma letra oca tem hastes finas (normalmente <= 3.5x a largura da caneta).
    # Se recuarmos (erosão) metade dessa espessura e ainda sobrar grande parte da área original,
    # trata-se de uma caixa/célula de tabela ou forma geométrica aberta, e não de uma haste de letra oca.
    max_stem_half_px = max(1.8 * pen_width_px, 0.85 * VPYPE_MM_TO_PX)
    eroded = region_poly.buffer(-max_stem_half_px, join_style=2)
    if not eroded.is_empty and eroded.area > (0.18 * region_poly.area):
        return False

    return True


def _is_structural_geometry_stroke(line: np.ndarray, med_h: float) -> bool:
    """
    Identifica se uma polilinha aberta ou fechada é um elemento estrutural/gráfico
    (ex: moldura, linha divisória longa, grade de tabela, eixo de gráfico) em vez de um glifo de texto.
    """
    if len(line) < 2:
        return False
    xs = line.real
    ys = line.imag
    w = float(np.max(xs) - np.min(xs))
    h = float(np.max(ys) - np.min(ys))

    # Molduras, linhas de tabela longas, eixos cartesianos ou curvas de gráficos amplos
    if w > max(4.5 * med_h, 16.0 * VPYPE_MM_TO_PX) or h > max(2.2 * med_h, 10.0 * VPYPE_MM_TO_PX):
        return True
    return False


def _sort_lines_for_text_writing(
    lines: List[np.ndarray],
    text_order: str = "top_to_bottom",
) -> List[np.ndarray]:
    """
    Ordena os traços de uma folha mista (texto + matemática + tabelas + gráficos) em ordem ideal:
      1. Separa traços de texto/símbolos matemáticos (traços locais) de linhas estruturais amplas
         (molduras, grades de tabelas, eixos e curvas de gráficos).
      2. Para o texto e contas:
         - Agrupa traços pertencentes ao mesmo caractere/glifo (corpo + pingo/acento/barra)
         - Agrupa os glifos em linhas horizontais (baselines)
         - Ordena as linhas de cima para baixo ('top_to_bottom') ou de baixo para cima ('bottom_to_top')
         - Dentro de cada linha, escreve estritamente da Esquerda para a Direita (X crescente)
      3. Para os elementos estruturais (tabelas, gráficos, divisórias):
         - Ordena por vizinho mais próximo (rota contínua sem saltos desnecessários).
    """
    valid_lines = [ln for ln in lines if len(ln) >= 2]
    if len(valid_lines) <= 1:
        return valid_lines

    raw_heights = [
        float(np.max(ln.imag) - np.min(ln.imag))
        for ln in valid_lines
        if 0.6 * VPYPE_MM_TO_PX < float(np.max(ln.imag) - np.min(ln.imag)) < 14.0 * VPYPE_MM_TO_PX
    ]
    med_h = float(np.median(raw_heights)) if raw_heights else (2.5 * VPYPE_MM_TO_PX)
    row_tol = max(med_h * 0.65, 1.0 * VPYPE_MM_TO_PX)

    text_items: List[Tuple[float, float, float, float, np.ndarray]] = []
    structural_lines: List[np.ndarray] = []

    for ln in valid_lines:
        if _is_structural_geometry_stroke(ln, med_h):
            structural_lines.append(ln)
        else:
            xs = ln.real
            ys = ln.imag
            text_items.append((float(np.min(xs)), float(np.min(ys)), float(np.max(xs)), float(np.max(ys)), ln))

    # Ordena preliminarmente os itens de texto por centro Y (em vpype, Y=0 é o topo e Y cresce para baixo)
    text_items.sort(key=lambda it: 0.5 * (it[1] + it[3]))

    # 1. Agrupa em linhas horizontais de texto
    rows: List[List[Tuple[float, float, float, float, np.ndarray]]] = []
    for it in text_items:
        cy = 0.5 * (it[1] + it[3])
        best_row = None
        best_dy = float("inf")
        for row in rows:
            row_cy = float(np.mean([0.5 * (r[1] + r[3]) for r in row]))
            dy = abs(cy - row_cy)
            if dy <= row_tol and dy < best_dy:
                best_dy = dy
                best_row = row
        if best_row is not None:
            best_row.append(it)
        else:
            rows.append([it])

    # Ordena as linhas pela média de Y (top_to_bottom = Y crescente; bottom_to_top = Y decrescente)
    reverse_rows = str(text_order).strip().lower() == "bottom_to_top"
    rows.sort(key=lambda r: float(np.mean([0.5 * (it[1] + it[3]) for it in r])), reverse=reverse_rows)

    ordered_result: List[np.ndarray] = []
    cur_pos: complex = complex(0.0, 0.0)

    for row in rows:
        # 2. Dentro da linha, agrupa traços que se sobrepõem horizontalmente no mesmo glifo
        row.sort(key=lambda it: it[0])
        glyphs: List[List[Tuple[float, float, float, float, np.ndarray]]] = []
        for it in row:
            x0, _y0, x1, _y1, _ln = it
            placed = False
            if glyphs:
                prev_g = glyphs[-1]
                g_x0 = min(p[0] for p in prev_g)
                g_x1 = max(p[2] for p in prev_g)
                overlap = min(g_x1, x1) - max(g_x0, x0)
                min_w = max(1e-4, min(g_x1 - g_x0, x1 - x0))
                cx_dist = abs(0.5 * (g_x0 + g_x1) - 0.5 * (x0 + x1))
                # Só agrupa no mesmo glifo se nenhum dos traços for uma linha horizontal longa (ex: traço de fração/conta)
                is_wide_bar = (x1 - x0) > 1.8 * med_h or (g_x1 - g_x0) > 1.8 * med_h
                if not is_wide_bar and (
                    overlap >= -0.06 * med_h or (overlap / min_w) > 0.35 or cx_dist <= 0.20 * med_h
                ):
                    prev_g.append(it)
                    placed = True
            if not placed:
                glyphs.append([it])

        # Garante ordem estrita da Esquerda para a Direita (menor X primeiro)
        glyphs.sort(key=lambda g: min(p[0] for p in g))

        for glyph in glyphs:
            # Orienta o primeiro traço do glifo (o maior/corpo principal) começando pela esquerda/topo
            glyph_strokes = [p[4] for p in glyph]
            glyph_strokes.sort(key=lambda s: float(np.sum(np.abs(np.diff(s)))), reverse=True)
            first_s = glyph_strokes[0]
            if abs(first_s[0] - first_s[-1]) > 1e-3:
                score_start = first_s[0].real + 0.5 * first_s[0].imag
                score_end = first_s[-1].real + 0.5 * first_s[-1].imag
                if score_end < score_start:
                    first_s = first_s[::-1]
            cur_pos = first_s[0]
            ord_g, cur_pos = _sort_lines_from_pos([first_s] + glyph_strokes[1:], cur_pos)
            ordered_result.extend(ord_g)

    # 3. Adiciona os elementos estruturais (molduras, tabelas, eixos e gráficos) ordenados de forma contínua
    if structural_lines:
        ord_struct, _ = _sort_lines_from_pos(structural_lines, cur_pos)
        ordered_result.extend(ord_struct)

    return ordered_result


def apply_text_mode_to_document(
    doc: vpype.Document,
    pen_width_mm: float = 0.5,
    centerline: bool = True,
    text_order: str = "top_to_bottom",
    log_callback: LogCallback = None,
) -> vpype.Document:
    """
    Processa o documento no Modo Texto (compatível com folhas mistas contendo textos, tabelas e gráficos):
      1. Se `centerline=True`, extrai a Linha Central apenas de letras ocas (contornos duplos),
         preservando intactos os contornos de molduras, tabelas, gráficos e caixas geométricas.
      2. Ordena textos e símbolos na sequência de escrita humana (Esquerda -> Direita, Linha por Linha)
         e traça os elementos estruturais/gráficos de forma contínua.
    """
    pen_px = max(0.05, float(pen_width_mm)) * VPYPE_MM_TO_PX
    close_tol_px = 0.25 * VPYPE_MM_TO_PX
    order_lbl = "Baixo ➔ Cima" if str(text_order).strip().lower() == "bottom_to_top" else "Cima ➔ Baixo"

    if log_callback:
        cl_lbl = "Linha única inteligente (preserva tabelas/gráficos)" if centerline else "Contorno original"
        log_callback(f"▶ Modo Texto: {cl_lbl} | Ordem Esquerda ➔ Direita ({order_lbl})...")

    total_strokes = 0
    for layer_id in list(doc.layers.keys()):
        lc = doc.layers[layer_id]
        meta = dict(lc.metadata)

        if centerline:
            regions, open_paths = _build_layer_regions(lc, close_tol_px=close_tol_px)
            layer_lines: List[np.ndarray] = list(open_paths)
            for region_poly, outlines, _ in regions:
                if _is_hollow_glyph_region(region_poly, pen_px):
                    for sub_poly in _extract_valid_polygons(region_poly):
                        layer_lines.extend(_polygon_to_centerlines(sub_poly, pen_px))
                else:
                    # Preserva molduras, células de tabelas, caixas e formas geométricas sem colapsar em Voronoi
                    if outlines:
                        layer_lines.extend(outlines)
                    else:
                        for sub_poly in _extract_valid_polygons(region_poly):
                            ext_arr = _ring_to_complex_array(sub_poly.exterior)
                            if len(ext_arr) >= 2:
                                layer_lines.append(ext_arr)
                            for hole in sub_poly.interiors:
                                h_arr = _ring_to_complex_array(hole)
                                if len(h_arr) >= 2:
                                    layer_lines.append(h_arr)
        else:
            layer_lines = list(lc)

        ordered_lines = _sort_lines_for_text_writing(layer_lines, text_order=text_order)
        total_strokes += len(ordered_lines)

        doc.pop(layer_id)
        new_lc = vpype.LineCollection(ordered_lines)
        new_lc.metadata.update(meta)
        doc.add(new_lc, layer_id)

    if log_callback:
        log_callback(f"✓ Texto e elementos gráficos otimizados para escrita: {total_strokes} traços")

    return doc


def load_and_optimize_svg(
    input_svg: str,
    tolerance: str = "0.2mm",
    simplify_tol: str = "0.05mm",
    page_mode: str = "original",
    smooth_level: str = "0.35",
    fill_enabled: bool = False,
    fill_pen_width: str = "0.5",
    fill_mode: str = "after_outline",
    fill_pattern: str = "hybrid",
    draw_mode: Optional[str] = None,
    text_centerline: bool = True,
    text_order: str = "top_to_bottom",
    text_font: str = "futural",
    log_callback: LogCallback = None,
) -> vpype.Document:
    """
    Lê um arquivo SVG e aplica o pipeline de otimização vetorial do vpype em memória:
      1. Conversão automática de tags <text>/<tspan> em vetores single-stroke
      2. Leitura do SVG (com fallback sem corte de página caso o desenho extrapole o viewBox)
      3. Enquadramento opcional na folha A4 (page_mode == 'a4_fit')
      4. União de segmentos conectados (linemerge)
      5. Suavização anti-vibração opcional com preservação de quinas (smooth_level > 0)
      6. Simplificação de micro-segmentos colineares (linesimplify)
      7. Modo de processamento: 'normal' (linesort), 'fill' (hachura) ou 'text' (centerline + escrita)
    """
    if not os.path.isfile(input_svg):
        raise FileNotFoundError(f"Arquivo SVG não encontrado: {input_svg}")

    effective_mode = str(draw_mode or ("fill" if fill_enabled else "normal")).strip().lower()
    is_fill_mode = effective_mode == "fill" or (draw_mode is None and fill_enabled)
    is_text_mode = effective_mode == "text"

    tol_str = _normalize_length_str(tolerance, "0.2mm")
    simp_mm = _parse_numeric_mm(simplify_tol, 0.05)
    simp_str = _normalize_length_str(simplify_tol, "0.05mm")
    smooth_mm = _parse_numeric_mm(smooth_level, 0.0)
    pen_width_mm = _parse_numeric_mm(fill_pen_width, 0.5)

    if log_callback:
        log_callback(f"▶ Lendo vetores do SVG: {os.path.basename(input_svg)}")

    # 1. Pré-processa tags <text> do SVG em caminhos vetoriais automaticamente
    svg_to_read, temp_text_svg, converted_texts = preprocess_svg_text_elements(
        input_svg,
        font_name=text_font,
        log_callback=log_callback,
    )
    quoted_in = _quote_vpype_path(svg_to_read)

    try:
        # 2. Tenta ler preservando a página original; se ficar vazio (desenho fora da página), lê com --no-crop
        doc = vpype_cli.execute(f"read {quoted_in}")
        if doc.is_empty():
            if log_callback:
                log_callback("ℹ Geometria fora dos limites da página SVG; recarregando com --no-crop...")
            doc = vpype_cli.execute(f"read --no-crop {quoted_in}")
    finally:
        if temp_text_svg and os.path.exists(temp_text_svg):
            try:
                os.remove(temp_text_svg)
            except OSError:
                pass

    if doc.is_empty():
        raise RuntimeError(
            "Nenhuma geometria vetorial ou texto foi encontrada no SVG.\n"
            "Verifique se o arquivo contém caminhos (<path>, <line>, <rect>, <text>) "
            "e se imagens bitmap foram vetorizadas."
        )

    raw_paths = sum(len(lc) for lc in doc.layers.values())
    raw_points = sum(len(line) for lc in doc.layers.values() for line in lc)
    if log_callback:
        log_callback(f"  • Geometria bruta: {raw_paths} caminhos ({raw_points} vértices)")

    # 3. Enquadramento e união inicial de segmentos conectados
    pre_cmds: List[str] = []
    if page_mode == "a4_fit":
        if log_callback:
            log_callback("▶ Ajustando e centralizando desenho na folha A4 (210×297 mm, margem 10 mm)...")
        pre_cmds.append("layout --fit-to-margins 10mm a4")

    pre_cmds.append(f"linemerge --tolerance {tol_str}")
    pre_cmd_str = " ".join(pre_cmds)
    if log_callback:
        log_callback(f"▶ vpype: {pre_cmd_str}")
    doc = vpype_cli.execute(pre_cmd_str, document=doc)

    # 4. Suavização Gaussiana anti-vibração (com preservação de quinas)
    if smooth_mm > 0 and converted_texts == 0:
        doc = smooth_document_paths(doc, smooth_mm=smooth_mm, log_callback=log_callback)

    # 5. Simplificação de micro-segmentos (e ordenação padrão apenas no modo normal)
    post_cmds: List[str] = []
    if simp_mm > 0:
        post_cmds.append(f"linesimplify --tolerance {simp_str}")
    elif smooth_mm > 0:
        post_cmds.append("linesimplify --tolerance 0.02mm")

    if not is_fill_mode and not is_text_mode:
        post_cmds.append("linesort")

    if post_cmds:
        post_cmd_str = " ".join(post_cmds)
        if log_callback:
            log_callback(f"▶ vpype: {post_cmd_str}")
        doc = vpype_cli.execute(post_cmd_str, document=doc)

    # 6. Modo Preenchimento (Fill) ou Modo Escrita de Texto
    if is_fill_mode:
        doc = apply_fill_to_document(
            doc,
            pen_width_mm=pen_width_mm,
            fill_mode=fill_mode,
            fill_pattern=fill_pattern,
            log_callback=log_callback,
        )
    elif is_text_mode:
        doc = apply_text_mode_to_document(
            doc,
            pen_width_mm=pen_width_mm,
            centerline=text_centerline,
            text_order=text_order,
            log_callback=log_callback,
        )
        if simp_mm > 0:
            doc = vpype_cli.execute(f"linesimplify --tolerance {simp_str}", document=doc)

    # Garante tamanho de página válido para referência de inversão do eixo Y
    if not doc.page_size or doc.page_size[0] <= 0 or doc.page_size[1] <= 0:
        bounds = doc.bounds()
        if bounds:
            max_x_px, max_y_px = bounds[2], bounds[3]
            w_px = max(A4_WIDTH_MM * VPYPE_MM_TO_PX, max_x_px)
            h_px = max(A4_HEIGHT_MM * VPYPE_MM_TO_PX, max_y_px)
            doc.page_size = (w_px, h_px)
        else:
            doc.page_size = (A4_WIDTH_MM * VPYPE_MM_TO_PX, A4_HEIGHT_MM * VPYPE_MM_TO_PX)

    opt_paths = sum(len(lc) for lc in doc.layers.values())
    opt_points = sum(len(line) for lc in doc.layers.values() for line in lc)
    if log_callback:
        reduction = (1.0 - (opt_points / max(1, raw_points))) * 100.0
        log_callback(
            f"✓ Otimização concluída: {opt_paths} rotas, {opt_points} pontos "
            f"({reduction:.1f}% de redução de micro-segmentos)"
        )

    return doc


def generate_gcode_from_document(
    doc: vpype.Document,
    output_gcode: str,
    feed_rate: float = 1000.0,
    plunge_rate: float = 600.0,
    lift_height: float = 5.0,
    cut_depth: float = -1.0,
    z_mode: str = "servo",
    servo_up: float = 250.0,
    servo_down: float = 0.0,
    servo_delay: float = 0.15,
    return_origin: bool = True,
    log_callback: LogCallback = None,
) -> Tuple[int, int]:
    """
    Converte um vpype.Document otimizado diretamente em um arquivo G-code padronizado (.gcode).
    Suporta dois modos de eixo Z (plunge):
      - 'servo': Acionamento via PWM na porta Z+ (M03 S<up> / M03 S<down> + G4 P<delay> opcional)
      - 'stepper': Acionamento via motor de passo tradicional (G0 Z<lift> / G1 Z<depth> F<plunge>)
    Retorna (total_linhas_gcode, total_rotas_corte).
    """
    is_servo = str(z_mode).strip().lower() == "servo"
    if log_callback:
        mode_desc = "Motor Servo (M03 S...)" if is_servo else "Motor de Passo (G0/G1 Z...)"
        log_callback(f"▶ Gerando instruções G-code nativamente [{mode_desc}]...")

    page_w_px, page_h_px = doc.page_size if doc.page_size else (
        A4_WIDTH_MM * VPYPE_MM_TO_PX,
        A4_HEIGHT_MM * VPYPE_MM_TO_PX,
    )

    feed_str = _format_num(feed_rate, keep_decimal=False)
    plunge_str = _format_num(plunge_rate, keep_decimal=False)
    lift_str = _format_num(lift_height, keep_decimal=False)
    depth_str = _format_num(cut_depth, keep_decimal=True)
    s_up_str = _format_num(servo_up, keep_decimal=False)
    s_down_str = _format_num(servo_down, keep_decimal=False)
    delay_str = _format_num(servo_delay, keep_decimal=False) if servo_delay > 0 else ""

    def _emit_pen_up(target_lines: List[str]):
        if is_servo:
            target_lines.append(f"M03 S{s_up_str}\n")
            if delay_str:
                target_lines.append(f"G4 P{delay_str}\n")
        else:
            target_lines.append(f"G0 Z{lift_str}\n")

    def _emit_pen_down(target_lines: List[str]):
        if is_servo:
            target_lines.append(f"M03 S{s_down_str}\n")
            if delay_str:
                target_lines.append(f"G4 P{delay_str}\n")
            target_lines.append(f"G1 F{feed_str}\n")
        else:
            target_lines.append(f"G1 Z{depth_str} F{plunge_str}\n")
            target_lines.append(f"G1 F{feed_str}\n")

    lines: List[str] = [
        "G21\n",
        "G90\n",
        f"G1 F{feed_str}\n",
    ]
    _emit_pen_up(lines)

    pen_down = False
    cur_x: Optional[float] = None
    cur_y: Optional[float] = None
    route_count = 0

    # Ordena camadas por ID para determinismo
    for layer_id in sorted(doc.layers.keys()):
        lc = doc.layers[layer_id]
        for line in lc:
            if len(line) < 2:
                continue

            # Converte de pixels vpype (96 DPI, origem topo-esquerdo) para mm (origem base-esquerda)
            pts_mm: List[Tuple[float, float]] = []
            for pt in line:
                x_mm = float(pt.real) * VPYPE_PX_TO_MM
                y_mm = float(page_h_px - pt.imag) * VPYPE_PX_TO_MM
                # Filtra pontos consecutivos duplicados (distância < 0.0001 mm)
                if not pts_mm or math.hypot(x_mm - pts_mm[-1][0], y_mm - pts_mm[-1][1]) >= 1e-4:
                    pts_mm.append((x_mm, y_mm))

            if len(pts_mm) < 2:
                continue

            x0, y0 = pts_mm[0]

            # Verifica se já estamos exatamente no ponto inicial com a caneta abaixada
            need_travel = (
                cur_x is None
                or cur_y is None
                or math.hypot(x0 - cur_x, y0 - cur_y) >= 1e-3
            )

            if need_travel:
                if pen_down:
                    _emit_pen_up(lines)
                    pen_down = False
                lines.append(f"G00 X{x0:.2f} Y{y0:.2f}\n")

            just_plunged = False
            if not pen_down:
                _emit_pen_down(lines)
                pen_down = True
                just_plunged = True

            for idx_pt, (xi, yi) in enumerate(pts_mm[1:]):
                if idx_pt == 0 and just_plunged:
                    lines.append(f"G01 X{xi:.2f} Y{yi:.2f} F{feed_str}\n")
                else:
                    lines.append(f"G01 X{xi:.2f} Y{yi:.2f}\n")

            cur_x, cur_y = pts_mm[-1]
            route_count += 1

    # Finalização segura: levanta a ferramenta e opcionalmente retorna à origem (0, 0)
    if pen_down:
        _emit_pen_up(lines)
        pen_down = False

    if return_origin:
        lines.append("G00 X0.00 Y0.00\n")

    with open(output_gcode, "w", encoding="utf-8") as f:
        f.writelines(lines)

    if log_callback:
        log_callback(f"✓ G-code gerado com sucesso ({len(lines)} linhas, {route_count} traços)")

    return len(lines), route_count


def run_full_pipeline(
    input_svg: str,
    output_gcode: str,
    params: Dict[str, Any],
    log_callback: LogCallback = None,
) -> Tuple[int, int]:
    """
    Executa o pipeline completo de validação de parâmetros, otimização vetorial via vpype
    e geração nativa do arquivo G-code.
    """
    z_mode = str(params.get("z_mode", "servo")).strip().lower()
    is_servo = z_mode == "servo"

    try:
        feed_rate = float(str(params.get("feed_rate", "1000")).replace(",", "."))
        if is_servo:
            servo_up = float(str(params.get("servo_up", "250")).replace(",", "."))
            servo_down = float(str(params.get("servo_down", "0")).replace(",", "."))
            servo_delay = float(str(params.get("servo_delay", "0.15")).replace(",", "."))
            plunge_rate = float(str(params.get("plunge_rate", "600") or "600").replace(",", "."))
            lift_height = float(str(params.get("lift_height", "5.0") or "5.0").replace(",", "."))
            cut_depth = float(str(params.get("cut_depth", "-1.0") or "-1.0").replace(",", "."))
        else:
            plunge_rate = float(str(params.get("plunge_rate", "600")).replace(",", "."))
            lift_height = float(str(params.get("lift_height", "5.0")).replace(",", "."))
            cut_depth = float(str(params.get("cut_depth", "-1.0")).replace(",", "."))
            servo_up = float(str(params.get("servo_up", "250") or "250").replace(",", "."))
            servo_down = float(str(params.get("servo_down", "0") or "0").replace(",", "."))
            servo_delay = float(str(params.get("servo_delay", "0.15") or "0.15").replace(",", "."))
    except ValueError as exc:
        raise ValueError(
            "Parâmetros numéricos inválidos. Verifique se as velocidades e configurações "
            "do Eixo Z (Servo UP/DOWN/Delay ou Altura Lift/Z de Desenho) contêm números válidos."
        ) from exc

    if feed_rate <= 0 or (not is_servo and plunge_rate <= 0):
        raise ValueError("As velocidades (Feed rate / Plunge rate) devem ser maiores que zero.")
    if is_servo and (servo_up < 0 or servo_down < 0 or servo_delay < 0):
        raise ValueError("Os valores do Servo (UP, DOWN e Delay) não podem ser negativos.")

    tolerance = str(params.get("tolerance", "0.2mm"))
    simplify_tol = str(params.get("simplify_tol", "0.05mm"))
    smooth_level = str(params.get("smooth_level", "0.35"))
    fill_enabled = bool(params.get("fill_enabled", False))
    draw_mode = str(params.get("draw_mode") or ("fill" if fill_enabled else "normal")).strip().lower()
    if draw_mode == "fill":
        fill_enabled = True
    fill_pen_width = str(params.get("fill_pen_width", "0.5"))
    fill_mode = str(params.get("fill_mode", "after_outline"))
    fill_pattern = str(params.get("fill_pattern", "hybrid"))
    text_centerline = bool(params.get("text_centerline", True))
    text_order = str(params.get("text_order", "top_to_bottom"))
    text_font = str(params.get("text_font", "futural"))
    page_mode = str(params.get("page_mode", "original"))
    return_origin = bool(params.get("return_origin", True))

    if _parse_numeric_mm(smooth_level, 0.0) < 0:
        raise ValueError("A suavização anti-vibração não pode ser negativa (use 0 para desativar).")
    if draw_mode in ("fill", "text") and _parse_numeric_mm(fill_pen_width, 0.0) <= 0:
        raise ValueError("A largura da caneta deve ser maior que zero.")

    doc = load_and_optimize_svg(
        input_svg=input_svg,
        tolerance=tolerance,
        simplify_tol=simplify_tol,
        page_mode=page_mode,
        smooth_level=smooth_level,
        fill_enabled=fill_enabled,
        fill_pen_width=fill_pen_width,
        fill_mode=fill_mode,
        fill_pattern=fill_pattern,
        draw_mode=draw_mode,
        text_centerline=text_centerline,
        text_order=text_order,
        text_font=text_font,
        log_callback=log_callback,
    )

    return generate_gcode_from_document(
        doc=doc,
        output_gcode=output_gcode,
        feed_rate=feed_rate,
        plunge_rate=plunge_rate,
        lift_height=lift_height,
        cut_depth=cut_depth,
        z_mode=z_mode,
        servo_up=servo_up,
        servo_down=servo_down,
        servo_delay=servo_delay,
        return_origin=return_origin,
        log_callback=log_callback,
    )

