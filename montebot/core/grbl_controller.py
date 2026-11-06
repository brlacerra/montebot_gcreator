"""
Controlador e Enviador Nativo de G-code para GRBL (v0.9 / v1.1+) via Porta Serial (COM).
Implementa:
  - Descoberta dinâmica de portas COM + Simulador GRBL 1.1h Virtual integrado
  - Protocolo oficial de streaming Character-Counting (buffer RX de 128 bytes)
  - Polling de telemetria em tempo real ('?' a 5 Hz) com parser de MPos, WPos e WCO
  - Comandos de tempo real ('!' Feed Hold, '~' Cycle Start/Resume, Ctrl-X Soft Reset)
  - Jogging manual (X, Y, Z), Zeramento de coordenadas (G10/G92), Homing ($H) e MDI
"""

from collections import deque
from dataclasses import dataclass
import math
import re
import threading
import time
from typing import Callable, Deque, List, Optional, Tuple

import serial
import serial.tools.list_ports

from montebot.config import GRBL_POLL_INTERVAL_SEC, GRBL_RX_BUFFER_SIZE, SIMULATOR_PORT


@dataclass
class GrblStatus:
    """Fotografia imutável do estado atual da máquina GRBL e do streaming."""
    connected: bool = False
    port: str = ""
    state: str = "DISCONNECTED"
    wpos: Tuple[float, float, float] = (0.0, 0.0, 5.0)
    mpos: Tuple[float, float, float] = (0.0, 0.0, 5.0)
    feed_rate: float = 0.0
    streaming: bool = False
    paused: bool = False
    sent_lines: int = 0
    ack_lines: int = 0
    total_lines: int = 0
    elapsed_sec: float = 0.0
    eta_sec: float = 0.0

    @property
    def progress_pct(self) -> float:
        if self.total_lines <= 0:
            return 0.0
        return min(100.0, (self.ack_lines / float(self.total_lines)) * 100.0)


class _VirtualGrblDevice:
    """
    Simulador em memória de uma placa GRBL 1.1h para testes completos sem hardware conectado.
    Emula respostas 'ok', relatórios '<Idle|MPos:...|WCO:...>', interpolação de movimento,
    pausa ('!'), retomada ('~') e reset ('\\x18').
    """

    def __init__(self):
        self.is_open = True
        self._rx_buf = bytearray()
        self._tx_queue: Deque[bytes] = deque()
        self._lock = threading.Lock()

        self.state = "Idle"
        self.mpos = [0.0, 0.0, 5.0]
        self.wco = [0.0, 0.0, 0.0]
        self.target_mpos = [0.0, 0.0, 5.0]
        self.relative_mode = False
        self.feed_rate = 1000.0
        self._last_step_time = time.perf_counter()

        self._enqueue_line("Grbl 1.1h ['$' for help]")

    def _enqueue_line(self, text: str):
        self._tx_queue.append((text + "\r\n").encode("utf-8"))

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        with self._lock:
            self._tx_queue.clear()

    def write(self, data: bytes):
        with self._lock:
            self._advance_physics()
            for b in data:
                ch = chr(b)
                if ch == "?":
                    wpos = [self.mpos[i] - self.wco[i] for i in range(3)]
                    st = self.state
                    if st == "Run" and self._dist_to_target() < 1e-3:
                        st = "Idle"
                        self.state = "Idle"
                    report = (
                        f"<{st}|MPos:{self.mpos[0]:.3f},{self.mpos[1]:.3f},{self.mpos[2]:.3f}"
                        f"|WPos:{wpos[0]:.3f},{wpos[1]:.3f},{wpos[2]:.3f}"
                        f"|FS:{int(self.feed_rate if st == 'Run' else 0)},0"
                        f"|WCO:{self.wco[0]:.3f},{self.wco[1]:.3f},{self.wco[2]:.3f}>"
                    )
                    self._enqueue_line(report)
                elif ch == "!":
                    if self.state in ("Run", "Jog"):
                        self.state = "Hold"
                elif ch == "~":
                    if self.state == "Hold":
                        self.state = "Run" if self._dist_to_target() > 1e-3 else "Idle"
                elif b == 0x18:
                    self.state = "Idle"
                    self.target_mpos = list(self.mpos)
                    self._rx_buf.clear()
                    self._enqueue_line("Grbl 1.1h ['$' for help]")
                elif ch in ("\r", "\n"):
                    line = self._rx_buf.decode("utf-8", errors="ignore").strip()
                    self._rx_buf.clear()
                    if line:
                        self._process_command(line)
                else:
                    self._rx_buf.append(b)

    def _dist_to_target(self) -> float:
        return math.sqrt(sum((self.target_mpos[i] - self.mpos[i]) ** 2 for i in range(3)))

    def _advance_physics(self):
        now = time.perf_counter()
        dt = max(0.0, now - self._last_step_time)
        self._last_step_time = now
        if self.state == "Hold":
            return
        dist = self._dist_to_target()
        if dist <= 1e-4:
            self.mpos = list(self.target_mpos)
            if self.state in ("Run", "Jog"):
                self.state = "Idle"
            return

        # No simulador, move rapidamente para permitir testar arquivos grandes com fluidez
        speed_mm_s = max(80.0, (self.feed_rate / 60.0) * 8.0)
        step = speed_mm_s * dt
        if step >= dist:
            self.mpos = list(self.target_mpos)
            self.state = "Idle"
        else:
            ratio = step / dist
            for i in range(3):
                self.mpos[i] += (self.target_mpos[i] - self.mpos[i]) * ratio

    def _process_command(self, line: str):
        up = line.upper()
        if up == "$$":
            self._enqueue_line("$100=80.000 (X-axis travel resolution, step/mm)")
            self._enqueue_line("$101=80.000 (Y-axis travel resolution, step/mm)")
            self._enqueue_line("$102=400.000 (Z-axis travel resolution, step/mm)")
            self._enqueue_line("$110=3000.000 (X-axis maximum rate, mm/min)")
            self._enqueue_line("$111=3000.000 (Y-axis maximum rate, mm/min)")
            self._enqueue_line("ok")
            return
        if up in ("$X", "$H"):
            self.state = "Idle"
            if up == "$H":
                self.mpos = [0.0, 0.0, 5.0]
                self.target_mpos = [0.0, 0.0, 5.0]
            self._enqueue_line("ok")
            return

        if "G91" in up:
            self.relative_mode = True
        elif "G90" in up:
            self.relative_mode = False

        # Comandos de zeramento G92 ou G10 P0 L20
        if "G92" in up or "G10" in up:
            for idx, axis in enumerate(("X", "Y", "Z")):
                m = re.search(rf"{axis}\s*([-+]?\d*\.?\d+)", up)
                if m:
                    val = float(m.group(1))
                    self.wco[idx] = self.mpos[idx] - val
            self._enqueue_line("ok")
            return

        f_m = re.search(r"F\s*([-+]?\d*\.?\d+)", up)
        if f_m:
            self.feed_rate = max(10.0, float(f_m.group(1)))

        moved = False
        for idx, axis in enumerate(("X", "Y", "Z")):
            m = re.search(rf"{axis}\s*([-+]?\d*\.?\d+)", up)
            if m:
                val = float(m.group(1))
                if self.relative_mode or up.startswith("$J="):
                    self.target_mpos[idx] = self.mpos[idx] + val
                else:
                    self.target_mpos[idx] = val + self.wco[idx]
                moved = True

        if moved:
            # Em streaming contínuo, avança parcialmente para simular execução suave
            for i in range(3):
                self.mpos[i] = self.mpos[i] + (self.target_mpos[i] - self.mpos[i]) * 0.65
            self.state = "Run"
        if up.startswith("$J="):
            self.relative_mode = False

        self._enqueue_line("ok")

    @property
    def in_waiting(self) -> int:
        with self._lock:
            self._advance_physics()
            return sum(len(x) for x in self._tx_queue)

    def readline(self) -> bytes:
        with self._lock:
            self._advance_physics()
            if self._tx_queue:
                return self._tx_queue.popleft()
            return b""


class GrblController:
    """
    Gerenciador assíncrono de conexão e envio de G-code para GRBL.
    Executa leitura serial, contagem de buffer de 128 bytes e polling '?' em thread dedicada.
    """

    def __init__(
        self,
        on_status_change: Optional[Callable[[GrblStatus], None]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_stream_complete: Optional[Callable[[bool, str], None]] = None,
    ):
        self.on_status_change = on_status_change
        self.on_log = on_log
        self.on_stream_complete = on_stream_complete

        self._serial = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None

        # Estado interno
        self._connected = False
        self._port = ""
        self._state = "DISCONNECTED"
        self._mpos = [0.0, 0.0, 5.0]
        self._wpos = [0.0, 0.0, 5.0]
        self._wco = [0.0, 0.0, 0.0]
        self._feed_rate = 0.0

        # Estado do Streaming Character-Counting (Buffer de 128 bytes do GRBL)
        self._streaming = False
        self._paused = False
        self._stream_lines: List[str] = []
        self._in_flight_lengths: Deque[int] = deque()
        self._manual_queue: Deque[str] = deque()
        self._manual_in_flight: int = 0
        self._sent_idx = 0
        self._ack_count = 0
        self._stream_start_time = 0.0

    # ─────────────────────────────────────────────
    #  DESCOBERTA DE PORTAS E CONEXÃO
    # ─────────────────────────────────────────────

    @staticmethod
    def list_ports() -> List[Tuple[str, str]]:
        """
        Retorna lista de tuplas (porta, descrição) com todas as portas COM ativas + Simulador.
        Prioriza automaticamente conversores USB-Serial / Arduino (CH340, CP210x, FTDI, USB)
        no topo da lista antes de portas Bluetooth virtuais.
        """
        usb_ports: List[Tuple[str, str]] = []
        other_ports: List[Tuple[str, str]] = []
        try:
            for p in serial.tools.list_ports.comports():
                desc = f"{p.device} — {p.description}" if p.description else p.device
                low_desc = desc.lower()
                is_usb_cnc = any(
                    k in low_desc
                    for k in ("ch340", "ch341", "cp210", "ftdi", "arduino", "usb-serial", "usb serial")
                )
                if is_usb_cnc:
                    usb_ports.append((p.device, desc))
                else:
                    other_ports.append((p.device, desc))
        except Exception:
            pass
        ports = usb_ports + other_ports
        ports.append((SIMULATOR_PORT, "🧪 SIMULADOR GRBL (Teste Virtual sem USB)"))
        return ports

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_streaming(self) -> bool:
        return self._streaming

    def get_status(self) -> GrblStatus:
        elapsed = (time.perf_counter() - self._stream_start_time) if self._streaming else 0.0
        eta = 0.0
        total = len(self._stream_lines)
        if self._streaming and self._ack_count > 5 and total > self._ack_count:
            rate = self._ack_count / max(0.1, elapsed)
            eta = (total - self._ack_count) / max(0.1, rate)

        return GrblStatus(
            connected=self._connected,
            port=self._port,
            state=self._state,
            wpos=(self._wpos[0], self._wpos[1], self._wpos[2]),
            mpos=(self._mpos[0], self._mpos[1], self._mpos[2]),
            feed_rate=self._feed_rate,
            streaming=self._streaming,
            paused=self._paused,
            sent_lines=self._sent_idx,
            ack_lines=self._ack_count,
            total_lines=total,
            elapsed_sec=elapsed,
            eta_sec=eta,
        )

    def connect(self, port: str, baudrate: int = 115200):
        """Abre a porta serial especificada (ou o simulador virtual) e inicia a thread de comunicação."""
        if self._connected:
            self.disconnect()

        self._stop_event.clear()
        if port == SIMULATOR_PORT:
            self._serial = _VirtualGrblDevice()
            self._log_msg("🔌 Conectado ao Simulador GRBL 1.1h Virtual.")
        else:
            self._log_msg(f"🔌 Abrindo porta {port} @ {baudrate} bps...")
            ser = serial.Serial()
            ser.port = port
            ser.baudrate = int(baudrate)
            ser.timeout = 0.05
            ser.write_timeout = 0.5
            ser.dtr = True
            ser.open()
            self._serial = ser
            # Sequência padrão de wake-up do GRBL
            time.sleep(0.3)
            ser.write(b"\r\n\r\n")

        self._connected = True
        self._port = port
        self._state = "IDLE"
        self._in_flight_lengths.clear()
        self._manual_queue.clear()
        self._manual_in_flight = 0

        self._worker_thread = threading.Thread(target=self._io_loop, daemon=True)
        self._worker_thread.start()
        self._emit_status()

    def disconnect(self):
        """Encerra o streaming se ativo e fecha a porta serial com segurança."""
        self._stop_event.set()
        self._streaming = False
        self._paused = False
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=1.0)

        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

        self._connected = False
        self._port = ""
        self._state = "DISCONNECTED"
        self._in_flight_lengths.clear()
        self._manual_queue.clear()
        self._manual_in_flight = 0
        self._log_msg("🔌 Porta serial desconectada.")
        self._emit_status()

    # ─────────────────────────────────────────────
    #  COMANDOS MANUAIS, JOGGING E TEMPO REAL
    # ─────────────────────────────────────────────

    def send_realtime(self, char_cmd: bytes):
        """Envia um byte imediato de tempo real ('?', '!', '~', '\\x18') diretamente ao GRBL."""
        if not self._connected or self._serial is None:
            return
        try:
            with self._lock:
                self._serial.write(char_cmd)
        except Exception as exc:
            self._log_msg(f"⚠ Erro ao enviar comando imediato: {exc}")

    def send_mdi(self, command: str):
        """Enfileira um ou mais comandos G-code/GRBL manuais (ex: '$$', '$X', 'G0 X0 Y0')."""
        if not self._connected:
            self._log_msg("⚠ Conecte-se a uma porta COM antes de enviar comandos.")
            return
        if self._streaming and not self._paused:
            self._log_msg("⚠ Pause ou aguarde o término do envio antes de enviar comandos manuais.")
            return

        for sub in command.splitlines():
            clean = sub.strip()
            if clean:
                self._log_msg(f"» {clean}")
                self._manual_queue.append(clean)

    def jog(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, feed_rate: float = 1500.0):
        """Realiza movimento manual incremental (Jog) compatível com GRBL 0.9 e 1.1."""
        if not self._connected or (self._streaming and not self._paused):
            return
        parts = ["G91", "G21", "G1"]
        if abs(dx) > 1e-5:
            parts.append(f"X{dx:.3f}")
        if abs(dy) > 1e-5:
            parts.append(f"Y{dy:.3f}")
        if abs(dz) > 1e-5:
            parts.append(f"Z{dz:.3f}")
        parts.append(f"F{int(max(50, feed_rate))}")

        self._manual_queue.append(" ".join(parts))
        self._manual_queue.append("G90")

    def zero_axes(self, x: bool = True, y: bool = True, z: bool = False):
        """Zera as coordenadas de trabalho (WPos) nos eixos selecionados usando G10 L20 P0 e G92."""
        axes = []
        if x:
            axes.append("X0")
        if y:
            axes.append("Y0")
        if z:
            axes.append("Z0")
        if not axes:
            return
        ax_str = " ".join(axes)
        self.send_mdi(f"G10 P0 L20 {ax_str}\nG92 {ax_str}")

    def go_to_zero(self, lift_height: float = 5.0):
        """Levanta o eixo Z por segurança e move para a origem de trabalho (X0 Y0)."""
        self.send_mdi(f"G90\nG0 Z{lift_height:.2f}\nG0 X0 Y0")

    def unlock_alarm(self):
        """Envia $X para destravar estado de ALARM no GRBL."""
        self.send_mdi("$X")

    def home_machine(self):
        """Envia $H para executar ciclo de Homing."""
        self.send_mdi("$H")

    def soft_reset(self):
        """Envia Ctrl-X (0x18) para parada imediata e reset do GRBL."""
        self._streaming = False
        self._paused = False
        self._in_flight_lengths.clear()
        self._manual_queue.clear()
        self._manual_in_flight = 0
        self.send_realtime(b"\x18")
        self._log_msg("🛑 Soft Reset (Ctrl-X) enviado ao GRBL!")
        self._emit_status()

    # ─────────────────────────────────────────────
    #  CONTROLE DE STREAMING DE ARQUIVO G-CODE
    # ─────────────────────────────────────────────

    def start_stream(self, gcode_path: str):
        """Carrega um arquivo .gcode e inicia o envio contínuo via Character-Counting."""
        if not self._connected:
            raise RuntimeError("Conecte-se a uma porta COM antes de iniciar o envio.")
        if self._streaming:
            return

        clean_lines: List[str] = []
        with open(gcode_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                # Remove comentários e espaços extras para economizar buffer serial do GRBL
                line = raw.split(";")[0].strip()
                line = re.sub(r"\(.*?\)", "", line).strip()
                if line:
                    clean_lines.append(line)

        if not clean_lines:
            raise RuntimeError("O arquivo G-code não contém instruções executáveis.")

        self._stream_lines = clean_lines
        self._sent_idx = 0
        self._ack_count = 0
        self._in_flight_lengths.clear()
        self._manual_queue.clear()
        self._manual_in_flight = 0
        self._paused = False
        self._streaming = True
        self._stream_start_time = time.perf_counter()

        self._log_msg(f"▶ Iniciando envio de G-code ({len(clean_lines):,} blocos)...")
        self._emit_status()

    def pause_stream(self):
        """Pausa o envio e envia comando imediato Feed Hold ('!') ao GRBL."""
        if not self._streaming or self._paused:
            return
        self._paused = True
        self.send_realtime(b"!")
        self._log_msg("⏸ Execução pausada (Feed Hold '!').")
        self._emit_status()

    def resume_stream(self):
        """Retoma o envio e envia comando imediato Cycle Start ('~') ao GRBL."""
        if not self._streaming or not self._paused:
            return
        self._paused = False
        self.send_realtime(b"~")
        self._log_msg("⏯ Execução retomada (Cycle Start '~').")
        self._emit_status()

    def stop_stream(self):
        """Interrompe imediatamente o envio e envia Soft Reset ao GRBL."""
        was_streaming = self._streaming
        self.soft_reset()
        if was_streaming and self.on_stream_complete:
            self.on_stream_complete(False, "Envio cancelado pelo usuário.")

    # ─────────────────────────────────────────────
    #  LOOP DE COMUNICAÇÃO SERIAL E PARSER GRBL
    # ─────────────────────────────────────────────

    def _io_loop(self):
        last_poll = 0.0

        while not self._stop_event.is_set() and self._connected:
            now = time.perf_counter()

            # 1. Polling periódico de status ('?') a 5 Hz
            if now - last_poll >= GRBL_POLL_INTERVAL_SEC:
                self.send_realtime(b"?")
                last_poll = now

            # 2. Leitura de linhas recebidas do GRBL
            lines_read = 0
            while lines_read < 50:
                raw_line = b""
                try:
                    with self._lock:
                        if self._serial and self._serial.in_waiting > 0:
                            raw_line = self._serial.readline()
                except Exception as exc:
                    self._log_msg(f"✗ Perda de conexão serial: {exc}")
                    self._connected = False
                    self._state = "DISCONNECTED"
                    self._emit_status()
                    return

                if not raw_line:
                    break

                lines_read += 1
                text = raw_line.decode("utf-8", errors="ignore").strip()
                if text:
                    self._handle_incoming_line(text)

            # 3. Envio de comandos manuais (MDI / Jog) quando não está em streaming ativo
            if self._manual_queue and self._manual_in_flight == 0:
                cmd = self._manual_queue.popleft()
                try:
                    with self._lock:
                        if self._serial:
                            self._serial.write((cmd + "\n").encode("ascii", errors="ignore"))
                            self._manual_in_flight += 1
                except Exception as exc:
                    self._log_msg(f"✗ Erro de escrita serial: {exc}")

            # 4. Streaming de G-code via Character-Counting (Buffer de 128 bytes)
            if self._streaming and not self._paused:
                self._fill_grbl_buffer()

            time.sleep(0.004)

    def _fill_grbl_buffer(self):
        """Preenche o buffer RX de 128 bytes do GRBL enquanto houver espaço disponível."""
        total = len(self._stream_lines)
        while self._sent_idx < total and not self._paused:
            next_line = self._stream_lines[self._sent_idx]
            line_len = len(next_line) + 1  # +1 para o '\n'
            bytes_in_flight = sum(self._in_flight_lengths)

            if bytes_in_flight + line_len > GRBL_RX_BUFFER_SIZE and self._in_flight_lengths:
                break

            try:
                with self._lock:
                    if self._serial:
                        self._serial.write((next_line + "\n").encode("ascii", errors="ignore"))
                self._in_flight_lengths.append(line_len)
                self._sent_idx += 1
            except Exception as exc:
                self._log_msg(f"✗ Erro durante streaming serial: {exc}")
                self._paused = True
                break

    def _handle_incoming_line(self, line: str):
        """Processa respostas 'ok', 'error:', 'ALARM:' e relatórios '<...>' do GRBL."""
        # 1. Relatório de status em tempo real: <Idle|MPos:0.000,0.000,0.000|...>
        if line.startswith("<") and line.endswith(">"):
            self._parse_status_report(line[1:-1])
            self._emit_status()
            return

        low = line.lower()

        # 2. Confirmação de comando ('ok' ou 'error:')
        if low == "ok" or low.startswith("error:"):
            if low.startswith("error:"):
                self._log_msg(f"⚠ GRBL {line}")

            if self._manual_in_flight > 0:
                self._manual_in_flight = max(0, self._manual_in_flight - 1)
            elif self._streaming and self._in_flight_lengths:
                self._in_flight_lengths.popleft()
                self._ack_count += 1

                # Verifica se o arquivo inteiro foi concluído e confirmado pelo GRBL
                if self._ack_count >= len(self._stream_lines):
                    elapsed = time.perf_counter() - self._stream_start_time
                    self._streaming = False
                    self._paused = False
                    msg = f"✓ Envio concluído! ({self._ack_count:,} linhas em {elapsed:.1f}s)"
                    self._log_msg(msg)
                    self._emit_status()
                    if self.on_stream_complete:
                        self.on_stream_complete(True, msg)
                    return

            self._emit_status()
            return

        # 3. Alarme ou mensagens de inicialização do GRBL
        if low.startswith("alarm:"):
            self._state = "ALARM"
            self._paused = True
            self._log_msg(f"🚨 {line} (Clique em 'Destravar $X' para liberar)")
            self._emit_status()
            return

        self._log_msg(f"← {line}")

    def _parse_status_report(self, content: str):
        """Suporta formatos GRBL 1.1 (separado por '|') e GRBL 0.9 (separado por ',')."""
        if "|" in content:
            fields = content.split("|")
            raw_state = fields[0].split(":")[0].upper()
            self._state = raw_state

            has_wpos = False
            for field in fields[1:]:
                if field.startswith("MPos:"):
                    coords = [float(x) for x in field[5:].split(",")[:3]]
                    if len(coords) == 3:
                        self._mpos = coords
                elif field.startswith("WPos:"):
                    coords = [float(x) for x in field[5:].split(",")[:3]]
                    if len(coords) == 3:
                        self._wpos = coords
                        has_wpos = True
                elif field.startswith("WCO:"):
                    coords = [float(x) for x in field[4:].split(",")[:3]]
                    if len(coords) == 3:
                        self._wco = coords
                elif field.startswith("FS:"):
                    parts = field[3:].split(",")
                    if parts:
                        self._feed_rate = float(parts[0])

            if not has_wpos:
                self._wpos = [self._mpos[i] - self._wco[i] for i in range(3)]
        else:
            # Formato legado GRBL 0.9: Idle,MPos:0.000,0.000,0.000,WPos:0.000,0.000,0.000
            state_m = re.match(r"^([A-Za-z]+)", content)
            if state_m:
                self._state = state_m.group(1).upper()
            mpos_m = re.search(r"MPos:([-+]?\d*\.?\d+),([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)", content)
            if mpos_m:
                self._mpos = [float(mpos_m.group(1)), float(mpos_m.group(2)), float(mpos_m.group(3))]
            wpos_m = re.search(r"WPos:([-+]?\d*\.?\d+),([-+]?\d*\.?\d+),([-+]?\d*\.?\d+)", content)
            if wpos_m:
                self._wpos = [float(wpos_m.group(1)), float(wpos_m.group(2)), float(wpos_m.group(3))]
            else:
                self._wpos = [self._mpos[i] - self._wco[i] for i in range(3)]

    def _log_msg(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def _emit_status(self):
        if self.on_status_change:
            self.on_status_change(self.get_status())
