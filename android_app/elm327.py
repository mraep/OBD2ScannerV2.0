"""
Klien ELM327 minimal: kirim perintah AT/OBD lewat objek transport (Wifi/
Bluetooth/USB dari obd_transport.py) dan parse response mode 01 dasar.
Dibuat dari nol (bukan pakai library python-obd seperti versi desktop) supaya
bisa jalan seragam di atas transport non-pyserial (Bluetooth classic Android,
USB4A), yang tidak kompatibel langsung dengan python-obd.
"""
import time

# name: (pid_hex, formula(data_bytes)->nilai, satuan)
PIDS = {
    "RPM":          ("010C", lambda d: ((d[0] * 256) + d[1]) / 4.0, "rpm"),
    "SPEED":        ("010D", lambda d: d[0], "km/h"),
    "COOLANT_TEMP": ("0105", lambda d: d[0] - 40, "°C"),
    "ENGINE_LOAD":  ("0104", lambda d: d[0] * 100 / 255.0, "%"),
    "THROTTLE_POS": ("0111", lambda d: d[0] * 100 / 255.0, "%"),
    "INTAKE_TEMP":  ("010F", lambda d: d[0] - 40, "°C"),
    "FUEL_LEVEL":   ("012F", lambda d: d[0] * 100 / 255.0, "%"),
}

# Urutan tampil di Live Monitor (kartu dibuat sesuai urutan ini)
MONITOR_ORDER = [
    "RPM", "SPEED", "COOLANT_TEMP", "ENGINE_LOAD",
    "THROTTLE_POS", "INTAKE_TEMP", "FUEL_LEVEL",
]

DTC_TYPE_PREFIX = ["P", "C", "B", "U"]


class ELM327Client:
    def __init__(self, transport):
        self.transport = transport

    def connect(self):
        self.transport.connect()
        self._init_adapter()

    def _send_raw(self, cmd, wait=0.3):
        self.transport.write((cmd + "\r").encode())
        time.sleep(wait)
        raw = self.transport.read_until(b">", overall_timeout=5)
        return raw.decode(errors="ignore")

    def _init_adapter(self):
        # Urutan inisialisasi standar ELM327: reset, matikan echo, matikan
        # linefeed, matikan spasi, matikan header, auto-detect protokol.
        for cmd in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"):
            self._send_raw(cmd, wait=0.5)

    def query(self, name):
        if name not in PIDS:
            raise ValueError(f"PID '{name}' tidak dikenal.")
        pid_hex, formula, unit = PIDS[name]
        resp = self._send_raw(pid_hex)
        data = self._parse_hex_response(resp, pid_hex)
        if data is None:
            return None
        try:
            value = formula(data)
        except Exception:
            return None
        return value, unit

    def read_voltage(self):
        """ATRV: baca tegangan langsung dari firmware ELM327 (bukan PID mode01)."""
        resp = self._send_raw("ATRV")
        for token in resp.replace("\r", " ").split():
            token = token.replace("V", "").strip()
            try:
                return float(token)
            except ValueError:
                continue
        return None

    @staticmethod
    def _parse_hex_response(resp, pid_hex):
        # Response mode 01 biasanya: "41 0C 1A F8"
        # (41 = mode 01 + 0x40, lalu echo PID, lalu byte data)
        mode_echo = f"{int(pid_hex[:2], 16) + 0x40:02X}"
        pid_echo = pid_hex[2:].upper()
        for line in resp.replace("\r", "\n").splitlines():
            tokens = line.strip().split()
            hex_tokens = [t for t in tokens if len(t) == 2 and all(c in "0123456789ABCDEFabcdef" for c in t)]
            if len(hex_tokens) >= 2 and hex_tokens[0].upper() == mode_echo and hex_tokens[1].upper() == pid_echo:
                data_bytes = [int(t, 16) for t in hex_tokens[2:]]
                if data_bytes:
                    return data_bytes
        return None

    def read_dtc(self):
        """Mode 03: baca daftar kode DTC yang tersimpan (format P0xxx/C0xxx/dst)."""
        resp = self._send_raw("03", wait=1.0)
        return self._decode_dtc_response(resp, mode_echo="43")

    def clear_dtc(self):
        """Mode 04: hapus semua DTC + matikan lampu Check Engine (MIL)."""
        resp = self._send_raw("04", wait=1.0)
        return "OK" in resp.upper() or "44" in resp.upper()

    @staticmethod
    def _decode_dtc_response(resp, mode_echo):
        all_bytes = []
        for line in resp.replace("\r", "\n").splitlines():
            tokens = line.strip().split()
            hex_tokens = [t for t in tokens if len(t) == 2 and all(c in "0123456789ABCDEFabcdef" for c in t)]
            if hex_tokens and hex_tokens[0].upper() == mode_echo:
                all_bytes.extend(int(t, 16) for t in hex_tokens[1:])

        codes = []
        for i in range(0, len(all_bytes) - 1, 2):
            b1, b2 = all_bytes[i], all_bytes[i + 1]
            if b1 == 0 and b2 == 0:
                continue
            type_char = DTC_TYPE_PREFIX[(b1 >> 6) & 0x03]
            digit1 = (b1 >> 4) & 0x03
            digit2 = b1 & 0x0F
            digit3 = (b2 >> 4) & 0x0F
            digit4 = b2 & 0x0F
            codes.append(f"{type_char}{digit1}{digit2:X}{digit3:X}{digit4:X}")
        return codes

    def close(self):
        self.transport.close()
