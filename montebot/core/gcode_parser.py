"""
Parser e analisador estatístico de arquivos G-code (.gcode).
Extrai rotas de corte (G1), rotas de transição/viagem (G0), limites geométricos
e métricas completas de execução (distâncias, levantamentos de ferramenta e tempo estimado).
"""

from dataclasses import dataclass
import math
import re
from typing import List, Tuple

Point2D = Tuple[float, float]
Polyline2D = List[Point2D]


@dataclass
class GCodeStats:
    """Métricas extraídas do arquivo G-code gerado."""
    cut_distance_mm: float = 0.0
    travel_distance_mm: float = 0.0
    pen_lifts: int = 0
    total_segments: int = 0
    total_lines: int = 0
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0
    estimated_time_sec: float = 0.0
    has_negative_coords: bool = False

    @property
    def total_distance_mm(self) -> float:
        return self.cut_distance_mm + self.travel_distance_mm

    def format_time(self) -> str:
        """Formata o tempo estimado em formato legível (ex: '2m 15s' ou '1h 04m')."""
        total_sec = max(1, int(round(self.estimated_time_sec)))
        if total_sec < 60:
            return f"{total_sec}s"
        minutes, seconds = divmod(total_sec, 60)
        if minutes < 60:
            return f"{minutes}m {seconds:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}m"


_CMD_RE = re.compile(r"^(G00|G01|G0|G1)\b", re.IGNORECASE)
_X_RE = re.compile(r"X\s*([-+]?\d*\.?\d+)", re.IGNORECASE)
_Y_RE = re.compile(r"Y\s*([-+]?\d*\.?\d+)", re.IGNORECASE)
_Z_RE = re.compile(r"Z\s*([-+]?\d*\.?\d+)", re.IGNORECASE)
_F_RE = re.compile(r"F\s*([-+]?\d*\.?\d+)", re.IGNORECASE)


def parse_gcode_file(
    filepath: str,
    default_feed_rate: float = 1000.0,
    default_plunge_rate: float = 600.0,
    rapid_rate: float = 3000.0,
) -> Tuple[List[Polyline2D], List[Polyline2D], Tuple[float, float, float, float], GCodeStats]:
    """
    Analisa um arquivo G-code e retorna:
      - cut_paths: Lista de polilinhas [(x, y), ...] correspondentes aos cortes/desenhos (G1)
      - travel_paths: Lista de segmentos [(x1, y1), (x2, y2)] correspondentes às viagens rápidas (G0)
      - bounds: (min_x, min_y, width, height) das geometrias desenhadas
      - stats: Objeto GCodeStats com métricas detalhadas
    """
    cut_paths: List[Polyline2D] = []
    travel_paths: List[Polyline2D] = []
    current_cut: Polyline2D = []

    x, y, z = 0.0, 0.0, 5.0
    feed_rate = max(1.0, float(default_feed_rate))
    plunge_rate = max(1.0, float(default_plunge_rate))

    cut_dist = 0.0
    travel_dist = 0.0
    z_time_sec = 0.0
    pen_lifts = 0
    total_lines = 0
    total_segments = 0

    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.split(";")[0].strip()
            if not line:
                continue
            total_lines += 1

            cmd_match = _CMD_RE.match(line)
            if not cmd_match:
                continue

            cmd = cmd_match.group(1).upper()
            x_match = _X_RE.search(line)
            y_match = _Y_RE.search(line)
            z_match = _Z_RE.search(line)
            f_match = _F_RE.search(line)

            if f_match:
                try:
                    parsed_f = float(f_match.group(1))
                    if parsed_f > 0 and not z_match:
                        feed_rate = parsed_f
                except ValueError:
                    pass

            if z_match:
                try:
                    new_z = float(z_match.group(1))
                    dz = abs(new_z - z)
                    if dz > 1e-4:
                        rate = plunge_rate if new_z < z else rapid_rate
                        z_time_sec += (dz / max(1.0, rate)) * 60.0
                        if new_z > z and current_cut:
                            pen_lifts += 1
                    z = new_z
                except ValueError:
                    pass

            has_xy = (x_match is not None) or (y_match is not None)
            if not has_xy:
                # Se for um G0 apenas de eixo Z, finaliza o corte atual se existir
                if cmd in ("G0", "G00") and current_cut:
                    if len(current_cut) >= 2:
                        cut_paths.append(current_cut)
                    current_cut = []
                continue

            new_x = float(x_match.group(1)) if x_match else x
            new_y = float(y_match.group(1)) if y_match else y
            dist = math.hypot(new_x - x, new_y - y)

            if cmd in ("G00", "G0"):
                if current_cut:
                    if len(current_cut) >= 2:
                        cut_paths.append(current_cut)
                    current_cut = []
                if dist > 1e-5:
                    travel_paths.append([(x, y), (new_x, new_y)])
                    travel_dist += dist
            elif cmd in ("G01", "G1"):
                if not current_cut:
                    current_cut.append((x, y))
                if dist > 1e-5:
                    current_cut.append((new_x, new_y))
                    cut_dist += dist
                    total_segments += 1

            x, y = new_x, new_y

    if current_cut and len(current_cut) >= 2:
        cut_paths.append(current_cut)

    if pen_lifts == 0 and cut_paths:
        pen_lifts = len(cut_paths)
    else:
        pen_lifts = max(pen_lifts, len(cut_paths))

    # Calcula limites geométricos (priorizando os pontos de corte/desenho)
    cut_pts = [pt for pl in cut_paths for pt in pl]
    all_pts = cut_pts if cut_pts else [pt for pl in travel_paths for pt in pl]

    if all_pts:
        xs = [p[0] for p in all_pts]
        ys = [p[1] for p in all_pts]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width_mm = max_x - min_x
        height_mm = max_y - min_y
        bounds = (min_x, min_y, max(width_mm, 1e-3), max(height_mm, 1e-3))
    else:
        min_x = min_y = max_x = max_y = width_mm = height_mm = 0.0
        bounds = (0.0, 0.0, 100.0, 100.0)

    # Estimativa realista de tempo considerando aceleração média de plotter (fator 1.15x)
    cut_time_sec = (cut_dist / max(1.0, feed_rate)) * 60.0 * 1.15
    travel_time_sec = (travel_dist / max(1.0, rapid_rate)) * 60.0 * 1.15
    estimated_time_sec = cut_time_sec + travel_time_sec + z_time_sec

    stats = GCodeStats(
        cut_distance_mm=cut_dist,
        travel_distance_mm=travel_dist,
        pen_lifts=pen_lifts,
        total_segments=total_segments,
        total_lines=total_lines,
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        width_mm=width_mm,
        height_mm=height_mm,
        estimated_time_sec=estimated_time_sec,
        has_negative_coords=(min_x < -0.01 or min_y < -0.01),
    )

    return cut_paths, travel_paths, bounds, stats


def parse_gcode_to_paths(filepath: str):
    """Wrapper de compatibilidade que retorna apenas (cut_paths, travel_paths, bounds)."""
    cut_paths, travel_paths, bounds, _ = parse_gcode_file(filepath)
    return cut_paths, travel_paths, bounds

