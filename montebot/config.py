"""
Configurações globais, constantes de máquina e paleta White Mode do MonteBot GCreator.
"""

APP_TITLE = "MonteBot GCreator — SVG para G-code"
APP_NAME = "MONTEBOT GCREATOR"
APP_VERSION = "v0.2.0 • UFU Monte Carmelo"

# Dimensões físicas padrão da folha A4 em milímetros
A4_WIDTH_MM = 210.0
A4_HEIGHT_MM = 297.0

# Conversão interna do vpype (96 DPI: 1 inch = 25.4 mm = 96 px)
VPYPE_PX_TO_MM = 25.4 / 96.0
VPYPE_MM_TO_PX = 96.0 / 25.4

# Parâmetros padrão de geração de G-code
DEFAULT_PARAMS = {
    "feed_rate": "1000",       # Velocidade de desenho/corte (mm/min)
    "plunge_rate": "600",      # Velocidade de descida Z (mm/min)
    "lift_height": "5.0",      # Altura Z levantado / viagem (mm)
    "cut_depth": "-1.0",       # Altura/profundidade Z de desenho (mm)
    "tolerance": "0.2mm",      # Tolerância de união de linhas (linemerge)
    "simplify_tol": "0.05mm",  # Tolerância de simplificação de micro-segmentos (linesimplify)
    "page_mode": "original",   # 'original' | 'a4_fit'
    "return_origin": True,     # Retornar para X0 Y0 ao finalizar
}

# Paleta de cores — White Mode Técnico (Engenharia / CAD)
THEME = {
    # Superfícies principais
    "bg_app": "#F8FAFC",          # Fundo geral da janela (Slate 50)
    "bg_sidebar": "#FFFFFF",      # Painel lateral (Branco puro)
    "bg_header": "#FFFFFF",       # Barra superior do preview
    "bg_card": "#F8FAFC",         # Fundo de cards internos e seções
    "bg_input": "#FFFFFF",        # Fundo dos campos de entrada
    "bg_log": "#F8FAFC",          # Fundo do terminal de log

    # Área do Canvas e Folha Física
    "bg_canvas": "#E2E8F0",       # Mesa de trabalho ao redor da folha (Slate 200)
    "bg_paper": "#FFFFFF",        # Superfície da folha física (Branco puro)
    "paper_shadow": "#CBD5E1",    # Sombra projetada da folha
    "paper_border": "#94A3B8",    # Contorno da folha física
    "grid_major": "#E2E8F0",      # Grade principal (50mm) dentro da folha
    "grid_minor": "#F1F5F9",      # Grade secundária (10mm) dentro da folha
    "desk_grid": "#CBD5E1",       # Grade externa da mesa

    # Bordas e divisores
    "border": "#E2E8F0",          # Divisórias sutis (Slate 200)
    "border_strong": "#CBD5E1",   # Bordas de inputs e botões secundários (Slate 300)
    "border_focus": "#2563EB",    # Realce de foco nos inputs (Blue 600)

    # Tipografia
    "text_primary": "#0F172A",    # Texto principal de alto contraste (Slate 900)
    "text_secondary": "#334155",  # Subtítulos e rótulos de campos (Slate 700)
    "text_muted": "#64748B",      # Dicas, unidades e textos auxiliares (Slate 500)

    # Cores de Ação e Status
    "primary": "#2563EB",         # Azul Engenharia principal (Blue 600)
    "primary_hover": "#1D4ED8",   # Hover do botão primário (Blue 700)
    "primary_light": "#EFF6FF",   # Fundo suave azul (Blue 50)
    "primary_fg": "#FFFFFF",      # Texto sobre botão primário

    "accent": "#059669",          # Verde Esmeralda para conclusão/salvar (Emerald 600)
    "accent_hover": "#047857",    # Hover do botão verde (Emerald 700)
    "accent_light": "#ECFDF5",    # Fundo suave verde (Emerald 50)
    "accent_fg": "#FFFFFF",

    "secondary_bg": "#F1F5F9",    # Botões neutros/secundários (Slate 100)
    "secondary_hover": "#E2E8F0", # Hover de botão neutro (Slate 200)
    "secondary_fg": "#1E293B",    # Texto de botão neutro (Slate 800)

    "disabled_bg": "#F1F5F9",     # Estado desabilitado (fundo)
    "disabled_fg": "#94A3B8",     # Estado desabilitado (texto)

    "warning": "#D97706",         # Alerta / Viagens (Amber 600)
    "warning_bg": "#FFFBEB",
    "danger": "#DC2626",          # Erro (Red 600)
    "danger_bg": "#FEF2F2",

    # Cores das rotas no Canvas de Preview
    "travel_color": "#F59E0B",    # Laranja/Âmbar visível no fundo branco (G0 pendente)
    "done_cut_color": "#CBD5E1",  # Cinza claro/opaco para linhas de corte (G1) onde a caneta já passou
    "done_travel_color": "#E2E8F0",  # Cinza suave para viagens (G0) já realizadas
    "start_marker": "#059669",    # Ponto inicial (Verde Esmeralda)
    "end_marker": "#7C3AED",      # Ponto final (Roxo Índigo)
    "origin_marker": "#DC2626",   # Indicador de origem (0,0)
    "toolhead_pen_down": "#DC2626",  # Ponteiro da máquina ao vivo com caneta abaixada
    "toolhead_pen_up": "#2563EB",    # Ponteiro da máquina ao vivo com caneta levantada

    # Cores do Inspetor de Código .gcode (Executado vs Pendente)
    "code_pending_fg": "#1E40AF",  # Azul meio escuro para linhas de G-code que ainda não rodaram
    "code_done_fg": "#94A3B8",     # Cinza claro/opaco para linhas de G-code já executadas
    "code_current_bg": "#DBEAFE",  # Realce de fundo da linha em execução atual
    "code_current_fg": "#1E3A8A",  # Texto da linha em execução atual
}

# Configurações de Comunicação Serial / GRBL
GRBL_BAUDRATES = ["115200", "250000", "57600", "38400", "19200", "9600"]
GRBL_DEFAULT_BAUD = "115200"
GRBL_RX_BUFFER_SIZE = 127        # Tamanho seguro do buffer de recepção do GRBL (128 bytes máx)
GRBL_POLL_INTERVAL_SEC = 0.20    # Intervalo de polling '?' (5 Hz)
SIMULATOR_PORT = "SIMULATOR"

# Cores dos estados do GRBL no DRO
GRBL_STATE_STYLE = {
    "DISCONNECTED": ("#F1F5F9", "#64748B"),
    "IDLE": ("#ECFDF5", "#059669"),
    "RUN": ("#EFF6FF", "#2563EB"),
    "JOG": ("#EFF6FF", "#2563EB"),
    "HOLD": ("#FFFBEB", "#D97706"),
    "ALARM": ("#FEF2F2", "#DC2626"),
    "DOOR": ("#FEF2F2", "#DC2626"),
    "HOME": ("#F5F3FF", "#7C3AED"),
    "CHECK": ("#F5F3FF", "#7C3AED"),
}


# Definições tipográficas
FONTS = {
    "title": ("Segoe UI", 14, "bold"),
    "subtitle": ("Segoe UI", 8),
    "section": ("Segoe UI", 8, "bold"),
    "label": ("Segoe UI", 9),
    "label_bold": ("Segoe UI", 9, "bold"),
    "small": ("Segoe UI", 8),
    "button": ("Segoe UI", 9, "bold"),
    "toolbar_btn": ("Segoe UI", 8, "bold"),
    "input": ("Consolas", 10),
    "mono_small": ("Consolas", 8),
    "mono_bold": ("Consolas", 9, "bold"),
    "log": ("Consolas", 8),
}


def gradient_color(t: float) -> str:
    """
    Calcula a cor de alto contraste para o fundo branco (White Mode) ao longo do progresso t in [0, 1].
    Transição em duas etapas:
      0.0 -> 0.5: Verde Esmeralda (#059669) -> Azul Royal (#2563EB)
      0.5 -> 1.0: Azul Royal (#2563EB) -> Roxo Índigo (#7C3AED)
    """
    t = max(0.0, min(1.0, float(t)))
    if t < 0.5:
        u = t * 2.0
        r = int(0x05 + u * (0x25 - 0x05))
        g = int(0x96 + u * (0x63 - 0x96))
        b = int(0x69 + u * (0xEB - 0x69))
    else:
        u = (t - 0.5) * 2.0
        r = int(0x25 + u * (0x7C - 0x25))
        g = int(0x63 + u * (0x3A - 0x63))
        b = int(0xEB + u * (0xED - 0xEB))
    return f"#{r:02x}{g:02x}{b:02x}"

