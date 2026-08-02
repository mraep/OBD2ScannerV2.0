"""
Diagnostic Assistant (rule-based, tanpa AI) + Oil Reset Guide per merek.
Dipisah dari main.py supaya file UI tidak kepanjangan.
"""

# Rentang nilai NUMERIK acuan kasar (bukan spesifikasi presisi resmi pabrikan)
# dipakai untuk perbandingan otomatis oleh Diagnostic Assistant. Sengaja
# dibuat ringkas (cuma parameter yang punya rentang aman jelas & universal
# lintas merek) - RPM/Engine Load sengaja TIDAK dimasukkan karena rentang
# idle-nya bervariasi cukup jauh antar model dan gampang salah tandai
# "tidak normal" padahal sebenarnya wajar untuk mobil tertentu.
DIAG_NUMERIC_RANGES = {
    "COOLANT_TEMP": (75, 105, "°C"),
    "INTAKE_TEMP": (-20, 80, "°C"),
    "THROTTLE_POS": (0, 100, "%"),
    "VOLTAGE": (12.0, 15.0, "V"),
}


def run_diagnostic_assistant(values):
    """values: dict nama_pid -> angka mentah (bukan string berformat),
    biasanya diambil dari hasil Live Monitor terakhir. Return list string
    temuan (findings), satu baris per parameter."""
    findings = []
    for name, (low, high, unit) in DIAG_NUMERIC_RANGES.items():
        val = values.get(name)
        if val is None:
            continue
        if val < low:
            findings.append(f"⚠ {name}: {val}{unit} - DI BAWAH rentang normal ({low}-{high}{unit})")
        elif val > high:
            findings.append(f"⚠ {name}: {val}{unit} - DI ATAS rentang normal ({low}-{high}{unit})")
        else:
            findings.append(f"✓ {name}: {val}{unit} - normal")

    if not findings:
        findings.append(
            "Belum ada data untuk dianalisis - jalankan Live Monitor dulu "
            "(biarkan menyala beberapa detik), baru buka Diagnostic Assistant lagi."
        )
    return findings


# Prosedur reset lampu indikator servis/oli, dikelompokkan per merek/grup
# merek yang biasanya sama prosedurnya. Ini panduan UMUM - langkah persis
# bisa beda sedikit tergantung model & tahun, selalu cek buku manual kalau
# ragu.
OIL_RESET_GUIDE = {
    "Toyota / Lexus": (
        "1. Matikan mesin (kontak OFF).\n"
        "2. Tekan & tahan tombol trip meter (ODO/TRIP) di dashboard.\n"
        "3. Sambil tetap ditahan, putar kontak ke ON (jangan starter mesin).\n"
        "4. Tahan tombol sampai indikator 'Maint Reqd' berkedip lalu reset ke 0.\n"
        "5. Lepas tombol, matikan kontak."
    ),
    "Honda": (
        "1. Kontak ON (mesin mati).\n"
        "2. Tekan & tahan tombol SELECT/TRIP sampai tulisan berkedip.\n"
        "3. Lepas, lalu tekan & tahan lagi sampai indikator oli reset ke 100%.\n"
        "4. Matikan kontak."
    ),
    "Suzuki": (
        "1. Kontak OFF.\n"
        "2. Tekan & tahan tombol trip meter, putar kontak ke ON.\n"
        "3. Tahan sampai simbol kunci pas/oli berkedip beberapa detik lalu mati.\n"
        "4. Lepas tombol."
    ),
    "Daihatsu": (
        "1. Kontak OFF.\n"
        "2. Tekan & tahan tombol trip meter, putar kontak ke ON.\n"
        "3. Tahan ±10 detik sampai indikator servis berkedip cepat lalu mati.\n"
        "4. Lepas tombol, kontak OFF lagi."
    ),
    "Mitsubishi": (
        "1. Kontak ON.\n"
        "2. Tekan tombol trip/set beberapa kali sampai masuk mode servis di odometer.\n"
        "3. Tekan & tahan tombol sampai counter servis reset ke default.\n"
        "4. Matikan kontak."
    ),
    "Nissan": (
        "1. Kontak ON (mesin mati).\n"
        "2. Tekan pedal gas penuh 5x dalam 5 detik (beberapa model), ATAU\n"
        "   tekan & tahan tombol trip sampai indikator servis reset.\n"
        "3. Matikan kontak - cek indikator sudah hilang."
    ),
    "Mazda": (
        "1. Kontak ON (mesin mati), tunggu semua indikator dashboard menyala lalu mati.\n"
        "2. Tekan pedal gas penuh 5x dalam 5 detik.\n"
        "3. Matikan kontak, nyalakan mesin - cek indikator servis hilang."
    ),
    "Hyundai / Kia": (
        "1. Kontak ON.\n"
        "2. Tekan & tahan tombol trip/odo sampai menu servis muncul.\n"
        "3. Tekan tombol set/reset sampai counter kembali ke default.\n"
        "4. Matikan kontak."
    ),
    "Chevrolet / GM / Buick / GMC": (
        "1. Kontak ON (mesin mati), JANGAN starter.\n"
        "2. Injak pedal gas penuh 3x dalam 10 detik.\n"
        "3. Indikator 'Change Oil' akan berkedip lalu mati - matikan kontak."
    ),
    "Ford / Lincoln": (
        "1. Kontak ON (mesin mati).\n"
        "2. Tekan pedal gas penuh 3x dalam 10 detik.\n"
        "3. Cek indikator oli mati, matikan kontak."
    ),
    "Volkswagen / Audi / Seat / Škoda": (
        "1. Kontak OFF.\n"
        "2. Tekan & tahan tombol trip (0.0) di cluster, putar kontak ke ON.\n"
        "3. Tahan sampai muncul menu servis (SET/RESET) - tekan tombol reset.\n"
        "4. Matikan kontak."
    ),
    "BMW": (
        "Kebanyakan BMW modern butuh reset lewat menu iDrive (Service History) "
        "atau software khusus (INPA/ISTA) - tidak selalu bisa lewat tombol "
        "dashboard biasa."
    ),
    "Mercedes-Benz": (
        "Umumnya lewat menu ASSYST/Service pada instrument cluster - navigasi "
        "pakai tombol di setir/cluster ke menu Service, lalu pilih Reset."
    ),
    "Lainnya / Generik": (
        "Prosedur umum: kontak ON (mesin mati), tekan & tahan tombol trip meter, "
        "lalu putar kontak ke ON sambil tetap menahan tombol sampai indikator "
        "servis berkedip/reset. Kalau tidak berhasil, cek buku manual kendaraan "
        "karena prosedur bisa berbeda per model/tahun."
    ),
}

# Pemetaan nama merek (dari dropdown CAR_BRANDS di main.py) ke key di
# OIL_RESET_GUIDE di atas - beberapa merek sengaja digabung satu grup karena
# prosedurnya sama.
BRAND_TO_GUIDE_KEY = {
    "Toyota": "Toyota / Lexus", "Lexus": "Toyota / Lexus",
    "Honda": "Honda",
    "Suzuki": "Suzuki",
    "Daihatsu": "Daihatsu",
    "Mitsubishi": "Mitsubishi",
    "Nissan": "Nissan", "Datsun": "Nissan", "Infiniti": "Nissan",
    "Mazda": "Mazda",
    "Hyundai": "Hyundai / Kia", "Kia": "Hyundai / Kia", "Genesis": "Hyundai / Kia",
    "Chevrolet": "Chevrolet / GM / Buick / GMC", "GMC": "Chevrolet / GM / Buick / GMC",
    "Buick": "Chevrolet / GM / Buick / GMC", "Cadillac": "Chevrolet / GM / Buick / GMC",
    "Holden": "Chevrolet / GM / Buick / GMC",
    "Ford": "Ford / Lincoln", "Lincoln": "Ford / Lincoln",
    "Volkswagen": "Volkswagen / Audi / Seat / Škoda", "Audi": "Volkswagen / Audi / Seat / Škoda",
    "Seat": "Volkswagen / Audi / Seat / Škoda", "Škoda": "Volkswagen / Audi / Seat / Škoda",
    "BMW": "BMW", "Mini": "BMW",
    "Mercedes-Benz": "Mercedes-Benz", "Smart": "Mercedes-Benz",
}


def get_oil_guide_for_brand(brand):
    """Cari panduan reset oli yang paling cocok untuk merek terpilih di
    Profil Kendaraan. Kalau tidak ketemu, kembalikan panduan generik."""
    key = BRAND_TO_GUIDE_KEY.get(brand.strip(), "Lainnya / Generik")
    return OIL_RESET_GUIDE.get(key, OIL_RESET_GUIDE["Lainnya / Generik"])


def compute_oil_status(last_change_km, interval_km, current_km):
    """Hitung sisa km & status servis oli dari 3 angka yang diisi user.
    Return (sisa_km, status_text)."""
    next_due_km = last_change_km + interval_km
    remaining = next_due_km - current_km
    if remaining < 0:
        status = f"SUDAH LEWAT {abs(remaining):.0f} km dari jadwal - segera ganti oli."
    elif remaining <= interval_km * 0.1:
        status = f"Segera servis - sisa {remaining:.0f} km lagi."
    else:
        status = f"Aman - sisa {remaining:.0f} km lagi menuju servis berikutnya."
    return remaining, status
