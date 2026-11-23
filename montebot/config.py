"""
Configurações globais, constantes de máquina e paleta White Mode do MonteBot GCreator.
"""

from typing import Dict, Tuple

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
    "z_mode": "servo",         # 'servo' (Porta Z+ / M03 S...) | 'stepper' (Motor de passo G0/G1 Z...)
    "feed_rate": "1000",       # Velocidade de desenho/corte (mm/min)
    "plunge_rate": "600",      # Velocidade de descida Z para motor de passo (mm/min)
    "lift_height": "5.0",      # Altura Z levantado / viagem para motor de passo (mm)
    "cut_depth": "-1.0",       # Altura/profundidade Z de desenho para motor de passo (mm)
    "servo_up": "250",         # Valor PWM do servo para caneta levantada (M03 S250)
    "servo_down": "0",         # Valor PWM do servo para caneta abaixada (M03 S0)
    "servo_delay": "0.15",     # Pausa G4 P<seg> após acionar o servo (0 para desativar)
    "tolerance": "0.2mm",      # Tolerância de união de linhas (linemerge)
    "simplify_tol": "0.05mm",  # Tolerância de simplificação de micro-segmentos (linesimplify)
    "smooth_level": "0.35",    # Suavização anti-vibração em mm (0 para desativar)
    "draw_mode": "normal",     # 'normal' | 'fill' | 'text'
    "fill_enabled": False,     # Ativar preenchimento de áreas/traços fechados
    "fill_pen_width": "0.5",   # Largura efetiva da linha/caneta em mm
    "fill_mode": "after_outline",  # 'after_outline' | 'while_drawing'
    "fill_pattern": "hybrid",  # 'horizontal' | 'contour' | 'hybrid'
    "text_centerline": True,   # Extrair linha central (esqueleto) de letras ocas
    "text_order": "top_to_bottom",  # 'top_to_bottom' (Cima->Baixo) | 'bottom_to_top' (Baixo->Cima)
    "text_font": "futural",    # Fonte single-stroke p/ tags <text>: 'futural' | 'scripts' | 'timesr'
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
GRBL_RX_BUFFER_SIZE = 120        # Tamanho seguro do buffer de recepção do GRBL (margem de segurança p/ 128 bytes)
GRBL_POLL_INTERVAL_SEC = 0.20    # Intervalo de polling '?' (5 Hz)
SIMULATOR_PORT = "SIMULATOR"

# Metadados dos parâmetros de firmware GRBL v1.1 ($0 a $132)
# Formato: code -> (nome_curto_ptbr, unidade, descricao_detalhada_ptbr)
GRBL_SETTINGS_METADATA: Dict[int, Tuple[str, str, str]] = {
    0: ("Pulso de passo", "µs", "Duração do pulso de passo enviado aos drivers (padrão: 10 µs)."),
    1: ("Atraso de bloqueio", "ms", "Tempo para desabilitar motores após parada (255 mantém sempre ligado)."),
    2: ("Inversão de pulso", "máscara", "Máscara de bits para inverter polaridade do sinal de Step."),
    3: ("Inversão de direção", "máscara", "Máscara de bits (X=1, Y=2, Z=4) para inverter direção dos eixos."),
    4: ("Inverter pino Enable", "bool", "Inverte lógica do pino Step Enable (0 = normal, 1 = invertido)."),
    5: ("Inverter pinos de limite", "bool", "Inverte lógica das chaves de fim de curso (0 = NA, 1 = NF)."),
    6: ("Inverter pino Probe", "bool", "Inverte lógica do pino de sonda Z-Probe (0 = normal, 1 = invertido)."),
    10: ("Relatório de status", "máscara", "Define quais dados são enviados no relatório '?' (MPos/WPos/Buffer)."),
    11: ("Desvio de junção", "mm", "Controla velocidade nas curvas; valores menores deixam cantos mais precisos."),
    12: ("Tolerância de arco", "mm", "Precisão de segmentação para arcos G2/G3."),
    13: ("Relatar em polegadas", "bool", "Define unidade dos relatórios de posição (0 = mm, 1 = polegadas)."),
    20: ("Limites de software", "bool", "Impede movimentos fora do curso máximo ($130-$132) após Homing."),
    21: ("Limites físicos (Hard)", "bool", "Ativa parada de emergência imediata pelas chaves de fim de curso."),
    22: ("Ciclo de Homing ($H)", "bool", "Habilita busca de origem automática via comando $H."),
    23: ("Inversão direção Homing", "máscara", "Inverte direção da busca de Homing por eixo (X=1, Y=2, Z=4)."),
    24: ("Velocidade fina Homing", "mm/min", "Velocidade lenta de precisão na segunda leitura das chaves."),
    25: ("Velocidade busca Homing", "mm/min", "Velocidade rápida inicial para encontrar as chaves de fim de curso."),
    26: ("Debounce Homing", "ms", "Atraso de filtragem de ruído nas chaves de fim de curso."),
    27: ("Recuo Homing (Pull-off)", "mm", "Distância de recuo após acionar a chave de fim de curso."),
    30: ("PWM / Spindle máximo", "RPM / PWM", "Valor máximo de S para 100% do sinal PWM (Spindle/Servo)."),
    31: ("PWM / Spindle mínimo", "RPM / PWM", "Valor mínimo de S para o sinal PWM."),
    32: ("Modo Laser", "bool", "Ativa controle contínuo de PWM sem paradas (0 = Desligado, 1 = Ligado)."),
    100: ("Resolução Eixo X", "passos/mm", "Quantidade de passos necessários para mover 1 mm no eixo X."),
    101: ("Resolução Eixo Y", "passos/mm", "Quantidade de passos necessários para mover 1 mm no eixo Y."),
    102: ("Resolução Eixo Z", "passos/mm", "Quantidade de passos necessários para mover 1 mm no eixo Z."),
    110: ("Velocidade máx. X", "mm/min", "Taxa de avanço máxima permitida para o eixo X (G0 / G1)."),
    111: ("Velocidade máx. Y", "mm/min", "Taxa de avanço máxima permitida para o eixo Y (G0 / G1)."),
    112: ("Velocidade máx. Z", "mm/min", "Taxa de avanço máxima permitida para o eixo Z (G0 / G1)."),
    120: ("Aceleração Eixo X", "mm/s²", "Aceleração máxima do eixo X para partidas e frenagens suaves."),
    121: ("Aceleração Eixo Y", "mm/s²", "Aceleração máxima do eixo Y para partidas e frenagens suaves."),
    122: ("Aceleração Eixo Z", "mm/s²", "Aceleração máxima do eixo Z para partidas e frenagens suaves."),
    130: ("Curso máximo X", "mm", "Comprimento útil máximo do eixo X (usado em Soft Limits e Homing)."),
    131: ("Curso máximo Y", "mm", "Comprimento útil máximo do eixo Y (usado em Soft Limits e Homing)."),
    132: ("Curso máximo Z", "mm", "Comprimento útil máximo do eixo Z (usado em Soft Limits e Homing)."),
}

# Valores iniciais padrão para o Simulador Virtual GRBL 1.1h
GRBL_DEFAULT_VIRTUAL_SETTINGS: Dict[int, str] = {
    0: "10",
    1: "25",
    2: "0",
    3: "0",
    4: "0",
    5: "0",
    6: "0",
    10: "1",
    11: "0.010",
    12: "0.002",
    13: "0",
    20: "0",
    21: "0",
    22: "0",
    23: "0",
    24: "25.000",
    25: "500.000",
    26: "250",
    27: "1.000",
    30: "1000",
    31: "0",
    32: "0",
    100: "80.000",
    101: "80.000",
    102: "400.000",
    110: "3000.000",
    111: "3000.000",
    112: "1000.000",
    120: "150.000",
    121: "150.000",
    122: "50.000",
    130: "250.000",
    131: "320.000",
    132: "50.000",
}

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

