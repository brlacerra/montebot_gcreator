"""
Pipeline de conversão SVG -> G-code 100% nativo em Python utilizando vpype.
Realiza leitura vetorial, tratamento de escala/página, união de linhas (linemerge),
simplificação de micro-segmentos (linesimplify), ordenação de rotas (linesort)
e geração direta de G-code padronizado.
"""

import math
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import vpype
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


def load_and_optimize_svg(
    input_svg: str,
    tolerance: str = "0.2mm",
    simplify_tol: str = "0.05mm",
    page_mode: str = "original",
    log_callback: LogCallback = None,
) -> vpype.Document:
    """
    Lê um arquivo SVG e aplica o pipeline de otimização vetorial do vpype em memória:
      1. Leitura do SVG (com fallback sem corte de página caso o desenho extrapole o viewBox)
      2. Enquadramento opcional na folha A4 (page_mode == 'a4_fit')
      3. União de segmentos conectados (linemerge)
      4. Simplificação de micro-segmentos colineares (linesimplify)
      5. Ordenação de caminhos para minimizar viagens (linesort)
    """
    if not os.path.isfile(input_svg):
        raise FileNotFoundError(f"Arquivo SVG não encontrado: {input_svg}")

    tol_str = _normalize_length_str(tolerance, "0.2mm")
    simp_mm = _parse_numeric_mm(simplify_tol, 0.05)
    simp_str = _normalize_length_str(simplify_tol, "0.05mm")
    quoted_in = _quote_vpype_path(input_svg)

    if log_callback:
        log_callback(f"▶ Lendo vetores do SVG: {os.path.basename(input_svg)}")

    # 1. Tenta ler preservando a página original; se ficar vazio (desenho fora da página), lê com --no-crop
    doc = vpype_cli.execute(f"read {quoted_in}")
    if doc.is_empty():
        if log_callback:
            log_callback("ℹ Geometria fora dos limites da página SVG; recarregando com --no-crop...")
        doc = vpype_cli.execute(f"read --no-crop {quoted_in}")

    if doc.is_empty():
        raise RuntimeError(
            "Nenhuma geometria vetorial foi encontrada no SVG.\n"
            "Verifique se o arquivo contém caminhos (<path>, <line>, <rect>, etc.) "
            "e se textos ou imagens bitmap foram convertidos em curvas no Inkscape."
        )

    raw_paths = sum(len(lc) for lc in doc.layers.values())
    raw_points = sum(len(line) for lc in doc.layers.values() for line in lc)
    if log_callback:
        log_callback(f"  • Geometria bruta: {raw_paths} caminhos ({raw_points} vértices)")

    # 2. Constrói pipeline de otimização sobre o documento carregado
    pipeline_cmds: List[str] = []

    if page_mode == "a4_fit":
        if log_callback:
            log_callback("▶ Ajustando e centralizando desenho na folha A4 (210×297 mm, margem 10 mm)...")
        pipeline_cmds.append("layout --fit-to-margins 10mm a4")

    pipeline_cmds.append(f"linemerge --tolerance {tol_str}")

    if simp_mm > 0:
        pipeline_cmds.append(f"linesimplify --tolerance {simp_str}")

    pipeline_cmds.append("linesort")

    cmd_str = " ".join(pipeline_cmds)
    if log_callback:
        log_callback(f"▶ vpype: {cmd_str}")

    doc = vpype_cli.execute(cmd_str, document=doc)

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
    return_origin: bool = True,
    log_callback: LogCallback = None,
) -> Tuple[int, int]:
    """
    Converte um vpype.Document otimizado diretamente em um arquivo G-code padronizado (.gcode).
    Retorna (total_linhas_gcode, total_rotas_corte).
    """
    if log_callback:
        log_callback("▶ Gerando instruções G-code nativamente...")

    page_w_px, page_h_px = doc.page_size if doc.page_size else (
        A4_WIDTH_MM * VPYPE_MM_TO_PX,
        A4_HEIGHT_MM * VPYPE_MM_TO_PX,
    )

    feed_str = _format_num(feed_rate, keep_decimal=False)
    plunge_str = _format_num(plunge_rate, keep_decimal=False)
    lift_str = _format_num(lift_height, keep_decimal=False)
    depth_str = _format_num(cut_depth, keep_decimal=True)

    lines: List[str] = [
        "G21\n",
        "G90\n",
        f"G1 F{feed_str}\n",
        f"G0 Z{lift_str}\n",
    ]

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
                    lines.append(f"G0 Z{lift_str}\n")
                    pen_down = False
                lines.append(f"G00 X{x0:.4f} Y{y0:.4f}\n")

            if not pen_down:
                lines.append(f"G1 Z{depth_str} F{plunge_str}\n")
                lines.append(f"G1 F{feed_str}\n")
                pen_down = True

            for xi, yi in pts_mm[1:]:
                lines.append(f"G01 X{xi:.4f} Y{yi:.4f}\n")

            cur_x, cur_y = pts_mm[-1]
            route_count += 1

    # Finalização segura: levanta a ferramenta e opcionalmente retorna à origem (0, 0)
    if pen_down:
        lines.append(f"G0 Z{lift_str}\n")
        pen_down = False

    if return_origin:
        lines.append("G00 X0.0000 Y0.0000\n")

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
    try:
        feed_rate = float(str(params.get("feed_rate", "1000")).replace(",", "."))
        plunge_rate = float(str(params.get("plunge_rate", "600")).replace(",", "."))
        lift_height = float(str(params.get("lift_height", "5.0")).replace(",", "."))
        cut_depth = float(str(params.get("cut_depth", "-1.0")).replace(",", "."))
    except ValueError as exc:
        raise ValueError(
            "Parâmetros numéricos inválidos. Verifique se Feed Rate, Plunge Rate, "
            "Altura Lift e Z de Desenho contêm apenas números válidos."
        ) from exc

    if feed_rate <= 0 or plunge_rate <= 0:
        raise ValueError("As velocidades (Feed rate e Plunge rate) devem ser maiores que zero.")

    tolerance = str(params.get("tolerance", "0.2mm"))
    simplify_tol = str(params.get("simplify_tol", "0.05mm"))
    page_mode = str(params.get("page_mode", "original"))
    return_origin = bool(params.get("return_origin", True))

    doc = load_and_optimize_svg(
        input_svg=input_svg,
        tolerance=tolerance,
        simplify_tol=simplify_tol,
        page_mode=page_mode,
        log_callback=log_callback,
    )

    return generate_gcode_from_document(
        doc=doc,
        output_gcode=output_gcode,
        feed_rate=feed_rate,
        plunge_rate=plunge_rate,
        lift_height=lift_height,
        cut_depth=cut_depth,
        return_origin=return_origin,
        log_callback=log_callback,
    )

