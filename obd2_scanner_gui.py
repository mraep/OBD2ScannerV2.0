#!/usr/bin/env python3
"""
OBD2 Scanner GUI (via Bluetooth ELM327)
=========================================
Aplikasi desktop dengan antarmuka grafis (Tkinter) untuk membaca data
OBD2 kendaraan melalui adapter Bluetooth ELM327.

Requirement:
    pip install obd pyserial
    (Tkinter biasanya sudah termasuk dalam instalasi Python standar)
    Fitur "Tambah Perangkat Bluetooth" otomatis (scan+pair) hanya aktif di
    Linux/Raspberry Pi yang punya bluetoothctl (paket bluez). Di Windows/Mac,
    tombol yang sama akan membuka panduan pairing manual lewat OS.

Persiapan adapter Bluetooth ELM327:
    Linux/Raspberry Pi:
        1. Pairing adapter via bluetoothctl / GUI Bluetooth
        2. sudo rfcomm bind /dev/rfcomm0 <MAC_ADDRESS_ADAPTER> 1
        3. Isi kolom "Port" di aplikasi dengan: /dev/rfcomm0

    Windows:
        1. Pairing adapter via Settings > Bluetooth
        2. Windows membuat COM port virtual, contoh: COM5
        3. Isi kolom "Port" di aplikasi dengan: COM5

    Kosongkan kolom "Port" untuk mencoba auto-detect.

Jalankan:
    python obd2_scanner_gui.py
"""

import csv
import json
import os
import platform
import queue
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

try:
    import serial.tools.list_ports
    SERIAL_SCAN_AVAILABLE = True
except ImportError:
    SERIAL_SCAN_AVAILABLE = False

try:
    import obd
except ImportError:
    print("Library 'obd' belum terinstall. Jalankan: pip install obd pyserial")
    sys.exit(1)

try:
    import requests
    ONLINE_AI_AVAILABLE = True
except ImportError:
    ONLINE_AI_AVAILABLE = False

# Dipakai fitur "Tambah Perangkat Bluetooth" (scan & pair) - hanya tersedia di
# Linux/Raspberry Pi lewat bluetoothctl bawaan BlueZ. Di Windows/Mac, fitur ini
# otomatis beralih ke mode "buka pengaturan Bluetooth OS" (lihat _open_add_bluetooth_dialog).
BLUETOOTHCTL_PATH = shutil.which("bluetoothctl")


# -----------------------------
# Daftar merek mobil yang cukup umum di berbagai belahan dunia (Jepang, Eropa,
# Amerika, Korea, China, India, dst). Dipakai untuk dropdown "Merek" di Profil
# Kendaraan supaya multi-brand. BUKAN daftar lengkap-mutlak semua merek yang
# pernah ada di dunia - kalau merek kamu tidak ada di daftar, tetap bisa
# DIKETIK MANUAL langsung di kolomnya karena combobox ini tidak dikunci.
# -----------------------------
CAR_BRANDS = [
    "Acura", "Alfa Romeo", "Aston Martin", "Audi", "Baojun", "Bentley", "BMW",
    "Buick", "BYD", "Cadillac", "Changan", "Chery", "Chevrolet", "Chrysler",
    "Citroën", "Dacia", "Daewoo", "Daihatsu", "Datsun", "DFSK", "Dodge",
    "DS Automobiles", "Ferrari", "Fiat", "Ford", "Foton", "Genesis", "Geely",
    "GMC", "Great Wall / Haval", "Holden", "Honda", "Hummer", "Hyundai",
    "Infiniti", "Isuzu", "JAC", "Jaguar", "Jeep", "Kia", "Lada", "Lamborghini",
    "Lancia", "Land Rover", "Lexus", "Lincoln", "Lotus", "Maserati",
    "Maxus / LDV", "Mazda", "McLaren", "Mercedes-Benz", "MG", "Mini",
    "Mitsubishi", "Nio", "Nissan", "Opel", "Perodua", "Peugeot", "Polestar",
    "Porsche", "Proton", "RAM", "Renault", "Rivian", "Rolls-Royce", "Saab",
    "Scion", "Seat", "Škoda", "Smart", "SsangYong", "Subaru", "Suzuki",
    "Tata", "Tesla", "Toyota", "Vauxhall", "Volkswagen", "Volvo", "Wuling",
    "Xpeng", "Zotye",
]


# -----------------------------
# Daftar IP:Port default umum untuk adapter ELM327 WiFi (dicoba duluan saat
# Scan Jaringan, sebelum full subnet scan yang lebih lambat).
# -----------------------------
WIFI_ELM327_KNOWN_DEFAULTS = [
    ("192.168.0.10", 35000),
    ("192.168.0.10", 23),
    ("192.168.4.1", 35000),
    ("192.168.1.10", 35000),
    ("192.168.1.5", 35000),
    ("192.168.10.10", 23),
]


# -----------------------------
# Parameter default yang dipantau di tab Live Monitor
# -----------------------------
MONITOR_COMMANDS = [
    obd.commands.RPM,
    obd.commands.SPEED,
    obd.commands.COOLANT_TEMP,
    obd.commands.THROTTLE_POS,
    obd.commands.ENGINE_LOAD,
    obd.commands.INTAKE_TEMP,
    obd.commands.MAF,
    obd.commands.FUEL_LEVEL,
    obd.commands.CONTROL_MODULE_VOLTAGE,
    # PID tambahan yang relevan untuk mesin diesel turbo common-rail (berbagai merek):
    obd.commands.OIL_TEMP,
    obd.commands.FUEL_RAIL_PRESSURE_DIRECT,
    obd.commands.COMMANDED_EGR,
    obd.commands.BAROMETRIC_PRESSURE,
    # PID untuk Diagnostik Lanjutan (Turbo Health & EGR System Logic):
    obd.commands.INTAKE_PRESSURE,
    obd.commands.EGR_ERROR,
]


# -----------------------------
# Freeze Frame: snapshot kondisi mesin persis saat DTC pertama kali muncul.
# Mode 02 di OBD2 pakai PID yang sama dengan Mode 01, ditandai prefix "DTC_"
# di python-obd (mis. DTC_RPM = RPM saat freeze frame direkam).
# -----------------------------
FREEZE_FRAME_COMMANDS = [
    obd.commands.DTC_RPM,
    obd.commands.DTC_SPEED,
    obd.commands.DTC_COOLANT_TEMP,
    obd.commands.DTC_THROTTLE_POS,
    obd.commands.DTC_ENGINE_LOAD,
    obd.commands.DTC_INTAKE_TEMP,
    obd.commands.DTC_MAF,
    obd.commands.DTC_FUEL_LEVEL,
    obd.commands.DTC_OIL_TEMP,
    obd.commands.DTC_FUEL_RAIL_PRESSURE_DIRECT,
    obd.commands.DTC_COMMANDED_EGR,
    obd.commands.DTC_BAROMETRIC_PRESSURE,
]

# -----------------------------
# O2 Sensor (Bank 1 & 2, Sensor 1-4) + Fuel Trim.
# CATATAN: mesin diesel umumnya TIDAK memakai narrowband O2 sensor seperti
# mesin bensin, jadi PID ini bisa saja tidak didukung ECU diesel (termasuk
# beberapa mobil diesel/GM). Aplikasi akan otomatis menandai N/A kalau tidak didukung.
# -----------------------------
O2_SENSOR_COMMANDS = [
    obd.commands.O2_B1S1,
    obd.commands.O2_B1S2,
    obd.commands.O2_B1S3,
    obd.commands.O2_B1S4,
    obd.commands.O2_B2S1,
    obd.commands.O2_B2S2,
    obd.commands.O2_B2S3,
    obd.commands.O2_B2S4,
]

FUEL_TRIM_COMMANDS = [
    obd.commands.SHORT_FUEL_TRIM_1,
    obd.commands.LONG_FUEL_TRIM_1,
    obd.commands.SHORT_FUEL_TRIM_2,
    obd.commands.LONG_FUEL_TRIM_2,
]

# -----------------------------
# Parameter yang bisa dipilih untuk ditampilkan di Grafik Historis (tab Grafik).
# -----------------------------
GRAPH_COMMANDS = [
    obd.commands.RPM,
    obd.commands.SPEED,
    obd.commands.COOLANT_TEMP,
    obd.commands.THROTTLE_POS,
    obd.commands.ENGINE_LOAD,
    obd.commands.OIL_TEMP,
    obd.commands.MAF,
]


# -----------------------------
# Nilai referensi / rentang normal untuk tiap parameter, sebagai ACUAN KASAR saja
# (bukan standar mutlak - bisa berbeda dikit antar merek/unit/kondisi). Aplikasi
# ini MULTI-BRAND / MULTI-MODEL: acuan dipecah jadi bagian yang SAMA untuk semua
# kendaraan (SHARED_NORMAL_RANGES) dan bagian yang beda tergantung jenis bahan
# bakar (GASOLINE_NORMAL_RANGES vs DIESEL_NORMAL_RANGES). Pilih lewat dropdown
# "Jenis Bahan Bakar" di tab Live Monitor. Kalau nilai terbaca jauh di luar
# rentang ini terus-menerus, ada kemungkinan masalah yang perlu dicek lebih
# lanjut ke bengkel.
# -----------------------------
SHARED_NORMAL_RANGES = {
    "SPEED": "0-180 km/h (sesuai kecepatan berkendara, bukan indikator masalah)",
    "COOLANT_TEMP": "80-95\u00b0C saat mesin sudah panas normal. >100\u00b0C terus-menerus = waspada overheat",
    "THROTTLE_POS": "0-20% saat idle, naik sesuai injakan gas (hingga 100%)",
    "INTAKE_TEMP": "Mendekati suhu udara luar saat dingin, naik sedikit saat mesin panas",
    "FUEL_LEVEL": "0-100% (tidak ada 'normal', hanya indikator sisa BBM)",
    "CONTROL_MODULE_VOLTAGE": "13.5-14.5V saat mesin hidup (alternator charging). ~12.4-12.8V saat kontak ON mesin mati. <13V saat hidup = curigai alternator/aki",
    "OIL_TEMP": "80-110\u00b0C normal operasi. >130\u00b0C terus-menerus = waspada",
    "BAROMETRIC_PRESSURE": "\u2248 95-102 kPa di dataran rendah (mendekati tekanan atmosfer setempat)",
    "INTAKE_PRESSURE": "\u2248 95-105 kPa idle (dekat atmosfer), naik jauh di atas itu kalau ada boost turbo",
}

GASOLINE_NORMAL_RANGES = {
    "RPM": "Idle \u2248 700-900 rpm (bensin umumnya idle sedikit lebih tinggi dari diesel)",
    "ENGINE_LOAD": "10-30% saat idle/jalan santai, bisa >80% saat akselerasi/tanjakan",
    "MAF": "\u2248 2-6 g/s saat idle bensin, naik signifikan (>40 g/s) saat akselerasi/RPM tinggi",
    "FUEL_RAIL_PRESSURE_DIRECT": "Umumnya N/A untuk bensin port-injection biasa; kalau GDI/direct injection cek acuan pabrikan",
    "COMMANDED_EGR": "Umumnya 0% saat idle/dingin, naik sedikit sesuai kalibrasi ECU (tidak semua mesin bensin punya EGR)",
    "EGR_ERROR": "Idealnya mendekati 0% kalau kendaraan punya sistem EGR",
}

DIESEL_NORMAL_RANGES = {
    "RPM": "Idle \u2248 700-900 rpm (bervariasi per merek/model, umumnya sedikit lebih rendah dari bensin)",
    "ENGINE_LOAD": "15-40% saat idle/jalan santai, bisa >80% saat akselerasi/tanjakan",
    "MAF": "\u2248 3-8 g/s saat idle diesel, naik signifikan (>50 g/s) saat akselerasi/RPM tinggi",
    "FUEL_RAIL_PRESSURE_DIRECT": "\u2248 200-300 bar saat idle, bisa 1300-1800 bar saat beban penuh (common-rail diesel)",
    "COMMANDED_EGR": "0% saat idle/dingin, naik (10-30%) saat kondisi tertentu sesuai kalibrasi ECU",
    "EGR_ERROR": "Idealnya mendekati 0%. Deviasi besar terus-menerus = indikasi masalah valve EGR",
}


def get_normal_ranges(fuel_type):
    """Gabungkan acuan SHARED dengan acuan spesifik jenis bahan bakar
    ("Bensin" / "Diesel"), supaya tampilan kartu Live Monitor menyesuaikan
    kendaraan yang dipilih di profil, bukan cuma satu model tertentu."""
    ranges = dict(SHARED_NORMAL_RANGES)
    if fuel_type == "Diesel":
        ranges.update(DIESEL_NORMAL_RANGES)
    else:
        ranges.update(GASOLINE_NORMAL_RANGES)
    return ranges


# -----------------------------
# Konfigurasi Diagnostik Lanjutan (Turbo Health, Common Rail Stability,
# EGR System Logic). Ini semua PERHITUNGAN HEURISTIK/INDIKATIF berdasarkan
# data live monitor yang terkumpul - BUKAN diagnosa pasti dari ECU.
# Tujuannya membantu memberi sinyal awal "perlu dicek" atau tidak, bukan
# menggantikan pemeriksaan bengkel/scan tool profesional.
# -----------------------------
DIAG_BUFFER_SIZE = 60  # jumlah sampel live monitor terakhir yang disimpan untuk analisa
IDLE_RPM_RANGE = (650, 950)  # dipakai untuk deteksi kondisi idle saat analisa common rail


# -----------------------------
# Panduan reset indikator "Oil Change" / "Maintenance Required" secara MANUAL.
# CATATAN PENTING:
# Reset oli BUKAN bagian dari standar OBD2 (SAE J1979) seperti DTC. Ini adalah
# fitur proprietary tiap pabrikan. Prosedur di bawah ini adalah metode standar
# pabrik lewat tombol dashboard/setir (tidak butuh adapter OBD sama sekali) dan
# berlaku untuk kebanyakan model - tapi bisa berbeda tergantung tahun/varian.
# Selalu cek buku manual kendaraan untuk memastikan.
# -----------------------------

# -----------------------------
# File penyimpanan data tracker "Oil Life" (riwayat ganti oli terakhir).
# Disimpan sebagai JSON sederhana di folder yang sama dengan script ini,
# supaya data tetap ada walau aplikasi ditutup/dibuka lagi.
# -----------------------------
OIL_TRACKER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oil_tracker_data.json")

# File log riwayat hasil Diagnostic Assistant (rule-based, lokal, TANPA API key).
DIAG_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_analysis_log.json")

# File konfigurasi AI online (API key & model). CATATAN: API key disimpan
# PLAIN TEXT di file lokal ini (wajar untuk aplikasi desktop personal),
# jangan share file ini ke orang lain.
AI_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_online_config.json")

# Provider AI online GRATIS (tidak butuh kartu kredit, per info per pertengahan
# 2026). Kebijakan free-tier bisa berubah sewaktu-waktu tanpa pemberitahuan -
# cek halaman resmi masing-masing kalau ada perubahan limit/error.
AI_PROVIDERS = {
    "Gemini (Google - Gratis)": {
        "key": "gemini",
        "default_model": "gemini-2.5-flash",
        "get_key_url": "https://ai.google.dev/",
        "endpoint": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
    },
    "Groq (Llama - Gratis)": {
        "key": "groq",
        "default_model": "llama-3.3-70b-versatile",
        "get_key_url": "https://console.groq.com/",
        "endpoint": "https://api.groq.com/openai/v1/chat/completions",
    },
}

# -----------------------------
# Rentang nilai NUMERIK untuk evaluasi rule-based Diagnostic Assistant.
# Beda dengan NORMAL_RANGES (teks buat ditampilkan di kartu Live Monitor),
# dict ini dipakai untuk perbandingan angka otomatis oleh Diagnostic Assistant.
# Format: nama_pid: (batas_bawah, batas_atas, satuan)
# Referensi kasar umum (multi merek/model) - BUKAN spesifikasi presisi resmi
# pabrikan. FUEL_RAIL_PRESSURE_DIRECT hanya relevan untuk diesel/GDI common-rail,
# jadi dipisah per jenis bahan bakar seperti NORMAL_RANGES.
# -----------------------------
SHARED_DIAG_NUMERIC_RANGES = {
    "COOLANT_TEMP": (75, 100, "\u00b0C"),
    "OIL_TEMP": (70, 120, "\u00b0C"),
    "CONTROL_MODULE_VOLTAGE": (13.0, 14.8, "V"),
    "INTAKE_PRESSURE": (90, 260, "kPa"),
}

GASOLINE_DIAG_NUMERIC_RANGES = {
    "FUEL_RAIL_PRESSURE_DIRECT": (30, 200, "bar"),
}

DIESEL_DIAG_NUMERIC_RANGES = {
    "FUEL_RAIL_PRESSURE_DIRECT": (150, 1900, "bar"),
}


def get_diag_numeric_ranges(fuel_type):
    """Gabungkan rentang numerik SHARED dengan rentang khusus jenis bahan
    bakar, dipakai oleh Diagnostik Lanjutan / Diagnostic Assistant."""
    ranges = dict(SHARED_DIAG_NUMERIC_RANGES)
    if fuel_type == "Diesel":
        ranges.update(DIESEL_DIAG_NUMERIC_RANGES)
    else:
        ranges.update(GASOLINE_DIAG_NUMERIC_RANGES)
    return ranges

OIL_RESET_GUIDE = {
    "Chevrolet Captiva Diesel 2.0L/2.2L VCDi (2011-2015, gen. Captiva pertama)": [
        "PENTING: Captiva diesel PAKAI PROSEDUR PEDAL GAS, BUKAN tombol trip meter.",
        "1. Masukkan kunci, putar ke posisi ON (satu posisi sebelum starter),",
        "   JANGAN nyalakan mesin.",
        "2. Karena mesin diesel, tekan pedal GAS PERLAHAN (bukan cepat):",
        "   tekan penuh & tahan ±2 detik, lepas ±2 detik, ulangi total 3 kali,",
        "   semua dalam waktu 60 detik sejak kunci ON.",
        "3. Lampu 'Oil Change' / kunci pas seharusnya mati atau OIL LIFE jadi 100%.",
        "4. Matikan kunci, lalu nyalakan mesin untuk konfirmasi lampu sudah mati.",
        "5. Jika belum berhasil, ulangi dari langkah 1 (kadang perlu 2x percobaan).",
        "",
        "CATATAN MODEL:",
        "- Prosedur ini berlaku untuk Captiva generasi pertama (2006-2015),",
        "  termasuk unit tahun 2014 yang umum di Indonesia.",
        "- Jika mobil kamu adalah Captiva 'Series 2' / facelift Eropa (Vauxhall/",
        "  Holden, ~2015 ke atas) dan tidak ada menu 'Oil Life' di panel instrumen,",
        "  reset kemungkinan HANYA bisa lewat scan tool khusus GM (Tech2/GDS2/",
        "  Autel dengan software GM) via port OBD2 - cara pedal gas tidak akan bekerja.",
        "- Cek buku manual atau tanya ke bengkel resmi Chevrolet untuk memastikan.",
    ],
    "Toyota / Lexus (kebanyakan model)": [
        "1. Matikan mesin (posisi OFF).",
        "2. Tekan dan tahan tombol trip meter (ODO/TRIP) di panel instrumen.",
        "3. Sambil menahan tombol, putar kunci ke posisi ON (mesin tetap mati).",
        "4. Tahan sampai tulisan 'Maint Reqd' berkedip, lalu lepas tombol.",
        "5. Tekan lagi tombol trip sampai indikator berhenti berkedip dan mati.",
    ],
    "Honda (kebanyakan model)": [
        "1. Kunci kontak posisi ON (mesin boleh mati), jangan starter mesin.",
        "2. Tekan tombol SELECT/RESET sampai muncul menu 'Maintenance Item'.",
        "3. Tahan tombol RESET selama ±10 detik sampai indikator berkedip.",
        "4. Lepas, lalu tekan RESET lagi sambil kontak ON untuk konfirmasi.",
    ],
    "Daihatsu (mirip Toyota, mis. Xenia/Terios)": [
        "1. Matikan mesin.",
        "2. Tekan & tahan tombol trip meter.",
        "3. Putar kunci ke ON sambil tetap menahan tombol trip.",
        "4. Tahan hingga indikator servis berkedip lalu mati.",
    ],
    "Suzuki (kebanyakan model)": [
        "1. Kunci kontak OFF.",
        "2. Tekan & tahan tombol trip meter.",
        "3. Putar kunci ke ON (mesin tetap mati) sambil tetap menahan tombol.",
        "4. Tahan ±10 detik sampai indikator servis mati / reset.",
    ],
    "Mitsubishi (kebanyakan model)": [
        "1. Kunci kontak OFF.",
        "2. Tekan & tahan tombol trip meter, lalu putar kunci ke ON.",
        "3. Tahan tombol ±10 detik sampai simbol kunci pas / servis berkedip.",
        "4. Lepas tombol; indikator akan reset.",
    ],
    "VW / Audi / Skoda / Seat (grup VAG)": [
        "1. Matikan mesin, tekan & tahan tombol trip (0.0) di panel instrumen.",
        "2. Putar kunci ke ON sambil tetap menahan tombol trip.",
        "3. Tunggu sampai muncul menu servis di layar (misal 'Oil change').",
        "4. Gunakan tombol trip untuk konfirmasi reset (biasanya tekan sebentar).",
        "   Catatan: beberapa model VAG terbaru butuh alat diagnostik khusus (VCDS/OBDeleven).",
    ],
    "BMW (kebanyakan model, non-iDrive lama)": [
        "1. Umumnya BMW mereset otomatis lewat servis resmi/alat khusus (INPA/ISTA).",
        "2. Model lama: tekan tombol trip reset sambil kunci ON, mirip merek lain.",
        "   Untuk BMW modern, reset oli biasanya HANYA bisa lewat scan tool khusus BMW.",
    ],
    "Mercedes-Benz (kebanyakan model)": [
        "1. Reset biasanya lewat menu di layar instrumen (Assyst/ASSYST Plus).",
        "2. Masuk ke menu 'Service' via tombol di setir/panel, pilih 'Reset'.",
        "3. Konfirmasi dengan menahan tombol OK/Reset beberapa detik.",
        "   Model tertentu butuh alat diagnostik resmi Mercedes (STAR/XENTRY).",
    ],
}


class OBD2App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OBD2 Scanner - Multi Brand (Bluetooth/USB/WiFi)")
        self.geometry("820x620")
        self.minsize(720, 520)

        self.connection = None
        self.polling = False
        self.poll_thread = None
        self.log_writer = None
        self.log_file = None
        self.msg_queue = queue.Queue()
        self.last_monitor_values = {}
        self.last_dtc_codes = None  # None = belum pernah dicek, [] = sudah dicek & bersih

        # Buffer rolling data untuk Diagnostik Lanjutan (Turbo/Common Rail/EGR)
        self.diag_buffers = {
            "RPM": deque(maxlen=DIAG_BUFFER_SIZE),
            "ENGINE_LOAD": deque(maxlen=DIAG_BUFFER_SIZE),
            "INTAKE_PRESSURE": deque(maxlen=DIAG_BUFFER_SIZE),
            "BAROMETRIC_PRESSURE": deque(maxlen=DIAG_BUFFER_SIZE),
            "FUEL_RAIL_PRESSURE_DIRECT": deque(maxlen=DIAG_BUFFER_SIZE),
            "COMMANDED_EGR": deque(maxlen=DIAG_BUFFER_SIZE),
            "EGR_ERROR": deque(maxlen=DIAG_BUFFER_SIZE),
        }

        self._build_ui()
        self._poll_queue()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------
    # UI Construction
    # ---------------------------------------------------------
    def _build_ui(self):
        # ---- Panel koneksi (selalu terlihat di atas) ----
        conn_frame = ttk.LabelFrame(self, text="Koneksi Adapter")
        conn_frame.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(conn_frame, text="Tipe Koneksi:").grid(row=0, column=0, padx=5, pady=8, sticky="w")
        self.conn_type_var = tk.StringVar(value="Bluetooth")
        conn_type_combo = ttk.Combobox(
            conn_frame,
            textvariable=self.conn_type_var,
            values=["Bluetooth", "USB", "WiFi"],
            state="readonly",
            width=12,
        )
        conn_type_combo.grid(row=0, column=1, padx=5, pady=8, sticky="w")
        conn_type_combo.bind("<<ComboboxSelected>>", lambda e: self._on_conn_type_changed())

        # ---- Sub-panel Bluetooth/USB (pakai field Port yang sama, karena
        # keduanya sama-sama serial port di level OS - rfcomm/COM untuk
        # Bluetooth, atau COM/ttyUSB untuk kabel USB) ----
        self.serial_frame = ttk.Frame(conn_frame)
        ttk.Label(self.serial_frame, text="Port:").pack(side="left", padx=(0, 5))
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(self.serial_frame, textvariable=self.port_var, width=22, values=[])
        self.port_combo.pack(side="left", padx=(0, 5))
        self.port_combo.bind("<<ComboboxSelected>>", self._on_port_selected)
        self.scan_serial_btn = ttk.Button(
            self.serial_frame, text="Scan", width=6, command=self._scan_serial_ports
        )
        self.scan_serial_btn.pack(side="left", padx=(0, 8))
        self.add_bt_btn = ttk.Button(
            self.serial_frame, text="Tambah Perangkat Bluetooth", command=self._open_add_bluetooth_dialog
        )
        self.add_bt_btn.pack(side="left", padx=(0, 8))
        self.serial_hint_var = tk.StringVar()
        ttk.Label(self.serial_frame, textvariable=self.serial_hint_var, foreground="gray").pack(side="left")

        # ---- Sub-panel WiFi (adapter ELM327 WiFi, konek lewat TCP socket) ----
        self.wifi_frame = ttk.Frame(conn_frame)
        ttk.Label(self.wifi_frame, text="IP Address:").pack(side="left", padx=(0, 5))
        self.wifi_ip_var = tk.StringVar(value="192.168.0.10")
        self.wifi_ip_combo = ttk.Combobox(self.wifi_frame, textvariable=self.wifi_ip_var, width=16, values=[])
        self.wifi_ip_combo.pack(side="left", padx=(0, 10))
        self.wifi_ip_combo.bind("<<ComboboxSelected>>", self._on_wifi_ip_selected)
        ttk.Label(self.wifi_frame, text="Port:").pack(side="left", padx=(0, 5))
        self.wifi_port_var = tk.StringVar(value="35000")
        ttk.Entry(self.wifi_frame, textvariable=self.wifi_port_var, width=8).pack(side="left", padx=(0, 5))
        self.scan_wifi_btn = ttk.Button(
            self.wifi_frame, text="Scan Jaringan", command=self._scan_wifi_adapters
        )
        self.scan_wifi_btn.pack(side="left", padx=(0, 8))
        ttk.Label(
            self.wifi_frame, text="(default umum: 192.168.0.10:35000)", foreground="gray"
        ).pack(side="left")

        self.serial_frame.grid(row=0, column=2, padx=5, pady=8, sticky="w")
        self._on_conn_type_changed()  # set tampilan awal sesuai tipe default (Bluetooth)

        self.connect_btn = ttk.Button(conn_frame, text="Sambungkan", command=self._connect)
        self.connect_btn.grid(row=0, column=3, padx=10)

        self.status_var = tk.StringVar(value="Status: belum terhubung")
        ttk.Label(conn_frame, textvariable=self.status_var, foreground="gray").grid(
            row=1, column=0, columnspan=4, padx=5, pady=(0, 4), sticky="w"
        )

        self.scan_status_var = tk.StringVar(value="")
        ttk.Label(conn_frame, textvariable=self.scan_status_var, foreground="#0a6b0a", wraplength=760).grid(
            row=2, column=0, columnspan=4, padx=5, pady=(0, 8), sticky="w"
        )

        # ---- Notebook (tab) ----
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        self.tab_monitor = ttk.Frame(self.notebook)
        self.tab_dtc = ttk.Frame(self.notebook)
        self.tab_freeze = ttk.Frame(self.notebook)
        self.tab_readiness = ttk.Frame(self.notebook)
        self.tab_o2 = ttk.Frame(self.notebook)
        self.tab_graph = ttk.Frame(self.notebook)
        self.tab_advanced = ttk.Frame(self.notebook)
        self.tab_ai_diag = ttk.Frame(self.notebook)
        self.tab_ai_log = ttk.Frame(self.notebook)
        self.tab_oil = ttk.Frame(self.notebook)
        self.tab_info = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_monitor, text="Live Monitor")
        self.notebook.add(self.tab_dtc, text="Kode Error (DTC)")
        self.notebook.add(self.tab_freeze, text="Freeze Frame")
        self.notebook.add(self.tab_readiness, text="Readiness Monitors")
        self.notebook.add(self.tab_o2, text="O2 & Fuel Trim")
        self.notebook.add(self.tab_graph, text="Grafik")
        self.notebook.add(self.tab_advanced, text="Diagnostik Lanjutan")
        self.notebook.add(self.tab_ai_diag, text="AI Diagnostic")
        self.notebook.add(self.tab_ai_log, text="AI Analysis Log")
        self.notebook.add(self.tab_oil, text="Reset Oli")
        self.notebook.add(self.tab_info, text="Info Kendaraan")

        self._build_monitor_tab()
        self._build_dtc_tab()
        self._build_freeze_tab()
        self._build_readiness_tab()
        self._build_o2_tab()
        self._build_graph_tab()
        self._build_advanced_diag_tab()
        self._build_ai_diagnostic_tab()
        self._build_ai_log_tab()
        self._build_oil_tab()
        self._build_info_tab()

        # ---- Log bar bawah ----
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=4, state="disabled", bg="#111", fg="#0f0")
        self.log_text.pack(fill="x")

    def _on_conn_type_changed(self):
        conn_type = self.conn_type_var.get()
        self.wifi_frame.grid_forget()
        self.serial_frame.grid_forget()

        if conn_type == "WiFi":
            self.wifi_frame.grid(row=0, column=2, padx=5, pady=8, sticky="w")
        else:
            self.serial_frame.grid(row=0, column=2, padx=5, pady=8, sticky="w")
            if conn_type == "Bluetooth":
                self.serial_hint_var.set("kosongkan=auto-detect. Contoh: /dev/rfcomm0 atau COM5")
                self.add_bt_btn.pack(side="left", padx=(0, 8))
            else:  # USB
                self.serial_hint_var.set("kosongkan=auto-detect. Contoh: /dev/ttyUSB0 atau COM3")
                self.add_bt_btn.pack_forget()

    def _build_connection_portstr(self):
        """Bangun string koneksi (portstr) sesuai tipe koneksi yang dipilih.
        Bluetooth & USB sama-sama pakai serial port biasa (rfcomm/COM/ttyUSB).
        WiFi pakai URL socket:// bawaan pyserial supaya bisa konek ke adapter
        ELM327 WiFi lewat TCP, tanpa perlu library tambahan."""
        conn_type = self.conn_type_var.get()
        if conn_type == "WiFi":
            ip = self.wifi_ip_var.get().strip()
            port = self.wifi_port_var.get().strip()
            if not ip or not port:
                raise ValueError("IP Address dan Port WiFi harus diisi.")
            return f"socket://{ip}:{port}"
        else:
            port = self.port_var.get().strip()
            return port or None  # None = auto-detect (khusus Bluetooth/USB)

    # ---------------------------------------------------------
    # Scan Device: cari port/adapter otomatis untuk SETIAP tipe koneksi
    # (Bluetooth, USB, WiFi), supaya user tidak perlu tahu persis nama
    # port/IP adapter-nya - tinggal klik "Scan" lalu pilih dari dropdown.
    # ---------------------------------------------------------
    def _on_port_selected(self, event=None):
        # Combobox menampilkan "DEVICE - deskripsi", tapi yang dipakai untuk
        # konek harus cuma bagian DEVICE-nya saja (mis. "COM5" atau "/dev/ttyUSB0").
        selected = self.port_var.get()
        device = selected.split(" - ", 1)[0].strip()
        self.port_var.set(device)

    def _on_wifi_ip_selected(self, event=None):
        # Combobox menampilkan "IP:PORT" hasil scan -> pecah ke 2 field terpisah.
        selected = self.wifi_ip_var.get()
        if ":" in selected:
            ip, port = selected.split(":", 1)
            self.wifi_ip_var.set(ip.strip())
            self.wifi_port_var.set(port.strip())

    def _scan_serial_ports(self):
        """Scan port serial yang terpasang di OS (dipakai untuk Bluetooth
        rfcomm/COM maupun USB ttyUSB/COM - keduanya sama-sama serial port)."""
        if not SERIAL_SCAN_AVAILABLE:
            messagebox.showwarning(
                "Scan Port",
                "Modul 'pyserial' belum lengkap untuk fitur scan port.\n"
                "Jalankan: pip install pyserial",
            )
            return
        self.scan_serial_btn.configure(state="disabled")
        self.scan_status_var.set("Memindai port serial (Bluetooth/USB)...")
        self._log("Memulai scan port serial...")
        threading.Thread(target=self._scan_serial_ports_worker, daemon=True).start()

    def _scan_serial_ports_worker(self):
        try:
            ports = list(serial.tools.list_ports.comports())
            err = None
        except Exception as e:
            ports = []
            err = str(e)
        self.after(0, lambda: self._show_serial_scan_result(ports, err))

    def _show_serial_scan_result(self, ports, err):
        self.scan_serial_btn.configure(state="normal")
        if err:
            self.scan_status_var.set(f"Scan port gagal: {err}")
            self._log(f"Scan port serial gagal: {err}")
            return
        if not ports:
            self.scan_status_var.set(
                "Tidak ada port serial terdeteksi. Pastikan adapter Bluetooth sudah "
                "di-pair (rfcomm/COM) atau adapter USB sudah dicolok & driver-nya terpasang."
            )
            self.port_combo["values"] = []
            self._log("Scan port serial: tidak ditemukan port.")
            return

        values = []
        for p in ports:
            desc = (p.description or "").strip()
            values.append(f"{p.device} - {desc}" if desc and desc.lower() != "n/a" else p.device)

        self.port_combo["values"] = values
        if not self.port_var.get().strip():
            self.port_var.set(ports[0].device)
        self.scan_status_var.set(
            f"Ditemukan {len(ports)} port serial: {', '.join(p.device for p in ports)}. "
            "Pilih dari dropdown Port di atas."
        )
        self._log(f"Scan port serial selesai: {len(ports)} ditemukan.")

    def _scan_wifi_adapters(self):
        """Scan adapter ELM327 WiFi: coba dulu daftar IP:Port default yang
        umum dipakai adapter OBD2 WiFi (cepat), lalu kalau belum ketemu coba
        seluruh subnet lokal di port default 35000 (agak lebih lambat)."""
        self.scan_wifi_btn.configure(state="disabled")
        self.scan_status_var.set(
            "Memindai jaringan untuk adapter ELM327 WiFi (bisa makan waktu beberapa detik)... "
            "Pastikan HP/laptop sudah terhubung ke WiFi adapter OBD2, bukan WiFi lain."
        )
        self._log("Memulai scan jaringan WiFi untuk adapter ELM327...")
        threading.Thread(target=self._scan_wifi_adapters_worker, daemon=True).start()

    @staticmethod
    def _tcp_probe(ip, port, timeout=0.3):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _get_local_ip():
        """Ambil IP lokal mesin ini di jaringan WiFi (tanpa perlu internet -
        socket UDP 'connect' ke sini cuma dipakai OS buat menentukan
        interface, tidak benar-benar mengirim data)."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return None

    def _scan_wifi_adapters_worker(self):
        found = []

        # 1) Coba daftar default umum dulu (cepat, cuma beberapa kombinasi)
        for ip, port in WIFI_ELM327_KNOWN_DEFAULTS:
            if self._tcp_probe(ip, port, timeout=0.3):
                found.append((ip, port))

        # 2) Kalau belum ketemu, scan subnet lokal (/24) di port default 35000
        if not found:
            local_ip = self._get_local_ip()
            if local_ip:
                prefix = ".".join(local_ip.split(".")[:3])
                candidates = [f"{prefix}.{i}" for i in range(1, 255)]
                try:
                    with ThreadPoolExecutor(max_workers=64) as executor:
                        results = executor.map(
                            lambda ip: (ip, self._tcp_probe(ip, 35000, timeout=0.15)), candidates
                        )
                        found = [(ip, 35000) for ip, ok in results if ok]
                except Exception:
                    pass

        self.after(0, lambda: self._show_wifi_scan_result(found))

    def _show_wifi_scan_result(self, found):
        self.scan_wifi_btn.configure(state="normal")
        if not found:
            self.scan_status_var.set(
                "Tidak ada adapter ELM327 WiFi terdeteksi. Pastikan sudah terhubung ke "
                "jaringan WiFi adapter OBD2 (bukan WiFi rumah/kantor), lalu coba Scan Jaringan lagi."
            )
            self._log("Scan jaringan WiFi: tidak ditemukan adapter.")
            return

        values = [f"{ip}:{port}" for ip, port in found]
        self.wifi_ip_combo["values"] = values
        self.wifi_ip_var.set(found[0][0])
        self.wifi_port_var.set(str(found[0][1]))
        self.scan_status_var.set(f"Ditemukan {len(found)} kemungkinan adapter WiFi: {', '.join(values)}")
        self._log(f"Scan jaringan WiFi selesai: {len(found)} ditemukan.")

    # ---------------------------------------------------------
    # Tambah Perangkat Bluetooth: SCAN & PAIRING perangkat Bluetooth BARU di
    # sekitar (beda dengan "Scan" port di atas, yang cuma mendeteksi port
    # yang SUDAH ter-bind/paired sebelumnya).
    #
    # Di Linux/Raspberry Pi diotomasi lewat bluetoothctl (BlueZ) - scan,
    # pair, trust, sampai coba bind ke /dev/rfcommN.
    # Di Windows/Mac, classic Bluetooth (SPP) pairing tidak bisa diotomasi
    # murni dari Python/Tkinter tanpa driver native tambahan, jadi tombol ini
    # membuka pengaturan Bluetooth OS + panduan manual, lalu tinggal klik
    # "Scan" (port) di atas setelah pairing selesai.
    # ---------------------------------------------------------
    def _open_add_bluetooth_dialog(self):
        win = tk.Toplevel(self)
        win.title("Tambah Perangkat Bluetooth")
        win.geometry("560x460")
        win.transient(self)

        if platform.system() == "Linux" and BLUETOOTHCTL_PATH:
            self._build_bluetooth_dialog_linux(win)
        else:
            self._build_bluetooth_dialog_fallback(win)

    def _build_bluetooth_dialog_fallback(self, win):
        os_name = platform.system()
        ttk.Label(
            win,
            text=(
                f"Pairing otomatis dari dalam aplikasi ini belum didukung di {os_name or 'OS ini'}.\n\n"
                "Pasangkan adapter ELM327 Bluetooth lewat pengaturan Bluetooth bawaan "
                "OS kamu dulu (seperti biasa), lalu kembali ke sini dan klik tombol "
                "'Scan Port Sekarang' di bawah untuk mendeteksi port yang muncul "
                "setelah pairing."
            ),
            wraplength=520, justify="left",
        ).pack(padx=15, pady=15, anchor="w")

        steps_by_os = {
            "Windows": (
                "1. Klik 'Buka Pengaturan Bluetooth Windows' di bawah.\n"
                "2. Klik 'Add device' / 'Tambahkan perangkat' > Bluetooth.\n"
                "3. Pilih adapter ELM327 dari daftar, lalu pasangkan (kalau diminta "
                "PIN, coba 1234 atau 0000 - umum untuk adapter ELM327).\n"
                "4. Windows akan membuat COM port virtual (contoh: COM5).\n"
                "5. Kembali ke sini, klik 'Scan Port Sekarang'."
            ),
            "Darwin": (
                "1. Buka System Settings > Bluetooth di Mac kamu.\n"
                "2. Pasangkan adapter ELM327 dari daftar perangkat.\n"
                "3. Kembali ke sini, klik 'Scan Port Sekarang'."
            ),
        }
        steps_text = steps_by_os.get(
            os_name,
            "1. Buka pengaturan Bluetooth OS kamu dan pasangkan adapter.\n"
            "2. Kembali ke sini, klik 'Scan Port Sekarang'.",
        )
        ttk.Label(win, text=steps_text, foreground="gray", wraplength=520, justify="left").pack(
            padx=15, pady=(0, 15), anchor="w"
        )

        btn_row = ttk.Frame(win)
        btn_row.pack(padx=15, pady=(0, 15), anchor="w")

        if os_name == "Windows":
            ttk.Button(
                btn_row, text="Buka Pengaturan Bluetooth Windows",
                command=self._open_windows_bluetooth_settings,
            ).pack(side="left", padx=(0, 10))

        ttk.Button(
            btn_row, text="Scan Port Sekarang",
            command=lambda: (win.destroy(), self._scan_serial_ports()),
        ).pack(side="left")

    @staticmethod
    def _open_windows_bluetooth_settings():
        try:
            os.startfile("ms-settings:bluetooth")
        except Exception:
            try:
                subprocess.Popen(["cmd", "/c", "start", "ms-settings:bluetooth"])
            except Exception:
                pass

    def _build_bluetooth_dialog_linux(self, win):
        ttk.Label(
            win,
            text=(
                "Cari perangkat Bluetooth di sekitar lewat bluetoothctl (BlueZ), "
                "lalu pasangkan & siapkan port serial-nya (rfcomm) otomatis."
            ),
            wraplength=520, justify="left",
        ).pack(padx=15, pady=(15, 5), anchor="w")

        btn_row = ttk.Frame(win)
        btn_row.pack(padx=15, pady=(0, 10), anchor="w")
        scan_btn = ttk.Button(
            btn_row, text="Cari Perangkat (8 detik)", command=lambda: self._bt_scan_devices(win)
        )
        scan_btn.pack(side="left", padx=(0, 10))
        pair_btn = ttk.Button(
            btn_row, text="Pasangkan & Siapkan Port", state="disabled",
            command=lambda: self._bt_pair_selected(win),
        )
        pair_btn.pack(side="left")

        list_frame = ttk.Frame(win)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        listbox = tk.Listbox(list_frame, height=7)
        listbox.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        vsb.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=vsb.set)

        ttk.Label(win, text="Log:", foreground="gray").pack(anchor="w", padx=15)
        log_text = tk.Text(win, height=8, state="disabled", bg="#111", fg="#0f0")
        log_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Simpan referensi widget di objek window supaya gampang diakses helper lain
        win.scan_btn = scan_btn
        win.pair_btn = pair_btn
        win.listbox = listbox
        win.log_text = log_text
        win.found_devices = []  # list of (mac, name)

    def _bt_log(self, win, text):
        win.log_text.configure(state="normal")
        win.log_text.insert("end", text + "\n")
        win.log_text.see("end")
        win.log_text.configure(state="disabled")

    def _bt_scan_devices(self, win):
        win.scan_btn.configure(state="disabled")
        win.pair_btn.configure(state="disabled")
        win.listbox.delete(0, "end")
        win.found_devices = []
        self._bt_log(win, "Memindai perangkat Bluetooth sekitar (8 detik)...")
        threading.Thread(target=self._bt_scan_worker, args=(win,), daemon=True).start()

    def _bt_scan_worker(self, win):
        devices = {}
        err = None
        proc = None
        try:
            proc = subprocess.Popen(
                [BLUETOOTHCTL_PATH],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            proc.stdin.write("agent on\n")
            proc.stdin.write("default-agent\n")
            proc.stdin.write("scan on\n")
            proc.stdin.flush()

            pattern = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)")
            end_time = time.time() + 8
            while time.time() < end_time:
                line = proc.stdout.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                m = pattern.search(line)
                if m:
                    mac, name = m.group(1), m.group(2).strip()
                    devices[mac] = name

            proc.stdin.write("scan off\nquit\n")
            proc.stdin.flush()
        except Exception as e:
            err = str(e)
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass

        self.after(0, lambda: self._bt_scan_result(win, devices, err))

    def _bt_scan_result(self, win, devices, err):
        win.scan_btn.configure(state="normal")
        if err:
            self._bt_log(win, f"Scan gagal: {err}")
            return
        if not devices:
            self._bt_log(
                win, "Tidak ada perangkat ditemukan. Pastikan adapter ELM327 menyala, "
                     "dekat dengan komputer, dan Bluetooth aktif - lalu coba lagi."
            )
            return
        win.found_devices = list(devices.items())
        for mac, name in win.found_devices:
            win.listbox.insert("end", f"{mac}  -  {name}")
        win.pair_btn.configure(state="normal")
        self._bt_log(
            win, f"Ditemukan {len(win.found_devices)} perangkat. Pilih satu dari daftar, "
                 "lalu klik 'Pasangkan & Siapkan Port'."
        )

    def _bt_pair_selected(self, win):
        sel = win.listbox.curselection()
        if not sel:
            messagebox.showinfo("Pilih Perangkat", "Pilih dulu salah satu perangkat dari daftar.")
            return
        mac, name = win.found_devices[sel[0]]
        win.pair_btn.configure(state="disabled")
        self._bt_log(win, f"Memasangkan {name} ({mac})...")
        threading.Thread(target=self._bt_pair_worker, args=(win, mac, name), daemon=True).start()

    def _bt_pair_worker(self, win, mac, name):
        pair_ok = False
        try:
            proc = subprocess.Popen(
                [BLUETOOTHCTL_PATH],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            proc.stdin.write("agent on\ndefault-agent\n")
            proc.stdin.write(f"pair {mac}\n")
            proc.stdin.flush()
            time.sleep(4)  # kasih waktu proses pairing (kadang butuh konfirmasi PIN)
            proc.stdin.write(f"trust {mac}\n")
            proc.stdin.flush()
            time.sleep(1)
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            proc.wait(timeout=10)
            pair_ok = True
        except Exception as e:
            self.after(0, lambda: self._bt_log(win, f"Proses pairing gagal: {e}"))

        rfcomm_dev = None
        bind_err = None
        if pair_ok:
            for i in range(10):
                candidate = f"/dev/rfcomm{i}"
                if os.path.exists(candidate):
                    continue  # sudah dipakai, coba nomor berikutnya
                try:
                    result = subprocess.run(
                        ["sudo", "-n", "rfcomm", "bind", candidate, mac, "1"],
                        capture_output=True, text=True, timeout=10,
                    )
                    if result.returncode == 0:
                        rfcomm_dev = candidate
                    else:
                        bind_err = (result.stderr or result.stdout or "").strip()
                except Exception as e:
                    bind_err = str(e)
                break

        self.after(0, lambda: self._bt_pair_result(win, mac, name, rfcomm_dev, bind_err))

    def _bt_pair_result(self, win, mac, name, rfcomm_dev, bind_err):
        win.pair_btn.configure(state="normal")
        self._bt_log(win, f"Pairing & trust untuk {name} ({mac}) sudah dikirim ke bluetoothctl.")
        if rfcomm_dev:
            self._bt_log(win, f"Berhasil bind ke {rfcomm_dev}.")
            self.port_var.set(rfcomm_dev)
            self.scan_status_var.set(
                f"Perangkat '{name}' dipasangkan & di-bind ke {rfcomm_dev}. "
                "Port sudah terisi otomatis - langsung klik 'Sambungkan'."
            )
            self._log(f"Bluetooth: {name} ({mac}) berhasil di-bind ke {rfcomm_dev}.")
        else:
            manual_cmd = f"sudo rfcomm bind /dev/rfcomm0 {mac} 1"
            self._bt_log(
                win,
                "Bind otomatis ke /dev/rfcommN tidak berhasil (butuh sudo interaktif "
                "di kebanyakan sistem). Jalankan perintah berikut di terminal:\n"
                f"  {manual_cmd}\n"
                "lalu isi kolom Port dengan /dev/rfcomm0 (atau klik 'Scan' setelahnya).",
            )
            if bind_err:
                self._bt_log(win, f"Detail: {bind_err}")
            self.scan_status_var.set(
                f"Perangkat '{name}' sudah di-pair/trust. Jalankan '{manual_cmd}' di "
                "terminal untuk menyiapkan port-nya, lalu klik Scan."
            )

    def _make_scrollable(self, parent):
        """
        Bikin area yang bisa di-scroll vertikal di dalam `parent`.
        Return: frame `inner` - taruh semua widget konten DI DALAM `inner` ini
        (bukan langsung di `parent`), supaya kalau kontennya lebih tinggi dari
        jendela, tetap bisa discroll pakai scrollbar atau mouse wheel.
        """
        canvas = tk.Canvas(parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)

        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Supaya lebar `inner` ikut menyesuaikan lebar canvas (biar tidak ada
        # ruang kosong aneh di kanan saat window di-resize lebih lebar)
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(canvas_window, width=e.width),
        )

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Scroll pakai mouse wheel (Windows/Mac pakai <MouseWheel>, Linux pakai Button-4/5)
        def _on_mousewheel(event):
            delta = -1 * (event.delta // 120) if event.delta else (-1 if event.num == 4 else 1)
            canvas.yview_scroll(delta, "units")

        def _bind_wheel(_):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_wheel(_):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        return inner

    def _make_scrollable_treeview(self, parent, columns, headings, widths, height=14):
        """
        Bikin Treeview dengan scrollbar vertikal terpasang, supaya baris yang
        banyak (mis. daftar readiness monitor / DTC / freeze frame) tetap bisa
        discroll, tidak terpotong begitu saja.
        Return: objek Treeview yang sudah siap dipakai (tinggal .insert(...)).
        """
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        tree = ttk.Treeview(container, columns=columns, show="headings", height=height)
        for col, head, width in zip(columns, headings, widths):
            tree.heading(col, text=head)
            tree.column(col, width=width)

        vsb = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)

        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        return tree

    def _build_monitor_tab(self):
        scroll_area = self._make_scrollable(self.tab_monitor)

        # ---- Panel Profil Kendaraan: bikin aplikasi ini MULTI-BRAND, bukan
        # cuma satu model tertentu. Merek dipilih dari dropdown berisi merek
        # mobil umum di dunia (tetap bisa diketik manual kalau tidak ada di
        # daftar), Model & Tahun teks bebas - semua dipakai sebagai catatan
        # konteks di AI Diagnostic. Jenis Bahan Bakar menentukan set acuan
        # (NORMAL_RANGES / DIAG_NUMERIC_RANGES) yang dipakai di kartu Live
        # Monitor & Diagnostik Lanjutan. ----
        profile_frame = ttk.LabelFrame(scroll_area, text="Profil Kendaraan")
        profile_frame.pack(fill="x", padx=10, pady=(8, 5))

        ttk.Label(profile_frame, text="Merek:").grid(row=0, column=0, padx=5, pady=8, sticky="w")
        self.vehicle_brand_var = tk.StringVar(value="")
        brand_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.vehicle_brand_var,
            values=CAR_BRANDS,
            state="normal",  # tetap bisa diketik manual kalau mereknya belum ada di daftar
            width=20,
        )
        brand_combo.grid(row=0, column=1, padx=5, pady=8, sticky="w")
        self.vehicle_brand_var.trace_add("write", lambda *a: self._sync_ai_vehicle_context())

        ttk.Label(profile_frame, text="Model:").grid(row=0, column=2, padx=(15, 5), pady=8, sticky="w")
        self.vehicle_model_var = tk.StringVar(value="")
        ttk.Entry(profile_frame, textvariable=self.vehicle_model_var, width=18).grid(
            row=0, column=3, padx=5, pady=8, sticky="w"
        )
        self.vehicle_model_var.trace_add("write", lambda *a: self._sync_ai_vehicle_context())

        ttk.Label(profile_frame, text="Tahun:").grid(row=0, column=4, padx=(15, 5), pady=8, sticky="w")
        self.vehicle_year_var = tk.StringVar(value="")
        year_entry = ttk.Entry(profile_frame, textvariable=self.vehicle_year_var, width=8)
        year_entry.grid(row=0, column=5, padx=5, pady=8, sticky="w")
        self.vehicle_year_var.trace_add("write", lambda *a: self._sync_ai_vehicle_context())

        ttk.Label(profile_frame, text="Jenis Bahan Bakar:").grid(
            row=1, column=0, padx=5, pady=(0, 8), sticky="w"
        )
        self.vehicle_fuel_type_var = tk.StringVar(value="Bensin")
        fuel_type_combo = ttk.Combobox(
            profile_frame,
            textvariable=self.vehicle_fuel_type_var,
            values=["Bensin", "Diesel"],
            state="readonly",
            width=10,
        )
        fuel_type_combo.grid(row=1, column=1, padx=5, pady=(0, 8), sticky="w")
        fuel_type_combo.bind("<<ComboboxSelected>>", lambda e: self._on_vehicle_profile_changed())

        ttk.Label(
            profile_frame,
            text="(Merek bisa diketik manual kalau tidak ada di daftar. Jenis Bahan Bakar mengatur acuan nilai normal di bawah.)",
            foreground="gray",
        ).grid(row=2, column=0, columnspan=6, padx=5, pady=(0, 8), sticky="w")

        top = ttk.Frame(scroll_area)
        top.pack(fill="x", pady=8)

        self.monitor_btn = ttk.Button(
            top, text="Mulai Monitoring", command=self._toggle_monitor, state="disabled"
        )
        self.monitor_btn.pack(side="left", padx=5)

        ttk.Label(top, text="Interval (detik):").pack(side="left", padx=(15, 5))
        self.interval_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(
            top, from_=0.2, to=10, increment=0.2, textvariable=self.interval_var, width=6
        ).pack(side="left")

        self.log_csv_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top, text="Simpan log ke CSV", variable=self.log_csv_var, command=self._toggle_csv_path
        ).pack(side="left", padx=(20, 5))
        self.csv_path_var = tk.StringVar(value="obd2_log.csv")
        self.csv_entry = ttk.Entry(top, textvariable=self.csv_path_var, width=20, state="disabled")
        self.csv_entry.pack(side="left")
        self.csv_browse_btn = ttk.Button(top, text="...", width=3, command=self._browse_csv, state="disabled")
        self.csv_browse_btn.pack(side="left", padx=3)

        # ---- Panel pengaturan estimasi jarak tempuh ----
        range_frame = ttk.LabelFrame(scroll_area, text="Pengaturan Estimasi Jarak Tempuh")
        range_frame.pack(fill="x", padx=10, pady=(0, 5))

        ttk.Label(range_frame, text="Kapasitas Tangki (liter):").grid(
            row=0, column=0, padx=5, pady=8, sticky="w"
        )
        # Default 50L: kapasitas tangki generik, banyak dipakai sedan/SUV kelas
        # menengah - SELALU sesuaikan dengan spek tangki kendaraan kamu sendiri.
        self.tank_capacity_var = tk.DoubleVar(value=50.0)
        tank_spin = ttk.Spinbox(
            range_frame, from_=5, to=200, increment=1, textvariable=self.tank_capacity_var, width=8,
            command=self._on_tank_capacity_change,
        )
        tank_spin.grid(row=0, column=1, padx=5, pady=8, sticky="w")
        tank_spin.bind("<FocusOut>", lambda e: self._on_tank_capacity_change())
        tank_spin.bind("<Return>", lambda e: self._on_tank_capacity_change())

        ttk.Label(range_frame, text="Konsumsi BBM rata-rata (km/liter):").grid(
            row=0, column=2, padx=(20, 5), pady=8, sticky="w"
        )
        # Default 10 km/L: perkiraan umum gabungan dalam/luar kota, netral
        # antar merek - SELALU sesuaikan dengan rata-rata konsumsi aktual kendaraan kamu.
        self.consumption_var = tk.DoubleVar(value=10.0)
        ttk.Spinbox(
            range_frame, from_=1, to=40, increment=0.5, textvariable=self.consumption_var, width=8
        ).grid(row=0, column=3, padx=5, pady=8, sticky="w")

        ttk.Label(
            range_frame,
            text="(Sesuaikan dengan spek tangki & rata-rata konsumsi BBM kendaraan kamu - berlaku untuk merek apa pun)",
            foreground="gray",
        ).grid(row=1, column=0, columnspan=4, padx=5, pady=(0, 8), sticky="w")

        # ---- Fallback: input manual level BBM, bisa lewat PERSEN atau LITER ----
        # Banyak kendaraan diesel/GM TIDAK mengirim data FUEL_LEVEL lewat OBD2
        # standar (PID 012F), karena level BBM dibaca dari modul instrumen/body,
        # bukan ECU mesin. Kalau itu terjadi, dua field ini bisa dipakai supaya
        # estimasi tetap bisa dihitung manual - isi salah satu (persen ATAU
        # liter), yang lain otomatis mengikuti berdasarkan Kapasitas Tangki.
        self.manual_fuel_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            range_frame,
            text="Level BBM tidak terbaca dari ECU? Input manual:",
            variable=self.manual_fuel_var,
            command=self._toggle_manual_fuel,
        ).grid(row=2, column=0, columnspan=2, padx=5, pady=(0, 8), sticky="w")

        self._fuel_sync_guard = False  # cegah loop tak terhingga saat sinkronisasi %<->liter

        self.manual_fuel_pct_var = tk.DoubleVar(value=50.0)
        self.manual_fuel_spin = ttk.Spinbox(
            range_frame,
            from_=0,
            to=100,
            increment=5,
            textvariable=self.manual_fuel_pct_var,
            width=8,
            state="disabled",
            command=self._on_manual_fuel_pct_change,
        )
        self.manual_fuel_spin.grid(row=2, column=2, padx=5, pady=(0, 8), sticky="w")
        self.manual_fuel_spin.bind("<FocusOut>", lambda e: self._on_manual_fuel_pct_change())
        self.manual_fuel_spin.bind("<Return>", lambda e: self._on_manual_fuel_pct_change())
        ttk.Label(range_frame, text="% BBM tersisa").grid(row=2, column=3, padx=5, pady=(0, 8), sticky="w")

        self.manual_fuel_liter_var = tk.DoubleVar(value=self._pct_to_liters(50.0))
        self.manual_fuel_liter_spin = ttk.Spinbox(
            range_frame,
            from_=0,
            to=200,
            increment=1,
            textvariable=self.manual_fuel_liter_var,
            width=8,
            state="disabled",
            command=self._on_manual_fuel_liter_change,
        )
        self.manual_fuel_liter_spin.grid(row=3, column=2, padx=5, pady=(0, 8), sticky="w")
        self.manual_fuel_liter_spin.bind("<FocusOut>", lambda e: self._on_manual_fuel_liter_change())
        self.manual_fuel_liter_spin.bind("<Return>", lambda e: self._on_manual_fuel_liter_change())
        ttk.Label(range_frame, text="liter BBM tersisa").grid(row=3, column=3, padx=5, pady=(0, 8), sticky="w")

        # ---- Kartu estimasi jarak tempuh (highlight, ditampilkan terpisah) ----
        estimate_frame = ttk.LabelFrame(scroll_area, text="Estimasi Jarak Tempuh Tersisa")
        estimate_frame.pack(fill="x", padx=10, pady=(0, 5))
        self.range_estimate_var = tk.StringVar(value="-- km")
        ttk.Label(
            estimate_frame, textvariable=self.range_estimate_var, font=("Segoe UI", 24, "bold"), foreground="#0a6b0a"
        ).pack(padx=10, pady=10)
        self.range_detail_var = tk.StringVar(value="Sambungkan & mulai monitoring untuk melihat estimasi.")
        ttk.Label(estimate_frame, textvariable=self.range_detail_var, foreground="gray").pack(
            padx=10, pady=(0, 10)
        )

        # Grid parameter
        grid_frame = ttk.Frame(scroll_area)
        grid_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.value_labels = {}
        self.ref_labels = {}
        normal_ranges = get_normal_ranges(self.vehicle_fuel_type_var.get())
        for i, cmd in enumerate(MONITOR_COMMANDS):
            row, col = divmod(i, 2)
            card = ttk.LabelFrame(grid_frame, text=cmd.name)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
            grid_frame.columnconfigure(col, weight=1)

            val_label = ttk.Label(card, text="--", font=("Segoe UI", 20, "bold"))
            val_label.pack(padx=10, pady=(10, 2))
            self.value_labels[cmd.name] = val_label

            ref_label = ttk.Label(
                card,
                text="",
                foreground="gray",
                font=("Segoe UI", 8),
                wraplength=260,
                justify="center",
            )
            ref_label.pack(padx=10, pady=(0, 10))
            self.ref_labels[cmd.name] = ref_label

        self._refresh_reference_labels(normal_ranges)

    def _refresh_reference_labels(self, normal_ranges=None):
        """Perbarui teks 'Acuan: ...' di tiap kartu Live Monitor sesuai Jenis
        Bahan Bakar yang dipilih di Profil Kendaraan."""
        if normal_ranges is None:
            normal_ranges = get_normal_ranges(self.vehicle_fuel_type_var.get())
        for name, label in self.ref_labels.items():
            ref_text = normal_ranges.get(name, "")
            label.configure(text=f"Acuan: {ref_text}" if ref_text else "")

    def _on_vehicle_profile_changed(self):
        self._refresh_reference_labels()
        self._sync_ai_vehicle_context()

    def _sync_ai_vehicle_context(self):
        """Isi otomatis catatan konteks kendaraan di tab AI Diagnostic dari
        Profil Kendaraan (Merek + Model + Tahun + Jenis Bahan Bakar), TANPA
        menimpa kalau user sudah mengubahnya sendiri secara manual."""
        if not hasattr(self, "ai_vehicle_context_var"):
            return
        brand = self.vehicle_brand_var.get().strip()
        model = self.vehicle_model_var.get().strip() if hasattr(self, "vehicle_model_var") else ""
        year = self.vehicle_year_var.get().strip() if hasattr(self, "vehicle_year_var") else ""
        fuel_type = self.vehicle_fuel_type_var.get()

        parts = " ".join(p for p in (brand, model, year) if p)
        auto_value = f"{parts} ({fuel_type})" if parts else fuel_type

        current = self.ai_vehicle_context_var.get()
        if current == "" or current == getattr(self, "_ai_context_auto_value", None):
            self.ai_vehicle_context_var.set(auto_value)
        self._ai_context_auto_value = auto_value

    def _toggle_manual_fuel(self):
        if self.manual_fuel_var.get():
            self.manual_fuel_spin.configure(state="normal")
            self.manual_fuel_liter_spin.configure(state="normal")
            self._on_manual_fuel_pct_change()
        else:
            self.manual_fuel_spin.configure(state="disabled")
            self.manual_fuel_liter_spin.configure(state="disabled")

    def _pct_to_liters(self, pct):
        tank_capacity = self.tank_capacity_var.get() if hasattr(self, "tank_capacity_var") else 50.0
        return round((pct / 100.0) * tank_capacity, 1)

    def _liters_to_pct(self, liters):
        tank_capacity = self.tank_capacity_var.get() if hasattr(self, "tank_capacity_var") else 50.0
        if tank_capacity <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (liters / tank_capacity) * 100.0)), 1)

    def _on_manual_fuel_pct_change(self):
        """User mengubah field PERSEN -> hitung ulang field LITER supaya tetap sinkron."""
        if self._fuel_sync_guard:
            return
        self._fuel_sync_guard = True
        try:
            self.manual_fuel_liter_var.set(self._pct_to_liters(self.manual_fuel_pct_var.get()))
        finally:
            self._fuel_sync_guard = False
        if self.manual_fuel_var.get():
            self._compute_range_estimate({}, force_manual=True)

    def _on_manual_fuel_liter_change(self):
        """User mengubah field LITER -> hitung ulang field PERSEN supaya tetap sinkron."""
        if self._fuel_sync_guard:
            return
        self._fuel_sync_guard = True
        try:
            self.manual_fuel_pct_var.set(self._liters_to_pct(self.manual_fuel_liter_var.get()))
        finally:
            self._fuel_sync_guard = False
        if self.manual_fuel_var.get():
            self._compute_range_estimate({}, force_manual=True)

    def _on_tank_capacity_change(self):
        """Kapasitas tangki berubah -> field liter manual perlu disesuaikan
        supaya tetap konsisten dengan field persen (persen tetap jadi acuan)."""
        if hasattr(self, "manual_fuel_pct_var") and not self._fuel_sync_guard:
            self._fuel_sync_guard = True
            try:
                self.manual_fuel_liter_var.set(self._pct_to_liters(self.manual_fuel_pct_var.get()))
            finally:
                self._fuel_sync_guard = False
            if self.manual_fuel_var.get():
                self._compute_range_estimate({}, force_manual=True)

    def _build_dtc_tab(self):
        top = ttk.Frame(self.tab_dtc)
        top.pack(fill="x", pady=8)

        self.read_dtc_btn = ttk.Button(top, text="Baca DTC", command=self._read_dtc, state="disabled")
        self.read_dtc_btn.pack(side="left", padx=5)

        self.clear_dtc_btn = ttk.Button(
            top, text="Hapus DTC (Clear Check Engine)", command=self._clear_dtc, state="disabled"
        )
        self.clear_dtc_btn.pack(side="left", padx=5)

        self.dtc_tree = self._make_scrollable_treeview(
            self.tab_dtc,
            columns=("code", "description"),
            headings=("Kode", "Deskripsi"),
            widths=(100, 550),
            height=15,
        )

    # ===========================================================
    # TAB: FREEZE FRAME
    # ===========================================================
    def _build_freeze_tab(self):
        top = ttk.Frame(self.tab_freeze)
        top.pack(fill="x", pady=8, padx=10)

        ttk.Label(
            top,
            text=(
                "Freeze Frame = snapshot kondisi mesin PERSIS saat DTC pertama kali muncul. "
                "Berguna untuk tahu penyebab error (mis. RPM/suhu saat itu), bukan cuma kodenya saja."
            ),
            foreground="gray",
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self.read_freeze_btn = ttk.Button(
            top, text="Baca Freeze Frame", command=self._read_freeze_frame, state="disabled"
        )
        self.read_freeze_btn.pack(anchor="w")

        self.freeze_dtc_var = tk.StringVar(value="Belum ada data. Klik 'Baca Freeze Frame'.")
        ttk.Label(self.tab_freeze, textvariable=self.freeze_dtc_var, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=10, pady=(4, 8)
        )

        self.freeze_tree = self._make_scrollable_treeview(
            self.tab_freeze,
            columns=("param", "value"),
            headings=("Parameter", "Nilai saat DTC muncul"),
            widths=(280, 300),
            height=14,
        )

    def _read_freeze_frame(self):
        self.read_freeze_btn.configure(state="disabled")
        threading.Thread(target=self._read_freeze_frame_worker, daemon=True).start()

    def _read_freeze_frame_worker(self):
        try:
            dtc_response = self.connection.query(obd.commands.FREEZE_DTC)
        except Exception as e:
            self._log(f"Gagal membaca Freeze Frame: {e}")
            self.after(0, lambda: self.read_freeze_btn.configure(state="normal"))
            return

        if dtc_response.is_null() or not dtc_response.value:
            dtc_label = "Tidak ada Freeze Frame tersimpan (belum pernah ada DTC yang tercatat)."
        else:
            code, desc = dtc_response.value
            dtc_label = f"Freeze Frame direkam saat DTC: {code} - {desc}"

        rows = []
        for cmd in FREEZE_FRAME_COMMANDS:
            display_name = cmd.name.replace("DTC_", "")
            try:
                response = self.connection.query(cmd)
                val = "Tidak didukung / N/A" if response.is_null() else str(response.value)
            except Exception as e:
                val = f"Error: {e}"
            rows.append((display_name, val))

        self.after(0, lambda: self._show_freeze_result(dtc_label, rows))

    def _show_freeze_result(self, dtc_label, rows):
        self.freeze_dtc_var.set(dtc_label)
        for item in self.freeze_tree.get_children():
            self.freeze_tree.delete(item)
        for name, val in rows:
            self.freeze_tree.insert("", "end", values=(name, val))
        self.read_freeze_btn.configure(state="normal")
        self._log("Pembacaan Freeze Frame selesai.")

    # ===========================================================
    # TAB: READINESS MONITORS (status kesiapan sistem emisi)
    # ===========================================================
    def _build_readiness_tab(self):
        top = ttk.Frame(self.tab_readiness)
        top.pack(fill="x", pady=8, padx=10)

        ttk.Label(
            top,
            text=(
                "Readiness Monitors = status kesiapan sistem emisi (dipakai saat uji emisi/smog test). "
                "'Ready' berarti sistem sudah selesai dites ECU sejak DTC terakhir dihapus."
            ),
            foreground="gray",
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self.read_readiness_btn = ttk.Button(
            top, text="Cek Readiness Monitors", command=self._read_readiness, state="disabled"
        )
        self.read_readiness_btn.pack(anchor="w")

        summary_frame = ttk.Frame(self.tab_readiness)
        summary_frame.pack(fill="x", padx=10, pady=(8, 4))

        self.mil_status_var = tk.StringVar(value="MIL (Check Engine): --")
        ttk.Label(summary_frame, textvariable=self.mil_status_var, font=("Segoe UI", 11, "bold")).pack(
            anchor="w"
        )
        self.dtc_count_var = tk.StringVar(value="Jumlah DTC aktif: --")
        ttk.Label(summary_frame, textvariable=self.dtc_count_var).pack(anchor="w")
        self.ignition_type_var = tk.StringVar(value="Tipe mesin: --")
        ttk.Label(summary_frame, textvariable=self.ignition_type_var).pack(anchor="w")

        self.readiness_tree = self._make_scrollable_treeview(
            self.tab_readiness,
            columns=("test", "status"),
            headings=("Sistem yang Dites", "Status"),
            widths=(320, 260),
            height=12,
        )

    def _read_readiness(self):
        self.read_readiness_btn.configure(state="disabled")
        threading.Thread(target=self._read_readiness_worker, daemon=True).start()

    def _read_readiness_worker(self):
        try:
            response = self.connection.query(obd.commands.STATUS)
        except Exception as e:
            self._log(f"Gagal membaca Readiness Monitors: {e}")
            self.after(0, lambda: self.read_readiness_btn.configure(state="normal"))
            return

        if response.is_null():
            self.after(0, lambda: self._show_readiness_error())
            return

        status = response.value
        self.after(0, lambda: self._show_readiness_result(status))

    def _show_readiness_error(self):
        self.mil_status_var.set("MIL (Check Engine): tidak ada data")
        self.dtc_count_var.set("Jumlah DTC aktif: --")
        self.ignition_type_var.set("Tipe mesin: --")
        for item in self.readiness_tree.get_children():
            self.readiness_tree.delete(item)
        self.read_readiness_btn.configure(state="normal")
        self._log("ECU tidak memberi data Readiness Monitors.")

    def _show_readiness_result(self, status):
        self.mil_status_var.set(
            f"MIL (Check Engine): {'MENYALA' if status.MIL else 'Mati (OK)'}"
        )
        self.dtc_count_var.set(f"Jumlah DTC aktif: {status.DTC_count}")
        ignition_label = {
            "spark": "Bensin (spark ignition)",
            "compression": "Diesel (compression ignition)",
        }.get(status.ignition_type, status.ignition_type or "Tidak diketahui")
        self.ignition_type_var.set(f"Tipe mesin: {ignition_label}")

        for item in self.readiness_tree.get_children():
            self.readiness_tree.delete(item)

        # BASE_TESTS berlaku untuk semua mesin; SPARK/COMPRESSION dipilih
        # sesuai ignition_type yang dilaporkan ECU.
        from obd.decoders import BASE_TESTS, COMPRESSION_TESTS, SPARK_TESTS

        relevant_names = list(BASE_TESTS)
        if status.ignition_type == "compression":
            relevant_names += COMPRESSION_TESTS
        else:
            relevant_names += SPARK_TESTS

        for name in relevant_names:
            if not name:
                continue
            test = status.__dict__.get(name)
            if test is None:
                continue
            display_name = name.replace("_MONITORING", "").replace("_", " ").title()
            if not test.available:
                status_text = "Tidak berlaku untuk kendaraan ini"
            elif test.complete:
                status_text = "Ready (sudah dites)"
            else:
                status_text = "Not Ready (belum selesai dites)"
            self.readiness_tree.insert("", "end", values=(display_name, status_text))

        self.read_readiness_btn.configure(state="normal")
        self._log("Pembacaan Readiness Monitors selesai.")

    # ===========================================================
    # TAB: O2 SENSOR & FUEL TRIM (+ VIN ditampilkan di sini juga)
    # ===========================================================
    def _build_o2_tab(self):
        top = ttk.Frame(self.tab_o2)
        top.pack(fill="x", pady=8, padx=10)

        ttk.Label(
            top,
            text=(
                "O2 Sensor & Fuel Trim membantu diagnosa efisiensi pembakaran / boros BBM. "
                "CATATAN: mesin diesel umumnya TIDAK memakai O2 sensor seperti mesin bensin, "
                "jadi bagian ini bisa saja tidak didukung ECU diesel di berbagai merek."
            ),
            foreground="gray",
            wraplength=680,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        btn_row = ttk.Frame(top)
        btn_row.pack(anchor="w")
        self.read_o2_btn = ttk.Button(
            btn_row, text="Baca O2 Sensor & Fuel Trim", command=self._read_o2_fuel_trim, state="disabled"
        )
        self.read_o2_btn.pack(side="left")

        self.read_vin_btn = ttk.Button(btn_row, text="Baca VIN", command=self._read_vin, state="disabled")
        self.read_vin_btn.pack(side="left", padx=(10, 0))

        self.vin_var = tk.StringVar(value="VIN: -- (klik 'Baca VIN')")
        ttk.Label(self.tab_o2, textvariable=self.vin_var, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=10, pady=(4, 8)
        )

        self.o2_tree = self._make_scrollable_treeview(
            self.tab_o2,
            columns=("param", "value"),
            headings=("Parameter", "Nilai"),
            widths=(280, 300),
            height=13,
        )

    def _read_o2_fuel_trim(self):
        self.read_o2_btn.configure(state="disabled")
        threading.Thread(target=self._read_o2_fuel_trim_worker, daemon=True).start()

    def _read_o2_fuel_trim_worker(self):
        rows = []
        any_supported = False
        supported = self.connection.supported_commands
        for cmd in O2_SENSOR_COMMANDS + FUEL_TRIM_COMMANDS:
            if cmd not in supported:
                rows.append((cmd.name, "Tidak didukung ECU ini"))
                continue
            try:
                response = self.connection.query(cmd)
                if response.is_null():
                    rows.append((cmd.name, "N/A"))
                else:
                    rows.append((cmd.name, str(response.value)))
                    any_supported = True
            except Exception as e:
                rows.append((cmd.name, f"Error: {e}"))

        self.after(0, lambda: self._show_o2_result(rows, any_supported))

    def _show_o2_result(self, rows, any_supported):
        for item in self.o2_tree.get_children():
            self.o2_tree.delete(item)
        for name, val in rows:
            self.o2_tree.insert("", "end", values=(name, val))
        self.read_o2_btn.configure(state="normal")
        if not any_supported:
            self._log(
                "Tidak ada data O2 Sensor/Fuel Trim yang didukung ECU ini "
                "(wajar untuk banyak mesin diesel, terlepas dari mereknya)."
            )
        else:
            self._log("Pembacaan O2 Sensor & Fuel Trim selesai.")

    def _read_vin(self):
        self.read_vin_btn.configure(state="disabled")
        threading.Thread(target=self._read_vin_worker, daemon=True).start()

    def _read_vin_worker(self):
        try:
            response = self.connection.query(obd.commands.VIN)
        except Exception as e:
            self._log(f"Gagal membaca VIN: {e}")
            self.after(0, lambda: self.read_vin_btn.configure(state="normal"))
            return

        if response.is_null():
            vin_text = "VIN: tidak didukung / tidak tersedia dari ECU ini"
        else:
            vin_text = f"VIN: {response.value}"

        self.after(0, lambda: self._show_vin_result(vin_text))

    def _show_vin_result(self, vin_text):
        self.vin_var.set(vin_text)
        self.read_vin_btn.configure(state="normal")
        self._log("Pembacaan VIN selesai.")

    # ===========================================================
    # TAB: GRAFIK HISTORIS (trend RPM/suhu/speed dari waktu ke waktu)
    # ===========================================================
    def _build_graph_tab(self):
        top = ttk.Frame(self.tab_graph)
        top.pack(fill="x", pady=8, padx=10)

        ttk.Label(top, text="Pilih parameter untuk digrafik (aktifkan saat Live Monitor berjalan):").pack(
            anchor="w"
        )

        picker_frame = ttk.Frame(top)
        picker_frame.pack(anchor="w", pady=(4, 8))
        self.graph_selected_var = tk.StringVar(value=obd.commands.RPM.name)
        for cmd in GRAPH_COMMANDS:
            ttk.Radiobutton(
                picker_frame,
                text=cmd.name,
                variable=self.graph_selected_var,
                value=cmd.name,
                command=self._reset_graph_data,
            ).pack(side="left", padx=(0, 10))

        # Buffer data historis (rolling window, maksimal 120 titik terakhir)
        self.graph_data = {cmd.name: deque(maxlen=120) for cmd in GRAPH_COMMANDS}
        self.graph_time = deque(maxlen=120)

        self.graph_fig = Figure(figsize=(6.5, 3.8), dpi=90)
        self.graph_ax = self.graph_fig.add_subplot(111)
        self.graph_ax.set_title("Grafik Historis (real-time)")
        self.graph_ax.set_xlabel("Waktu (detik lalu)")
        (self.graph_line,) = self.graph_ax.plot([], [], color="#1f77b4")

        self.graph_canvas = FigureCanvasTkAgg(self.graph_fig, master=self.tab_graph)
        self.graph_canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.graph_canvas.draw()

    def _reset_graph_data(self):
        for cmd in GRAPH_COMMANDS:
            self.graph_data[cmd.name].clear()
        self.graph_time.clear()
        self._redraw_graph()

    def _redraw_graph(self):
        selected = self.graph_selected_var.get()
        raw_y = list(self.graph_data.get(selected, []))
        # Buang titik yang None (parameter tidak terbaca saat itu) supaya garis tidak error
        y_data = [v for v in raw_y if v is not None]
        x_data = list(range(-len(y_data) + 1, 1)) if y_data else []

        self.graph_ax.clear()
        self.graph_ax.set_title(f"Grafik Historis: {selected}")
        self.graph_ax.set_xlabel("Sampel terakhir")
        self.graph_ax.set_ylabel(selected)
        if y_data:
            self.graph_ax.plot(x_data, y_data, color="#1f77b4")
        else:
            self.graph_ax.text(
                0.5, 0.5, "Belum ada data (mulai Live Monitor dulu)",
                ha="center", va="center", transform=self.graph_ax.transAxes, color="gray",
            )
        self.graph_canvas.draw_idle()

    # ===========================================================
    # TAB: DIAGNOSTIK LANJUTAN (Turbo Health, Common Rail Stability, EGR Logic)
    # ===========================================================
    def _build_advanced_diag_tab(self):
        scroll_area = self._make_scrollable(self.tab_advanced)

        ttk.Label(
            scroll_area,
            text=(
                "\u26a0 PENTING: Analisa di bawah ini adalah PERHITUNGAN HEURISTIK "
                "dari data live monitor yang terkumpul, BUKAN diagnosa pasti dari ECU. "
                "Gunakan sebagai sinyal awal 'perlu dicek' saja, bukan pengganti "
                "pemeriksaan bengkel/scan tool profesional. Jalankan Live Monitor "
                "beberapa menit dulu (idle + sedikit RPM tinggi) sebelum menganalisa."
            ),
            foreground="#a05a00",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 10))

        # ---- Turbocharger Health ----
        turbo_frame = ttk.LabelFrame(scroll_area, text="Turbocharger Health")
        turbo_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(
            turbo_frame,
            text=(
                "Membandingkan Intake Manifold Pressure (boost) terhadap Barometric "
                "Pressure saat Engine Load tinggi, untuk indikasi kasar performa turbo."
            ),
            foreground="gray", wraplength=700, justify="left",
        ).pack(anchor="w", padx=8, pady=(8, 4))
        ttk.Button(turbo_frame, text="Analisa Turbo Health", command=self._analyze_turbo).pack(
            anchor="w", padx=8, pady=(0, 4)
        )
        self.turbo_result_var = tk.StringVar(value="Belum dianalisa.")
        ttk.Label(turbo_frame, textvariable=self.turbo_result_var, wraplength=700, justify="left").pack(
            anchor="w", padx=8, pady=(0, 8)
        )

        # ---- Common Rail Stability ----
        rail_frame = ttk.LabelFrame(scroll_area, text="Common Rail Stability")
        rail_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(
            rail_frame,
            text=(
                "Mengukur fluktuasi Fuel Rail Pressure saat kondisi idle. Rail pressure "
                "yang goyang-goyang jauh dari rata-rata bisa indikasi masalah fuel pump/"
                "injector/regulator tekanan rail."
            ),
            foreground="gray", wraplength=700, justify="left",
        ).pack(anchor="w", padx=8, pady=(8, 4))
        ttk.Button(rail_frame, text="Analisa Common Rail Stability", command=self._analyze_common_rail).pack(
            anchor="w", padx=8, pady=(0, 4)
        )
        self.rail_result_var = tk.StringVar(value="Belum dianalisa.")
        ttk.Label(rail_frame, textvariable=self.rail_result_var, wraplength=700, justify="left").pack(
            anchor="w", padx=8, pady=(0, 8)
        )

        # ---- EGR System Logic ----
        egr_frame = ttk.LabelFrame(scroll_area, text="EGR System Logic")
        egr_frame.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(
            egr_frame,
            text=(
                "Membandingkan Commanded EGR (perintah ECU) vs EGR Error (deviasi "
                "aktual). Error yang besar terus-menerus bisa indikasi valve EGR "
                "macet/kotor karbon."
            ),
            foreground="gray", wraplength=700, justify="left",
        ).pack(anchor="w", padx=8, pady=(8, 4))
        ttk.Button(egr_frame, text="Analisa EGR System Logic", command=self._analyze_egr).pack(
            anchor="w", padx=8, pady=(0, 4)
        )
        self.egr_result_var = tk.StringVar(value="Belum dianalisa.")
        ttk.Label(egr_frame, textvariable=self.egr_result_var, wraplength=700, justify="left").pack(
            anchor="w", padx=8, pady=(0, 8)
        )

    def _analyze_turbo(self):
        intake = list(self.diag_buffers["INTAKE_PRESSURE"])
        baro = list(self.diag_buffers["BAROMETRIC_PRESSURE"])
        load = list(self.diag_buffers["ENGINE_LOAD"])

        if len(intake) < 5 or len(load) < 5:
            self.turbo_result_var.set(
                "Data belum cukup. Jalankan Live Monitor dulu (idle beberapa saat, "
                "lalu injak gas sebentar biar ada variasi Engine Load)."
            )
            return

        baseline_baro = statistics.mean(baro) if baro else 101.0
        # Ambil sampel saat load tinggi (>60%) untuk menilai boost saat dibutuhkan
        high_load_pairs = [(i, l) for i, l in zip(intake, load) if l > 60]

        if not high_load_pairs:
            self.turbo_result_var.set(
                "Belum ada sampel Engine Load tinggi (>60%) selama monitoring. "
                "Coba injak gas / jalan sedikit lebih agresif lalu analisa ulang."
            )
            return

        avg_boost = statistics.mean([i for i, l in high_load_pairs])
        boost_above_atm = avg_boost - baseline_baro
        max_intake = max(intake)

        if boost_above_atm < 5:
            status = (
                f"Boost rendah (\u2248 {boost_above_atm:.1f} kPa di atas atmosfer saat load tinggi). "
                "Indikasi kemungkinan: turbo lag berlebihan, kebocoran pipa intake/intercooler, "
                "atau wastegate/actuator bermasalah. SARAN: cek fisik pipa intercooler & "
                "boost hose ke bengkel."
            )
        elif boost_above_atm > 150:
            status = (
                f"Boost sangat tinggi (\u2248 {boost_above_atm:.1f} kPa di atas atmosfer). "
                "Indikasi kemungkinan overboost - cek wastegate/actuator. "
                "SARAN: periksa ke bengkel, overboost bisa membebani mesin berlebihan."
            )
        else:
            status = (
                f"Boost terlihat wajar (\u2248 {boost_above_atm:.1f} kPa di atas atmosfer saat load tinggi, "
                f"puncak {max_intake:.0f} kPa). Tidak ada indikasi masalah turbo dari data ini."
            )

        self.turbo_result_var.set(status)
        self._log_ai_analysis("Turbocharger Health", status)

    def _analyze_common_rail(self):
        rpm = list(self.diag_buffers["RPM"])
        rail = list(self.diag_buffers["FUEL_RAIL_PRESSURE_DIRECT"])

        if len(rail) < 5 or len(rpm) < 5:
            self.rail_result_var.set("Data belum cukup. Jalankan Live Monitor dulu beberapa menit saat idle.")
            return

        idle_pressures = [
            r for r, p in zip(rail, rpm) if IDLE_RPM_RANGE[0] <= p <= IDLE_RPM_RANGE[1]
        ]

        if len(idle_pressures) < 5:
            self.rail_result_var.set(
                "Belum ada cukup sampel saat kondisi idle "
                f"({IDLE_RPM_RANGE[0]}-{IDLE_RPM_RANGE[1]} rpm). Diamkan mesin idle sebentar lalu analisa ulang."
            )
            return

        mean_p = statistics.mean(idle_pressures)
        stdev_p = statistics.stdev(idle_pressures) if len(idle_pressures) > 1 else 0.0
        cv_pct = (stdev_p / mean_p * 100.0) if mean_p else 0.0

        if cv_pct > 8:
            status = (
                f"Fluktuasi rail pressure TINGGI saat idle (\u2248 {mean_p:.0f} bar \u00b1{stdev_p:.1f} bar, "
                f"variasi {cv_pct:.1f}%). Indikasi kemungkinan: fuel pump aus, injector bocor/tidak presisi, "
                "atau regulator tekanan rail bermasalah. SARAN: cek ke bengkel spesialis diesel common-rail."
            )
        elif cv_pct > 4:
            status = (
                f"Fluktuasi rail pressure sedikit di atas normal (\u2248 {mean_p:.0f} bar \u00b1{stdev_p:.1f} bar, "
                f"variasi {cv_pct:.1f}%). Belum tentu masalah, tapi perlu dipantau."
            )
        else:
            status = (
                f"Rail pressure stabil saat idle (\u2248 {mean_p:.0f} bar \u00b1{stdev_p:.1f} bar, "
                f"variasi {cv_pct:.1f}%). Tidak ada indikasi masalah dari data ini."
            )

        self.rail_result_var.set(status)
        self._log_ai_analysis("Common Rail Stability", status)

    def _analyze_egr(self):
        commanded = list(self.diag_buffers["COMMANDED_EGR"])
        error = list(self.diag_buffers["EGR_ERROR"])

        if len(error) < 5:
            self.egr_result_var.set("Data belum cukup. Jalankan Live Monitor dulu beberapa menit.")
            return

        active_errors = [e for c, e in zip(commanded, error) if c > 0]
        sample = active_errors if active_errors else error
        avg_abs_error = statistics.mean([abs(e) for e in sample])
        max_abs_error = max([abs(e) for e in sample])

        if avg_abs_error > 20:
            status = (
                f"Deviasi EGR Error besar (rata-rata \u2248 {avg_abs_error:.1f}%, puncak {max_abs_error:.1f}%). "
                "Indikasi kemungkinan: valve EGR macet/kotor karbon, atau sensor posisi EGR bermasalah. "
                "SARAN: cek & bersihkan valve EGR ke bengkel."
            )
        elif avg_abs_error > 10:
            status = (
                f"Deviasi EGR Error sedikit di atas normal (rata-rata \u2248 {avg_abs_error:.1f}%). "
                "Belum tentu masalah, tapi perlu dipantau terutama kalau ada gejala asap/tenaga kurang."
            )
        else:
            status = (
                f"EGR bekerja sesuai perintah ECU (rata-rata deviasi \u2248 {avg_abs_error:.1f}%). "
                "Tidak ada indikasi masalah dari data ini."
            )

        self.egr_result_var.set(status)
        self._log_ai_analysis("EGR System Logic", status)

    # ===========================================================
    # TAB: AI DIAGNOSTIC
    # ===========================================================
    def _build_ai_diagnostic_tab(self):
        scroll_area = self._make_scrollable(self.tab_ai_diag)

        ttk.Label(
            scroll_area,
            text=(
                "Diagnostic Assistant bisa jalan 2 cara: (1) LOKAL - rule-based, "
                "offline, tanpa API key sama sekali; atau (2) ONLINE lewat AI "
                "sungguhan (Gemini/Groq) yang PUNYA TIER GRATIS (tanpa kartu "
                "kredit) - tapi tetap butuh internet & API key gratis dari "
                "provider terkait.\n\n"
                "CATATAN: kebijakan free-tier provider AI bisa berubah sewaktu-"
                "waktu di luar kendali aplikasi ini - kalau ada error 'quota' "
                "atau 'rate limit', itu wajar & bukan bug aplikasi."
            ),
            foreground="#a05a00",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 10))

        source_frame = ttk.LabelFrame(scroll_area, text="Sumber Analisa")
        source_frame.pack(fill="x", padx=10, pady=(0, 10))

        ttk.Label(source_frame, text="Pilih sumber:").grid(row=0, column=0, padx=8, pady=8, sticky="w")
        self.ai_source_var = tk.StringVar(value="Lokal (Rule-Based, Offline)")
        source_combo = ttk.Combobox(
            source_frame,
            textvariable=self.ai_source_var,
            values=["Lokal (Rule-Based, Offline)"] + list(AI_PROVIDERS.keys()),
            state="readonly",
            width=30,
        )
        source_combo.grid(row=0, column=1, padx=8, pady=8, sticky="w")
        source_combo.bind("<<ComboboxSelected>>", lambda e: self._on_ai_source_changed())

        # ---- Sub-panel konfigurasi online (API key & model), disembunyikan
        # kalau sumber = Lokal ----
        self.ai_online_frame = ttk.Frame(source_frame)
        ttk.Label(self.ai_online_frame, text="API Key:").grid(row=0, column=0, padx=8, pady=4, sticky="w")
        self.ai_api_key_var = tk.StringVar()
        ttk.Entry(self.ai_online_frame, textvariable=self.ai_api_key_var, width=40, show="*").grid(
            row=0, column=1, padx=8, pady=4, sticky="w"
        )
        ttk.Label(self.ai_online_frame, text="Model:").grid(row=1, column=0, padx=8, pady=4, sticky="w")
        self.ai_model_var = tk.StringVar()
        ttk.Entry(self.ai_online_frame, textvariable=self.ai_model_var, width=30).grid(
            row=1, column=1, padx=8, pady=4, sticky="w"
        )
        self.ai_getkey_var = tk.StringVar()
        ttk.Label(self.ai_online_frame, textvariable=self.ai_getkey_var, foreground="blue").grid(
            row=2, column=0, columnspan=2, padx=8, pady=(0, 4), sticky="w"
        )
        ttk.Button(self.ai_online_frame, text="Simpan Konfigurasi", command=self._save_ai_config).grid(
            row=3, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w"
        )
        self.ai_online_frame.grid(row=1, column=0, columnspan=2, padx=0, pady=0, sticky="w")

        if not ONLINE_AI_AVAILABLE:
            ttk.Label(
                source_frame,
                text="\u26a0 Library 'requests' belum terinstall. Jalankan: pip install requests\n"
                     "(dibutuhkan hanya kalau mau pakai sumber Gemini/Groq, mode Lokal tetap bisa jalan)",
                foreground="red",
            ).grid(row=2, column=0, columnspan=2, padx=8, pady=(0, 8), sticky="w")

        ttk.Label(
            scroll_area,
            text="Catatan konteks kendaraan (opsional, ikut ditampilkan di laporan):",
            foreground="gray", wraplength=720, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 4))
        self.ai_vehicle_context_var = tk.StringVar(value="")
        ttk.Entry(scroll_area, textvariable=self.ai_vehicle_context_var, width=70).pack(
            anchor="w", padx=10, pady=(0, 10)
        )
        self._sync_ai_vehicle_context()

        self.run_ai_btn = ttk.Button(
            scroll_area, text="Jalankan Diagnostic Assistant", command=self._run_ai_diagnostic, state="disabled"
        )
        self.run_ai_btn.pack(anchor="w", padx=10, pady=(0, 10))

        self.ai_status_var = tk.StringVar(value="")
        ttk.Label(scroll_area, textvariable=self.ai_status_var, foreground="gray").pack(
            anchor="w", padx=10, pady=(0, 4)
        )

        result_container = ttk.Frame(scroll_area)
        result_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.ai_result_text = tk.Text(result_container, wrap="word", height=18, state="disabled")
        ai_result_vsb = ttk.Scrollbar(result_container, orient="vertical", command=self.ai_result_text.yview)
        self.ai_result_text.configure(yscrollcommand=ai_result_vsb.set)
        self.ai_result_text.pack(side="left", fill="both", expand=True)
        ai_result_vsb.pack(side="right", fill="y")

        self._on_ai_source_changed()
        self._load_ai_config()

    def _on_ai_source_changed(self):
        source = self.ai_source_var.get()
        if source in AI_PROVIDERS:
            provider = AI_PROVIDERS[source]
            self.ai_online_frame.grid()
            if not self.ai_model_var.get():
                self.ai_model_var.set(provider["default_model"])
            self.ai_getkey_var.set(f"Dapatkan API key gratis di: {provider['get_key_url']}")
        else:
            self.ai_online_frame.grid_remove()

    def _load_ai_config(self):
        if not os.path.exists(AI_CONFIG_FILE):
            return
        try:
            with open(AI_CONFIG_FILE, "r") as f:
                data = json.load(f)
            source = data.get("source", "Lokal (Rule-Based, Offline)")
            if source in AI_PROVIDERS or source == "Lokal (Rule-Based, Offline)":
                self.ai_source_var.set(source)
            self.ai_api_key_var.set(data.get("api_key", ""))
            self.ai_model_var.set(data.get("model", ""))
            self._on_ai_source_changed()
        except (json.JSONDecodeError, OSError):
            pass

    def _save_ai_config(self):
        data = {
            "source": self.ai_source_var.get(),
            "api_key": self.ai_api_key_var.get().strip(),
            "model": self.ai_model_var.get().strip(),
        }
        try:
            with open(AI_CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Tersimpan", "Konfigurasi AI berhasil disimpan.")
        except OSError as e:
            messagebox.showerror("Gagal Menyimpan", str(e))

    def _show_ai_diag_result(self, text, category="Diagnostic Assistant"):
        self.ai_status_var.set("Selesai.")
        self.run_ai_btn.configure(state="normal")
        self.ai_result_text.configure(state="normal")
        self.ai_result_text.delete("1.0", "end")
        self.ai_result_text.insert("1.0", text)
        self.ai_result_text.configure(state="disabled")
        self._log_ai_analysis(category, text)

    def _run_ai_diagnostic(self):
        if not self.last_monitor_values:
            messagebox.showwarning(
                "Belum Ada Data", "Jalankan Live Monitor dulu beberapa saat supaya ada data untuk dianalisa."
            )
            return

        source = self.ai_source_var.get()

        if source == "Lokal (Rule-Based, Offline)":
            self.run_ai_btn.configure(state="disabled")
            self.ai_status_var.set("Menganalisa data secara lokal...")
            threading.Thread(target=self._run_local_diagnostic_worker, daemon=True).start()
            return

        # ---- Sumber online (Gemini/Groq) ----
        if not ONLINE_AI_AVAILABLE:
            messagebox.showerror("Library Belum Ada", "Jalankan: pip install requests, lalu restart aplikasi.")
            return
        api_key = self.ai_api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "API Key Kosong",
                f"Isi dulu API Key {source} (gratis, tanpa kartu kredit).\n"
                f"Dapatkan di: {AI_PROVIDERS[source]['get_key_url']}",
            )
            return

        self.run_ai_btn.configure(state="disabled")
        self.ai_status_var.set(f"Mengirim data ke {source}, mohon tunggu...")
        threading.Thread(
            target=self._run_online_diagnostic_worker, args=(source, api_key), daemon=True
        ).start()

    def _run_local_diagnostic_worker(self):
        try:
            report = self._build_local_diagnostic_report()
        except Exception as e:
            self.msg_queue.put(("ai_diag_result", (f"Terjadi error saat menyusun laporan:\n{e}", "Diagnostic Assistant (Lokal)")))
            return
        self.msg_queue.put(("ai_diag_result", (report, "Diagnostic Assistant (Lokal)")))

    def _run_online_diagnostic_worker(self, source, api_key):
        provider = AI_PROVIDERS[source]
        model = self.ai_model_var.get().strip() or provider["default_model"]
        prompt = self._build_ai_prompt_text()
        try:
            if provider["key"] == "gemini":
                text = self._call_gemini(api_key, model, prompt)
            else:
                text = self._call_groq(api_key, model, prompt)
        except Exception as e:
            text = (
                f"Terjadi error saat memanggil {source}:\n{e}\n\n"
                "Kemungkinan penyebab: API key salah/kadaluarsa, kuota gratis "
                "harian habis, tidak ada koneksi internet, atau model yang "
                "diisi tidak valid. Coba lagi nanti atau pakai sumber Lokal."
            )
        self.msg_queue.put(("ai_diag_result", (text, f"Diagnostic Assistant ({source})")))

    def _call_gemini(self, api_key, model, prompt):
        url = AI_PROVIDERS["Gemini (Google - Gratis)"]["endpoint"].format(model=model, api_key=api_key)
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Respons kosong dari Gemini: {data}")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts).strip() or "(Respons kosong)"

    def _call_groq(self, api_key, model, prompt):
        url = AI_PROVIDERS["Groq (Llama - Gratis)"]["endpoint"]
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1500,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError(f"Respons kosong dari Groq: {data}")
        return choices[0]["message"]["content"].strip() or "(Respons kosong)"

    def _build_ai_prompt_text(self):
        """Susun prompt teks untuk dikirim ke AI online (Gemini/Groq)."""
        lines = [
            "Kamu adalah asisten diagnosa kendaraan. Analisa data OBD2 berikut dan "
            "berikan penjelasan dalam Bahasa Indonesia yang mudah dipahami awam, "
            "dengan format: (1) Ringkasan kondisi umum, (2) Hal yang perlu diperhatikan "
            "(kalau ada), (3) Saran tindak lanjut. Jangan mengklaim diagnosa pasti - "
            "sampaikan sebagai indikasi awal saja dan sarankan cek bengkel untuk "
            "konfirmasi kalau ada tanda mencurigakan.",
            "",
            f"Kendaraan: {self.ai_vehicle_context_var.get().strip() or 'Tidak disebutkan'}",
            "",
            "=== Data Live Monitor Terakhir ===",
        ]
        for name, val in self.last_monitor_values.items():
            lines.append(f"- {name}: {val}")

        if self.last_dtc_codes is not None:
            lines.append("")
            lines.append("=== DTC (Kode Error) ===")
            if self.last_dtc_codes:
                lines.append(f"Kode aktif: {', '.join(self.last_dtc_codes)}")
            else:
                lines.append("Tidak ada DTC tersimpan.")

        lines.append("")
        lines.append("=== Hasil Diagnostik Lanjutan (kalau sudah dianalisa) ===")
        lines.append(f"- Turbocharger Health: {self.turbo_result_var.get()}")
        lines.append(f"- Common Rail Stability: {self.rail_result_var.get()}")
        lines.append(f"- EGR System Logic: {self.egr_result_var.get()}")

        return "\n".join(lines)

    def _build_local_diagnostic_report(self):
        """
        Susun laporan diagnosa otomatis SECARA LOKAL (rule-based), tanpa API/
        internet. Membandingkan nilai live monitor terakhir ke rentang wajar
        (DIAG_NUMERIC_RANGES), lalu digabung dengan hasil Diagnostik Lanjutan
        (Turbo/Rail/EGR) dan status DTC yang sudah dibaca (kalau ada).
        """
        findings = []  # (level, pesan) - level: "ok" / "perhatian" / "waspada"
        diag_numeric_ranges = get_diag_numeric_ranges(self.vehicle_fuel_type_var.get())

        for name, val in self.last_monitor_values.items():
            if name not in diag_numeric_ranges:
                continue
            numeric = self._to_numeric(val)
            if numeric is None:
                continue
            low, high, unit = diag_numeric_ranges[name]
            display_name = name.replace("_", " ").title()
            if numeric < low:
                findings.append((
                    "perhatian",
                    f"{display_name} lebih rendah dari rentang wajar ({numeric:.1f}{unit}, "
                    f"acuan {low}-{high}{unit}).",
                ))
            elif numeric > high:
                findings.append((
                    "waspada",
                    f"{display_name} lebih tinggi dari rentang wajar ({numeric:.1f}{unit}, "
                    f"acuan {low}-{high}{unit}).",
                ))
            else:
                findings.append(("ok", f"{display_name} dalam rentang wajar ({numeric:.1f}{unit})."))

        # Sertakan hasil DTC kalau sudah pernah dibaca di tab DTC
        dtc_note = None
        if self.last_dtc_codes is not None:
            if len(self.last_dtc_codes) == 0:
                dtc_note = "Tidak ada kode error (DTC) tersimpan saat terakhir dicek."
            else:
                dtc_note = f"Ada {len(self.last_dtc_codes)} kode DTC aktif: {', '.join(self.last_dtc_codes)}."

        # Sertakan hasil Diagnostik Lanjutan kalau sudah pernah dijalankan
        advanced_results = []
        for label, var in (
            ("Turbocharger Health", self.turbo_result_var),
            ("Common Rail Stability", self.rail_result_var),
            ("EGR System Logic", self.egr_result_var),
        ):
            text = var.get()
            if text and text != "Belum dianalisa.":
                flagged = any(kw in text for kw in ("TINGGI", "rendah", "SARAN", "waspada", "kemungkinan"))
                advanced_results.append((label, text, flagged))

        # ---- Susun laporan ----
        waspada_list = [msg for lvl, msg in findings if lvl == "waspada"]
        perhatian_list = [msg for lvl, msg in findings if lvl == "perhatian"]
        ok_list = [msg for lvl, msg in findings if lvl == "ok"]

        lines = []
        vehicle = self.ai_vehicle_context_var.get().strip()
        if vehicle:
            lines.append(f"Kendaraan: {vehicle}")
        lines.append(f"Waktu analisa: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        lines.append("=== 1. RINGKASAN KONDISI UMUM ===")
        if not waspada_list and not perhatian_list:
            lines.append(
                "Semua parameter yang terpantau berada dalam rentang wajar. "
                "Tidak ada indikasi masalah dari data live monitor saat ini."
            )
        else:
            lines.append(
                f"Ditemukan {len(waspada_list)} parameter di luar rentang wajar (perlu perhatian lebih) "
                f"dan {len(perhatian_list)} parameter sedikit menyimpang dari rentang wajar."
            )
        if dtc_note:
            lines.append(dtc_note)

        if waspada_list or perhatian_list:
            lines.append("")
            lines.append("=== 2. HAL YANG PERLU DIPERHATIKAN ===")
            for msg in waspada_list:
                lines.append(f"\u26a0 [WASPADA] {msg}")
            for msg in perhatian_list:
                lines.append(f"\u2022 [PERHATIAN] {msg}")

        if advanced_results:
            lines.append("")
            lines.append("=== 3. HASIL DIAGNOSTIK LANJUTAN ===")
            for label, text, flagged in advanced_results:
                marker = "\u26a0" if flagged else "\u2713"
                lines.append(f"{marker} {label}: {text}")

        lines.append("")
        lines.append("=== 4. SARAN TINDAK LANJUT ===")
        if waspada_list:
            lines.append(
                "Ada parameter yang cukup jauh dari rentang wajar - sebaiknya segera "
                "cek ke bengkel untuk pemeriksaan lebih lanjut, terutama kalau juga "
                "disertai gejala fisik (asap, tenaga kurang, suara aneh, dsb)."
            )
        elif perhatian_list:
            lines.append(
                "Ada sedikit penyimpangan dari rentang wajar - belum tentu masalah "
                "serius, tapi ada baiknya dipantau beberapa waktu ke depan atau "
                "ditanyakan ke bengkel saat servis berikutnya."
            )
        else:
            lines.append("Tidak ada tindakan khusus yang diperlukan berdasarkan data saat ini.")

        lines.append("")
        lines.append(
            "\u26a0 Disclaimer: laporan ini dibuat otomatis dari perbandingan angka "
            "ke rentang referensi umum, BUKAN diagnosa pasti dari mekanik/ECU. "
            "Gunakan sebagai sinyal awal saja."
        )

        if ok_list:
            lines.append("")
            lines.append("=== Detail Parameter Normal ===")
            for msg in ok_list:
                lines.append(f"\u2713 {msg}")

        return "\n".join(lines)

    @staticmethod
    def _to_numeric(val):
        if val in (None, "N/A"):
            return None
        if isinstance(val, str) and val.startswith("Error"):
            return None
        try:
            return float(val.magnitude) if hasattr(val, "magnitude") else float(val)
        except (TypeError, ValueError):
            return None

    # ===========================================================
    # TAB: AI ANALYSIS LOG
    # ===========================================================
    def _build_ai_log_tab(self):
        top = ttk.Frame(self.tab_ai_log)
        top.pack(fill="x", padx=10, pady=8)

        ttk.Label(
            top,
            text=(
                "Riwayat semua hasil analisa AI Diagnostic & Diagnostik Lanjutan "
                "(Turbo/Common Rail/EGR), tersimpan otomatis ke file lokal."
            ),
            foreground="gray", wraplength=700, justify="left",
        ).pack(anchor="w")

        btn_row = ttk.Frame(self.tab_ai_log)
        btn_row.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(btn_row, text="Refresh", command=self._refresh_ai_log_view).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Hapus Semua Log", command=self._clear_ai_log).pack(side="left")

        self.ai_log_tree = self._make_scrollable_treeview(
            self.tab_ai_log,
            columns=("time", "category", "summary"),
            headings=("Waktu", "Kategori", "Ringkasan"),
            widths=(140, 160, 400),
            height=10,
        )
        self.ai_log_tree.bind("<<TreeviewSelect>>", self._on_ai_log_select)

        detail_container = ttk.Frame(self.tab_ai_log)
        detail_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.ai_log_detail_text = tk.Text(detail_container, wrap="word", height=10, state="disabled")
        detail_vsb = ttk.Scrollbar(detail_container, orient="vertical", command=self.ai_log_detail_text.yview)
        self.ai_log_detail_text.configure(yscrollcommand=detail_vsb.set)
        self.ai_log_detail_text.pack(side="left", fill="both", expand=True)
        detail_vsb.pack(side="right", fill="y")

        self._ai_log_data = []
        self._refresh_ai_log_view()

    def _load_ai_log(self):
        if not os.path.exists(DIAG_LOG_FILE):
            return []
        try:
            with open(DIAG_LOG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _log_ai_analysis(self, category, text):
        """Simpan satu entri hasil analisa (AI Diagnostic ATAU Diagnostik
        Lanjutan) ke file log lokal, supaya ada riwayatnya."""
        entries = self._load_ai_log()
        entries.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "category": category,
            "text": text,
        })
        try:
            with open(DIAG_LOG_FILE, "w") as f:
                json.dump(entries, f, indent=2)
        except OSError as e:
            self._log(f"Gagal menyimpan AI Analysis Log: {e}")
        if hasattr(self, "ai_log_tree"):
            self.after(0, self._refresh_ai_log_view)

    def _refresh_ai_log_view(self):
        self._ai_log_data = self._load_ai_log()
        for item in self.ai_log_tree.get_children():
            self.ai_log_tree.delete(item)
        for idx, entry in enumerate(reversed(self._ai_log_data)):
            summary = entry["text"].replace("\n", " ")[:80]
            self.ai_log_tree.insert("", "end", iid=str(idx), values=(entry["time"], entry["category"], summary))

    def _on_ai_log_select(self, event):
        selection = self.ai_log_tree.selection()
        if not selection:
            return
        idx = int(selection[0])
        entry = list(reversed(self._ai_log_data))[idx]
        self.ai_log_detail_text.configure(state="normal")
        self.ai_log_detail_text.delete("1.0", "end")
        self.ai_log_detail_text.insert("1.0", f"[{entry['time']}] {entry['category']}\n\n{entry['text']}")
        self.ai_log_detail_text.configure(state="disabled")

    def _clear_ai_log(self):
        if not messagebox.askyesno("Konfirmasi", "Hapus semua riwayat AI Analysis Log?"):
            return
        try:
            if os.path.exists(DIAG_LOG_FILE):
                os.remove(DIAG_LOG_FILE)
        except OSError as e:
            messagebox.showerror("Gagal Menghapus", str(e))
        self._refresh_ai_log_view()
        self.ai_log_detail_text.configure(state="normal")
        self.ai_log_detail_text.delete("1.0", "end")
        self.ai_log_detail_text.configure(state="disabled")

    def _build_oil_tab(self):
        scroll_area = self._make_scrollable(self.tab_oil)

        # ---- Panel kiri: pilih merek & tampilkan panduan manual ----
        container = ttk.Frame(scroll_area)
        container.pack(fill="both", expand=True, padx=10, pady=10)

        left = ttk.LabelFrame(container, text="Panduan Reset Manual (via tombol dashboard)")
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        ttk.Label(left, text="Pilih merek / model kendaraan:").pack(anchor="w", padx=8, pady=(8, 2))
        self.oil_brand_var = tk.StringVar(value=list(OIL_RESET_GUIDE.keys())[0])
        brand_combo = ttk.Combobox(
            left,
            textvariable=self.oil_brand_var,
            values=list(OIL_RESET_GUIDE.keys()),
            state="readonly",
            width=55,
        )
        brand_combo.pack(anchor="w", padx=8, pady=(0, 8))
        brand_combo.bind("<<ComboboxSelected>>", lambda e: self._show_oil_guide())

        guide_text_container = ttk.Frame(left)
        guide_text_container.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.oil_guide_text = tk.Text(guide_text_container, wrap="word", height=12, state="disabled")
        guide_vsb = ttk.Scrollbar(guide_text_container, orient="vertical", command=self.oil_guide_text.yview)
        self.oil_guide_text.configure(yscrollcommand=guide_vsb.set)
        self.oil_guide_text.pack(side="left", fill="both", expand=True)
        guide_vsb.pack(side="right", fill="y")

        ttk.Label(
            left,
            text="Catatan: prosedur ini TIDAK memerlukan adapter OBD, hanya tombol\n"
                 "di dashboard/setir. Selalu cek buku manual untuk memastikan langkah\n"
                 "yang tepat sesuai tahun & varian kendaraan kamu.",
            foreground="gray",
            justify="left",
        ).pack(anchor="w", padx=8, pady=(0, 8))

        self._show_oil_guide()

        # ---- Panel kanan: reset via OBD (experimental) ----
        right = ttk.LabelFrame(container, text="Reset via OBD (Experimental)")
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))

        ttk.Label(
            right,
            text=(
                "Reset oli BUKAN bagian dari standar OBD2, jadi TIDAK dijamin\n"
                "bekerja di kendaraan kamu. Fitur ini hanya mencoba mengirim\n"
                "perintah mode servis umum yang dipakai sebagian adapter/mobil.\n\n"
                "Jika gagal, gunakan panduan manual di sebelah kiri - itu cara\n"
                "yang paling pasti berhasil untuk hampir semua kendaraan."
            ),
            foreground="#a05a00",
            justify="left",
            wraplength=280,
        ).pack(anchor="w", padx=8, pady=8)

        self.oil_reset_obd_btn = ttk.Button(
            right,
            text="Coba Reset Oli via OBD",
            command=self._try_oil_reset_obd,
            state="disabled",
        )
        self.oil_reset_obd_btn.pack(anchor="w", padx=8, pady=(0, 8))

        self.oil_result_var = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.oil_result_var, foreground="gray", wraplength=280, justify="left").pack(
            anchor="w", padx=8, pady=(0, 8)
        )

        # ---- Panel bawah: Tracker Oil Life & Riwayat Ganti Oli ----
        tracker_frame = ttk.LabelFrame(scroll_area, text="Tracker Oil Life & Riwayat Ganti Oli")
        tracker_frame.pack(fill="x", padx=10, pady=(5, 10))
        self._build_oil_tracker_panel(tracker_frame)

    def _build_oil_tracker_panel(self, parent):
        """
        Panel tracker Oil Life mandiri (bukan dari OBD/ECU, karena OBD2 standar
        tidak punya PID Oil Life %). Data diisi manual oleh pengguna lalu dihitung
        estimasi sisa umur oli berdasarkan jarak (km) dan waktu (hari/bulan) sejak
        ganti oli terakhir. Data disimpan otomatis ke file JSON lokal.
        """
        ttk.Label(
            parent,
            text=(
                "Catatan: OBD2 standar tidak menyediakan data Oil Life % secara langsung. "
                "Tracker ini menghitung sendiri berdasarkan data yang kamu masukkan."
            ),
            foreground="gray",
            wraplength=680,
            justify="left",
        ).grid(row=0, column=0, columnspan=4, padx=8, pady=(8, 4), sticky="w")

        # --- Input riwayat ganti oli terakhir ---
        ttk.Label(parent, text="Tanggal ganti oli terakhir (YYYY-MM-DD):").grid(
            row=1, column=0, padx=8, pady=4, sticky="w"
        )
        self.oil_last_date_var = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(parent, textvariable=self.oil_last_date_var, width=14).grid(
            row=1, column=1, padx=8, pady=4, sticky="w"
        )

        ttk.Label(parent, text="Odometer saat ganti oli (km):").grid(
            row=1, column=2, padx=(20, 8), pady=4, sticky="w"
        )
        self.oil_last_odo_var = tk.DoubleVar(value=0.0)
        ttk.Entry(parent, textvariable=self.oil_last_odo_var, width=12).grid(
            row=1, column=3, padx=8, pady=4, sticky="w"
        )

        # --- Interval servis ---
        ttk.Label(parent, text="Interval ganti oli (km):").grid(row=2, column=0, padx=8, pady=4, sticky="w")
        # Default 10.000 km: interval umum servis berkala banyak kendaraan (normal).
        # Untuk kondisi berat (macet parah, jarak pendek terus-menerus), pabrikan
        # umumnya menyarankan interval lebih pendek (mis. 5.000 km) - cek buku manual.
        self.oil_interval_km_var = tk.DoubleVar(value=10000.0)
        ttk.Entry(parent, textvariable=self.oil_interval_km_var, width=12).grid(
            row=2, column=1, padx=8, pady=4, sticky="w"
        )

        ttk.Label(parent, text="Interval ganti oli (bulan):").grid(
            row=2, column=2, padx=(20, 8), pady=4, sticky="w"
        )
        self.oil_interval_month_var = tk.DoubleVar(value=12.0)
        ttk.Entry(parent, textvariable=self.oil_interval_month_var, width=12).grid(
            row=2, column=3, padx=8, pady=4, sticky="w"
        )

        # --- Odometer saat ini (manual, karena OBD2 standar tidak punya PID odometer total) ---
        ttk.Label(parent, text="Odometer saat ini (km):").grid(row=3, column=0, padx=8, pady=4, sticky="w")
        self.oil_current_odo_var = tk.DoubleVar(value=0.0)
        ttk.Entry(parent, textvariable=self.oil_current_odo_var, width=12).grid(
            row=3, column=1, padx=8, pady=4, sticky="w"
        )
        ttk.Label(
            parent,
            text="(isi manual dari spedometer - OBD2 standar tidak punya PID odometer total)",
            foreground="gray",
        ).grid(row=3, column=2, columnspan=2, padx=8, pady=4, sticky="w")

        # --- Tombol aksi ---
        btn_frame = ttk.Frame(parent)
        btn_frame.grid(row=4, column=0, columnspan=4, padx=8, pady=(6, 4), sticky="w")
        ttk.Button(btn_frame, text="Simpan & Hitung", command=self._save_and_compute_oil_tracker).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            btn_frame, text="Catat Ganti Oli Baru Hari Ini", command=self._mark_oil_changed_today
        ).pack(side="left")

        # --- Hasil perhitungan ---
        result_frame = ttk.Frame(parent)
        result_frame.grid(row=5, column=0, columnspan=4, padx=8, pady=(6, 10), sticky="we")

        self.oil_life_pct_var = tk.StringVar(value="-- %")
        ttk.Label(result_frame, text="Sisa Oil Life:", font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w")
        self.oil_life_label = ttk.Label(
            result_frame, textvariable=self.oil_life_pct_var, font=("Segoe UI", 16, "bold")
        )
        self.oil_life_label.grid(row=0, column=1, padx=(8, 30), sticky="w")

        self.oil_life_detail_var = tk.StringVar(value="Isi data di atas lalu klik 'Simpan & Hitung'.")
        ttk.Label(result_frame, textvariable=self.oil_life_detail_var, foreground="gray", justify="left").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 0)
        )

        # Muat data tersimpan (kalau ada) saat aplikasi pertama dibuka
        self._load_oil_tracker()

    def _load_oil_tracker(self):
        if not os.path.exists(OIL_TRACKER_FILE):
            return
        try:
            with open(OIL_TRACKER_FILE, "r") as f:
                data = json.load(f)
            self.oil_last_date_var.set(data.get("last_change_date", date.today().isoformat()))
            self.oil_last_odo_var.set(data.get("last_change_odo", 0.0))
            self.oil_interval_km_var.set(data.get("interval_km", 10000.0))
            self.oil_interval_month_var.set(data.get("interval_month", 12.0))
            self.oil_current_odo_var.set(data.get("current_odo", 0.0))
            self._update_oil_life_display()
        except (json.JSONDecodeError, OSError, ValueError) as e:
            self._log(f"Gagal memuat data tracker oli tersimpan: {e}")

    def _save_oil_tracker(self):
        data = {
            "last_change_date": self.oil_last_date_var.get().strip(),
            "last_change_odo": self.oil_last_odo_var.get(),
            "interval_km": self.oil_interval_km_var.get(),
            "interval_month": self.oil_interval_month_var.get(),
            "current_odo": self.oil_current_odo_var.get(),
        }
        try:
            with open(OIL_TRACKER_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            self._log(f"Gagal menyimpan data tracker oli: {e}")

    def _mark_oil_changed_today(self):
        if not messagebox.askyesno(
            "Konfirmasi",
            "Catat hari ini sebagai tanggal ganti oli terbaru, dengan odometer "
            "saat ini sebagai patokan baru?",
        ):
            return
        self.oil_last_date_var.set(date.today().isoformat())
        self.oil_last_odo_var.set(self.oil_current_odo_var.get())
        self._save_and_compute_oil_tracker()

    def _save_and_compute_oil_tracker(self):
        self._save_oil_tracker()
        self._update_oil_life_display()

    def _update_oil_life_display(self):
        try:
            last_date = datetime.strptime(self.oil_last_date_var.get().strip(), "%Y-%m-%d").date()
        except ValueError:
            self.oil_life_pct_var.set("-- %")
            self.oil_life_detail_var.set(
                "Format tanggal salah. Gunakan format YYYY-MM-DD, contoh: 2026-07-01"
            )
            return

        last_odo = self.oil_last_odo_var.get()
        current_odo = self.oil_current_odo_var.get()
        interval_km = max(self.oil_interval_km_var.get(), 1.0)
        interval_month = max(self.oil_interval_month_var.get(), 0.1)

        km_used = max(current_odo - last_odo, 0.0)
        days_used = max((date.today() - last_date).days, 0)
        interval_days = interval_month * 30.44  # rata-rata hari per bulan

        pct_by_km = min(km_used / interval_km, 1.0)
        pct_by_time = min(days_used / interval_days, 1.0)
        pct_used = max(pct_by_km, pct_by_time)  # dipakai batas yang lebih dulu tercapai (mana pun duluan)
        pct_remaining = max(0.0, 100.0 * (1.0 - pct_used))

        self.oil_life_pct_var.set(f"{pct_remaining:.0f} %")
        if pct_remaining <= 10:
            self.oil_life_label.configure(foreground="#c0392b")  # merah - segera ganti
        elif pct_remaining <= 30:
            self.oil_life_label.configure(foreground="#e08e00")  # oranye - mendekati waktunya
        else:
            self.oil_life_label.configure(foreground="#0a6b0a")  # hijau - masih aman

        km_remaining = max(interval_km - km_used, 0.0)
        days_remaining = max(interval_days - days_used, 0.0)

        self.oil_life_detail_var.set(
            f"Sudah {km_used:.0f} km / {days_used} hari sejak ganti oli terakhir ({last_date.isoformat()}).\n"
            f"Sisa perkiraan: \u2248 {km_remaining:.0f} km atau \u2248 {days_remaining/30.44:.1f} bulan lagi "
            f"(mana yang lebih dulu tercapai)."
        )

    def _show_oil_guide(self):
        steps = OIL_RESET_GUIDE.get(self.oil_brand_var.get(), [])
        self.oil_guide_text.configure(state="normal")
        self.oil_guide_text.delete("1.0", "end")
        self.oil_guide_text.insert("1.0", "\n".join(steps))
        self.oil_guide_text.configure(state="disabled")

    def _try_oil_reset_obd(self):
        if not self.connection or not self.connection.is_connected():
            messagebox.showwarning("Belum Terhubung", "Sambungkan ke adapter OBD2 terlebih dahulu.")
            return
        self.oil_reset_obd_btn.configure(state="disabled")
        self.oil_result_var.set("Mencoba mengirim perintah reset...")
        threading.Thread(target=self._oil_reset_obd_worker, daemon=True).start()

    def _oil_reset_obd_worker(self):
        # Tidak ada PID standar SAE J1979 untuk reset oli, sehingga kita coba
        # perintah mode 0x04 (sama seperti CLEAR_DTC) sebagai satu-satunya
        # command "reset" generik yang didukung ELM327 secara luas.
        # Pada banyak kendaraan ini TIDAK akan mereset indikator oli karena
        # servis reminder disimpan di modul body/instrumen, bukan ECU mesin.
        try:
            response = self.connection.query(obd.commands.CLEAR_DTC)
            success = not response.is_null()
        except Exception as e:
            self._log(f"Gagal mengirim perintah reset oli via OBD: {e}")
            self.msg_queue.put(("oil_reset_done", f"Gagal: {e}"))
            return

        if success:
            msg = (
                "Perintah terkirim. Namun kebanyakan kendaraan TIDAK menyimpan "
                "status servis oli di ECU mesin, jadi indikator mungkin belum "
                "berubah. Cek dashboard - jika masih menyala, gunakan panduan "
                "manual di sebelah kiri."
            )
        else:
            msg = "Adapter tidak memberi respon valid. Gunakan panduan manual di sebelah kiri."

        self._log("Percobaan reset oli via OBD selesai.")
        self.msg_queue.put(("oil_reset_done", msg))

    def _build_info_tab(self):
        text_container = ttk.Frame(self.tab_info)
        text_container.pack(fill="both", expand=True, padx=10, pady=10)

        self.info_text = tk.Text(text_container, state="disabled", wrap="word")
        info_vsb = ttk.Scrollbar(text_container, orient="vertical", command=self.info_text.yview)
        self.info_text.configure(yscrollcommand=info_vsb.set)
        self.info_text.pack(side="left", fill="both", expand=True)
        info_vsb.pack(side="right", fill="y")

        self.refresh_info_btn = ttk.Button(
            self.tab_info, text="Refresh Info", command=self._refresh_info, state="disabled"
        )
        self.refresh_info_btn.pack(pady=(0, 10))

    # ---------------------------------------------------------
    # Logging helper (ke text box bawah, thread-safe via queue)
    # ---------------------------------------------------------
    def _log(self, message):
        self.msg_queue.put(("log", message))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    ts = datetime.now().strftime("%H:%M:%S")
                    self.log_text.insert("end", f"[{ts}] {payload}\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "values":
                    self.last_monitor_values = payload
                    for name, val in payload.items():
                        if name in self.value_labels:
                            self.value_labels[name].configure(text=str(val))
                elif kind == "connected":
                    self._on_connected_ui()
                elif kind == "conn_failed":
                    self._on_connect_failed_ui(payload)
                elif kind == "oil_reset_done":
                    self.oil_result_var.set(payload)
                    self.oil_reset_obd_btn.configure(state="normal")
                elif kind == "range_estimate":
                    text, detail = payload
                    self.range_estimate_var.set(text)
                    self.range_detail_var.set(detail)
                elif kind == "graph_update":
                    self._redraw_graph()
                elif kind == "ai_diag_result":
                    text, category = payload
                    self._show_ai_diag_result(text, category)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ---------------------------------------------------------
    # Koneksi
    # ---------------------------------------------------------
    def _connect(self):
        self.connect_btn.configure(state="disabled")
        self.status_var.set("Status: menghubungkan...")
        try:
            portstr = self._build_connection_portstr()
        except ValueError as e:
            self.status_var.set("Status: gagal terhubung")
            self.connect_btn.configure(state="normal")
            messagebox.showerror("Input Tidak Lengkap", str(e))
            return
        conn_type = self.conn_type_var.get()
        threading.Thread(target=self._connect_worker, args=(portstr, conn_type), daemon=True).start()

    def _connect_worker(self, portstr, conn_type):
        self._log(f"Mencoba koneksi via {conn_type} (portstr={portstr or 'auto-detect'}) ...")
        kwargs = {"fast": False, "timeout": 30}
        if portstr:
            kwargs["portstr"] = portstr
        try:
            connection = obd.OBD(**kwargs)
        except Exception as e:
            self.msg_queue.put(("conn_failed", str(e)))
            return

        if not connection.is_connected():
            hint = ""
            if conn_type == "WiFi":
                hint = (
                    " Pastikan HP/laptop sudah terhubung ke WiFi hotspot adapter OBD2 "
                    "(bukan WiFi rumah/kantor), dan IP:Port sudah benar."
                )
            self.msg_queue.put(("conn_failed", "Adapter tidak merespon / tidak ditemukan." + hint))
            return

        self.connection = connection
        self.msg_queue.put(("connected", None))

    def _on_connected_ui(self):
        proto = self.connection.protocol_name() or "Tidak diketahui"
        self.status_var.set(f"Status: terhubung ({proto})")
        self._log("Berhasil terhubung ke kendaraan.")
        self.connect_btn.configure(state="normal", text="Sambungkan Ulang")
        self.monitor_btn.configure(state="normal")
        self.read_dtc_btn.configure(state="normal")
        self.clear_dtc_btn.configure(state="normal")
        self.oil_reset_obd_btn.configure(state="normal")
        self.read_freeze_btn.configure(state="normal")
        self.read_readiness_btn.configure(state="normal")
        self.read_o2_btn.configure(state="normal")
        self.read_vin_btn.configure(state="normal")
        self.run_ai_btn.configure(state="normal")
        self.refresh_info_btn.configure(state="normal")
        self._refresh_info()

    def _on_connect_failed_ui(self, error_msg):
        self.status_var.set("Status: gagal terhubung")
        self.connect_btn.configure(state="normal")
        self._log(f"Gagal terhubung: {error_msg}")
        messagebox.showerror(
            "Koneksi Gagal",
            "Tidak bisa terhubung ke adapter OBD2.\n\n"
            "Pastikan:\n"
            "- Adapter Bluetooth ELM327 terpasang ke port OBD kendaraan\n"
            "- Kunci kontak minimal posisi ON\n"
            "- Sudah pairing & port sudah benar\n\n"
            f"Detail error: {error_msg}",
        )

    # ---------------------------------------------------------
    # Live Monitor
    # ---------------------------------------------------------
    def _toggle_csv_path(self):
        state = "normal" if self.log_csv_var.get() else "disabled"
        self.csv_entry.configure(state=state)
        self.csv_browse_btn.configure(state=state)

    def _browse_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            self.csv_path_var.set(path)

    def _toggle_monitor(self):
        if self.polling:
            self.polling = False
            self.monitor_btn.configure(text="Mulai Monitoring")
            self._log("Monitoring dihentikan.")
            if self.log_file:
                self.log_file.close()
                self.log_file = None
                self.log_writer = None
        else:
            if self.log_csv_var.get():
                path = self.csv_path_var.get().strip()
                if not path:
                    messagebox.showwarning("Peringatan", "Isi dulu path file CSV.")
                    return
                self.log_file = open(path, "w", newline="")
                self.log_writer = csv.writer(self.log_file)
                self.log_writer.writerow(["timestamp"] + [c.name for c in MONITOR_COMMANDS])

            self.polling = True
            self.monitor_btn.configure(text="Hentikan Monitoring")
            self._log("Monitoring dimulai.")
            self.poll_thread = threading.Thread(target=self._poll_worker, daemon=True)
            self.poll_thread.start()

    def _poll_worker(self):
        supported = self.connection.supported_commands
        active_commands = [c for c in MONITOR_COMMANDS if c in supported]
        unsupported = [c.name for c in MONITOR_COMMANDS if c not in supported]
        if unsupported:
            self._log(f"Parameter tidak didukung ECU ini: {', '.join(unsupported)}")

        while self.polling:
            values = {}
            row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
            for cmd in MONITOR_COMMANDS:
                if cmd not in active_commands:
                    values[cmd.name] = "N/A"
                    row.append("N/A")
                    continue
                try:
                    response = self.connection.query(cmd)
                    val = "N/A" if response.is_null() else response.value
                except Exception as e:
                    val = f"Error: {e}"
                values[cmd.name] = val
                row.append(str(val))

            self.msg_queue.put(("values", values))
            self._compute_range_estimate(values)
            self._push_graph_data(values)
            self._push_diag_data(values)
            if self.log_writer:
                self.log_writer.writerow(row)
                self.log_file.flush()

            time.sleep(max(0.1, self.interval_var.get()))

    def _push_diag_data(self, values):
        """Simpan nilai numerik ke buffer Diagnostik Lanjutan (Turbo/Common Rail/EGR)."""
        for name in self.diag_buffers:
            val = values.get(name)
            numeric = None
            if val not in (None, "N/A") and not (isinstance(val, str) and val.startswith("Error")):
                try:
                    numeric = float(val.magnitude) if hasattr(val, "magnitude") else float(val)
                except (TypeError, ValueError):
                    numeric = None
            if numeric is not None:
                self.diag_buffers[name].append(numeric)

    def _push_graph_data(self, values):
        """Simpan nilai numerik ke buffer grafik historis (dipanggil tiap siklus polling)."""
        self.graph_time.append(time.time())
        for cmd in GRAPH_COMMANDS:
            val = values.get(cmd.name)
            numeric = None
            if val not in (None, "N/A") and not (isinstance(val, str) and val.startswith("Error")):
                try:
                    numeric = float(val.magnitude) if hasattr(val, "magnitude") else float(val)
                except (TypeError, ValueError):
                    numeric = None
            self.graph_data[cmd.name].append(numeric)
        self.msg_queue.put(("graph_update", None))

    def _compute_range_estimate(self, values, force_manual=False):
        """Hitung estimasi jarak tempuh tersisa dari level BBM (%),
        kapasitas tangki (liter), dan konsumsi BBM rata-rata (km/liter).

        Jika mode manual aktif (checkbox dicentang), level BBM diambil dari
        input pengguna karena banyak mobil diesel/GM tidak mengirim
        FUEL_LEVEL lewat OBD2 standar."""
        tank_capacity = self.tank_capacity_var.get()
        consumption = self.consumption_var.get()

        if self.manual_fuel_var.get():
            fuel_percent = self.manual_fuel_pct_var.get()
            source_note = "input manual"
        else:
            fuel_val = values.get(obd.commands.FUEL_LEVEL.name)
            is_error = isinstance(fuel_val, str) and fuel_val.startswith("Error")
            if fuel_val in (None, "N/A") or is_error:
                self.msg_queue.put((
                    "range_estimate",
                    (
                        "-- km",
                        "ECU tidak mengirim data FUEL_LEVEL (umum terjadi di mobil diesel/GM). "
                        "Centang 'Input manual' di atas untuk tetap dapat estimasi.",
                    ),
                ))
                return
            try:
                fuel_percent = float(fuel_val.magnitude) if hasattr(fuel_val, "magnitude") else float(fuel_val)
            except (TypeError, ValueError):
                self.msg_queue.put(("range_estimate", ("-- km", "Gagal membaca nilai level BBM dari ECU.")))
                return
            source_note = "sensor ECU"

        liters_remaining = (fuel_percent / 100.0) * tank_capacity
        estimated_km = liters_remaining * consumption

        detail = (
            f"BBM tersisa \u2248 {liters_remaining:.1f} liter dari level {fuel_percent:.0f}% ({source_note}), "
            f"tangki {tank_capacity:.0f} L, konsumsi {consumption:.1f} km/L."
        )
        self.msg_queue.put(("range_estimate", (f"\u2248 {estimated_km:.0f} km", detail)))

    # ---------------------------------------------------------
    # DTC
    # ---------------------------------------------------------
    def _read_dtc(self):
        self.read_dtc_btn.configure(state="disabled")
        threading.Thread(target=self._read_dtc_worker, daemon=True).start()

    def _read_dtc_worker(self):
        try:
            response = self.connection.query(obd.commands.GET_DTC)
        except Exception as e:
            self._log(f"Gagal membaca DTC: {e}")
            self.after(0, lambda: self.read_dtc_btn.configure(state="normal"))
            return

        self.after(0, lambda: self._show_dtc_result(response))

    def _show_dtc_result(self, response):
        for item in self.dtc_tree.get_children():
            self.dtc_tree.delete(item)

        if response.is_null() or not response.value:
            self._log("Tidak ada kode error tersimpan / tidak ada data.")
            self.last_dtc_codes = []
        else:
            for code, desc in response.value:
                self.dtc_tree.insert("", "end", values=(code, desc))
            self._log(f"Ditemukan {len(response.value)} kode error.")
            self.last_dtc_codes = [code for code, desc in response.value]

        self.read_dtc_btn.configure(state="normal")

    def _clear_dtc(self):
        if not messagebox.askyesno(
            "Konfirmasi", "Yakin ingin menghapus semua DTC / mematikan lampu Check Engine?"
        ):
            return
        self.clear_dtc_btn.configure(state="disabled")
        threading.Thread(target=self._clear_dtc_worker, daemon=True).start()

    def _clear_dtc_worker(self):
        try:
            self.connection.query(obd.commands.CLEAR_DTC)
            self._log("Perintah clear DTC dikirim.")
        except Exception as e:
            self._log(f"Gagal menghapus DTC: {e}")
        self.after(0, lambda: self.clear_dtc_btn.configure(state="normal"))
        self.after(0, lambda: [self.dtc_tree.delete(i) for i in self.dtc_tree.get_children()])

    # ---------------------------------------------------------
    # Info
    # ---------------------------------------------------------
    def _refresh_info(self):
        if not self.connection:
            return
        lines = []
        lines.append(f"Protokol       : {self.connection.protocol_name()}")
        lines.append(f"Port           : {self.connection.port_name()}")
        lines.append(f"Status         : {self.connection.status()}")
        lines.append("")
        lines.append("Command yang didukung ECU kendaraan ini:")
        for cmd in sorted(self.connection.supported_commands, key=lambda c: c.name):
            lines.append(f"  - {cmd.name}")

        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", "\n".join(lines))
        self.info_text.configure(state="disabled")

    # ---------------------------------------------------------
    # Cleanup
    # ---------------------------------------------------------
    def _on_close(self):
        self.polling = False
        time.sleep(0.1)
        if self.log_file:
            self.log_file.close()
        if self.connection:
            try:
                self.connection.close()
            except Exception:
                pass
        self.destroy()


def main():
    app = OBD2App()
    app.mainloop()


if __name__ == "__main__":
    main()
