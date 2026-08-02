"""
Klien ELM327 minimal: kirim perintah AT/OBD lewat objek transport (Wifi/
Bluetooth/USB dari obd_transport.py) dan parse response mode 01/02/03/04.
Dibuat dari nol (bukan pakai library python-obd seperti versi desktop) supaya
bisa jalan seragam di atas transport non-pyserial (Bluetooth classic Android,
USB4A), yang tidak kompatibel langsung dengan python-obd.
"""
import time

# name: (pid_hex, formula(data_bytes)->nilai, satuan) - dipakai untuk Live
# Monitor (mode 01, data live) MAUPUN Freeze Frame (mode 02, snapshot saat
# DTC terakhir muncul) - PID-nya sama, cuma mode request yang beda.
PIDS = {
    "RPM":          ("010C", lambda d: ((d[0] * 256) + d[1]) / 4.0, "rpm"),
    "SPEED":        ("010D", lambda d: d[0], "km/h"),
    "COOLANT_TEMP": ("0105", lambda d: d[0] - 40, "°C"),
    "ENGINE_LOAD":  ("0104", lambda d: d[0] * 100 / 255.0, "%"),
    "THROTTLE_POS": ("0111", lambda d: d[0] * 100 / 255.0, "%"),
    "INTAKE_TEMP":  ("010F", lambda d: d[0] - 40, "°C"),
    "FUEL_LEVEL":   ("012F", lambda d: d[0] * 100 / 255.0, "%"),
}

# Urutan tampil di Live Monitor & Freeze Frame (kartu dibuat sesuai urutan ini)
MONITOR_ORDER = [
    "RPM", "SPEED", "COOLANT_TEMP", "ENGINE_LOAD",
    "THROTTLE_POS", "INTAKE_TEMP", "FUEL_LEVEL",
]

# PID O2 Sensor standar (mode 01, PID 0x14-0x1B): Bank 1-2, Sensor 1-4.
# Tiap PID balikin 2 byte: A = tegangan sensor (A/200 V), B = short term
# fuel trim (kalau B=0xFF berarti sensor ini tidak dipakai/tidak ada).
O2_SENSOR_PIDS = {
    "O2_B1S1": "0114", "O2_B1S2": "0115", "O2_B1S3": "0116", "O2_B1S4": "0117",
    "O2_B2S1": "0118", "O2_B2S2": "0119", "O2_B2S3": "011A", "O2_B2S4": "011B",
}

# Label monitor readiness (PID 0101) byte C/D - beda untuk mesin bensin
# (spark ignition) vs diesel (compression ignition) sesuai standar SAE J1979.
READINESS_LABELS_SPARK = {
    0: "Catalyst", 1: "Heated Catalyst", 2: "Evap System",
    3: "Secondary Air System", 4: "A/C Refrigerant",
    5: "Oxygen Sensor", 6: "Oxygen Sensor Heater", 7: "EGR System",
}
READINESS_LABELS_COMPRESSION = {
    0: "NMHC Catalyst", 1: "NOx/SCR Monitor", 3: "Boost Pressure",
    5: "Exhaust Gas Sensor", 6: "PM Filter", 7: "EGR/VVT System",
}

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

    # ------------------------------------------------------------------
    # Live Monitor (mode 01) & Freeze Frame (mode 02)
    # ------------------------------------------------------------------
    def query(self, name):
        """Mode 01: baca nilai LIVE saat ini."""
        if name not in PIDS:
            raise ValueError(f"PID '{name}' tidak dikenal.")
        pid_hex, formula, unit = PIDS[name]
        resp = self._send_raw(pid_hex)
        mode_echo = f"{int(pid_hex[:2], 16) + 0x40:02X}"
        data = self._parse_hex_response(resp, mode_echo, pid_hex[2:].upper())
        if data is None:
            return None
        try:
            value = formula(data)
        except Exception:
            return None
        return value, unit

    def query_freeze_frame(self, name, frame=0):
        """Mode 02: baca nilai yang DIBEKUKAN persis saat DTC terakhir
        muncul (snapshot, bukan live) - PID sama dengan query(), cuma mode
        request-nya 02 bukan 01."""
        if name not in PIDS:
            raise ValueError(f"PID '{name}' tidak dikenal.")
        pid_hex, formula, unit = PIDS[name]
        # format mode02: "02" + PID(2 hex) + nomor frame(2 hex, biasanya 00)
        cmd = f"02{pid_hex[2:]}{frame:02X}"
        resp = self._send_raw(cmd)
        data = self._parse_hex_response(resp, "42", pid_hex[2:].upper())
        if data is None:
            return None
        try:
            value = formula(data)
        except Exception:
            return None
        return value, unit

    @staticmethod
    def _parse_hex_response(resp, mode_echo, pid_echo):
        # Response biasanya: "41 0C 1A F8" (mode+0x40, echo PID, data byte...)
        for line in resp.replace("\r", "\n").splitlines():
            tokens = line.strip().split()
            hex_tokens = [t for t in tokens if len(t) == 2 and all(c in "0123456789ABCDEFabcdef" for c in t)]
            if len(hex_tokens) >= 2 and hex_tokens[0].upper() == mode_echo and hex_tokens[1].upper() == pid_echo:
                data_bytes = [int(t, 16) for t in hex_tokens[2:]]
                if data_bytes:
                    return data_bytes
        return None

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

    # ------------------------------------------------------------------
    # O2 Sensor (mode 01, PID 0x14-0x1B)
    # ------------------------------------------------------------------
    def query_o2(self, name):
        if name not in O2_SENSOR_PIDS:
            raise ValueError(f"O2 sensor '{name}' tidak dikenal.")
        pid_hex = O2_SENSOR_PIDS[name]
        resp = self._send_raw(pid_hex)
        mode_echo = f"{int(pid_hex[:2], 16) + 0x40:02X}"
        data = self._parse_hex_response(resp, mode_echo, pid_hex[2:].upper())
        if data is None or len(data) < 2:
            return None
        voltage = round(data[0] / 200.0, 3)
        trim = None if data[1] == 0xFF else round((data[1] * 100 / 128.0) - 100, 1)
        return voltage, trim

    # ------------------------------------------------------------------
    # Readiness Monitor (mode 01, PID 01)
    # ------------------------------------------------------------------
    def read_readiness(self, fuel_type="Bensin"):
        """PID 0101: status MIL, jumlah DTC tersimpan, dan status siap/belum
        siap tiap monitor emisi (readiness monitors) sesuai SAE J1979."""
        resp = self._send_raw("0101")
        data = self._parse_hex_response(resp, "41", "01")
        if data is None or len(data) < 4:
            return None
        a, b, c, d = data[0], data[1], data[2], data[3]

        result = {}
        result["MIL"] = "Menyala" if (a & 0x80) else "Mati"
        result["Jumlah DTC"] = str(a & 0x7F)

        # Continuous monitors (selalu ada di semua kendaraan): byte B
        for label, support_bit, ready_bit in (
            ("Misfire", 4, 0), ("Fuel System", 5, 1), ("Components", 6, 2),
        ):
            if b & (1 << support_bit):
                result[label] = "Belum Siap" if (b & (1 << ready_bit)) else "Siap"
            else:
                result[label] = "Tidak Didukung"

        # Non-continuous monitors: byte C (didukung/tidak) + D (siap/belum)
        # - beda daftar untuk mesin bensin vs diesel.
        labels = READINESS_LABELS_COMPRESSION if fuel_type == "Diesel" else READINESS_LABELS_SPARK
        for bit, label in labels.items():
            if c & (1 << bit):
                result[label] = "Belum Siap" if (d & (1 << bit)) else "Siap"
            else:
                result[label] = "Tidak Didukung"

        return result

    # ------------------------------------------------------------------
    # DTC (mode 03 baca, mode 04 hapus)
    # ------------------------------------------------------------------
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
