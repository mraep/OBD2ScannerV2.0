"""
OBD2 Scanner - versi Android (Kivy)
====================================
Port dari obd2_scanner_gui.py (desktop/Tkinter) supaya bisa di-build jadi
APK Android lewat Buildozer + GitHub Actions. Lihat README_ANDROID.md untuk
penjelasan lengkap scope & cara build.

FITUR:
    Koneksi Bluetooth/USB/WiFi + scan & tambah perangkat Bluetooth,
    Profil Kendaraan (merek dropdown dunia + model + tahun + jenis bahan
    bakar), Live Monitor PID inti + tegangan aki, estimasi jarak tempuh dari
    fuel % & liter (saling sinkron), Freeze Frame, Readiness Monitor, O2
    Sensor, baca & hapus DTC, Diagnostic Assistant (rule-based, offline),
    AI Diagnostic (opsional, butuh API key + koneksi internet sendiri),
    Oil Reset tracker per-merek.

    BELUM ikut: grafik real-time (matplotlib berat/kurang stabil untuk APK
    Android) - kalau memang perlu, bisa disusulkan pakai kivy_garden.graph.
"""
import json
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.checkbox import CheckBox
from kivy.metrics import dp
from kivy.network.urlrequest import UrlRequest

from obd_transport import WifiTransport, BluetoothTransport, UsbTransport, TransportError
from elm327 import ELM327Client, PIDS, MONITOR_ORDER, O2_SENSOR_PIDS
from diagnostics import run_diagnostic_assistant, get_oil_guide_for_brand, compute_oil_status

try:
    import bt_scan
    ANDROID = True
except Exception:
    bt_scan = None
    ANDROID = False


# Daftar merek mobil umum di dunia (sama seperti dropdown di versi desktop).
# Combobox Kivy (Spinner) tidak mendukung ketik-manual, jadi ditambahkan opsi
# "Lainnya (ketik di kolom Model)" di paling atas sebagai jalan keluar.
CAR_BRANDS = [
    "-- Pilih Merek --", "Acura", "Alfa Romeo", "Aston Martin", "Audi", "Baojun",
    "Bentley", "BMW", "Buick", "BYD", "Cadillac", "Changan", "Chery", "Chevrolet",
    "Chrysler", "Citroën", "Dacia", "Daewoo", "Daihatsu", "Datsun", "DFSK", "Dodge",
    "DS Automobiles", "Ferrari", "Fiat", "Ford", "Foton", "Genesis", "Geely", "GMC",
    "Great Wall / Haval", "Holden", "Honda", "Hummer", "Hyundai", "Infiniti", "Isuzu",
    "JAC", "Jaguar", "Jeep", "Kia", "Lada", "Lamborghini", "Lancia", "Land Rover",
    "Lexus", "Lincoln", "Lotus", "Maserati", "Maxus / LDV", "Mazda", "McLaren",
    "Mercedes-Benz", "MG", "Mini", "Mitsubishi", "Nio", "Nissan", "Opel", "Perodua",
    "Peugeot", "Polestar", "Porsche", "Proton", "RAM", "Renault", "Rivian",
    "Rolls-Royce", "Saab", "Scion", "Seat", "Škoda", "Smart", "SsangYong", "Subaru",
    "Suzuki", "Tata", "Tesla", "Toyota", "Vauxhall", "Volkswagen", "Volvo", "Wuling",
    "Xpeng", "Zotye", "Lainnya (ketik di kolom Model)",
]

SHARED_NORMAL_RANGES = {
    "SPEED": "0-180 km/h",
    "COOLANT_TEMP": "80-95°C saat panas normal",
    "THROTTLE_POS": "0-20% saat idle",
    "INTAKE_TEMP": "mendekati suhu udara luar",
    "FUEL_LEVEL": "0-100%",
}
GASOLINE_NORMAL_RANGES = {
    "RPM": "idle ≈ 700-900 rpm",
    "ENGINE_LOAD": "10-30% saat idle",
}
DIESEL_NORMAL_RANGES = {
    "RPM": "idle ≈ 700-900 rpm (umumnya sedikit lebih rendah)",
    "ENGINE_LOAD": "15-40% saat idle",
}


def get_normal_ranges(fuel_type):
    ranges = dict(SHARED_NORMAL_RANGES)
    ranges.update(DIESEL_NORMAL_RANGES if fuel_type == "Diesel" else GASOLINE_NORMAL_RANGES)
    return ranges


def show_popup(title, message):
    Popup(
        title=title,
        content=Label(text=message, halign="left"),
        size_hint=(0.85, 0.5),
    ).open()


class LabeledRow(BoxLayout):
    """Baris 'Label: Widget' generik dipakai berulang kali di seluruh app."""

    def __init__(self, label_text, widget, label_width=140, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(44), spacing=dp(6), **kwargs)
        self.add_widget(Label(text=label_text, size_hint_x=None, width=dp(label_width)))
        self.add_widget(widget)


class OBD2App(App):
    def build(self):
        self.client = None
        self.transport = None
        self.connected = False
        self.monitoring = False
        self.monitor_event = None
        self.value_labels = {}
        self.ref_labels = {}
        self.last_monitor_values = {}  # nama_pid -> angka mentah (bukan string), dipakai Diagnostic Assistant
        self._fuel_sync_guard = False
        self._ai_context_auto_value = None

        if ANDROID:
            bt_scan.request_runtime_permissions()

        root = TabbedPanel(do_default_tab=False, tab_width=dp(110))
        root.add_widget(self._build_connection_tab())
        root.add_widget(self._build_profile_tab())
        root.add_widget(self._build_monitor_tab())
        root.add_widget(self._build_freeze_frame_tab())
        root.add_widget(self._build_readiness_tab())
        root.add_widget(self._build_o2_tab())
        root.add_widget(self._build_dtc_tab())
        root.add_widget(self._build_diagnostic_assistant_tab())
        root.add_widget(self._build_ai_diagnostic_tab())
        root.add_widget(self._build_oil_tab())
        return root

    # ------------------------------------------------------------------
    # TAB: Koneksi
    # ------------------------------------------------------------------
    def _build_connection_tab(self):
        tab = TabbedPanelItem(text="Koneksi")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        self.conn_type_spinner = Spinner(
            text="Bluetooth", values=("Bluetooth", "USB", "WiFi"), size_hint_y=None, height=dp(44)
        )
        self.conn_type_spinner.bind(text=self._on_conn_type_changed)
        layout.add_widget(LabeledRow("Tipe Koneksi:", self.conn_type_spinner))

        # ---- Bluetooth/USB: pilih dari device yang sudah terpasang/paired ----
        self.device_spinner = Spinner(text="(belum scan)", values=(), size_hint_y=None, height=dp(44))
        self.device_row = LabeledRow("Device:", self.device_spinner)
        layout.add_widget(self.device_row)

        # ---- WiFi: IP + Port (baris ini selalu ada di layout, tinggal
        # disembunyikan/ditampilkan lewat height+opacity supaya tidak perlu
        # reparenting widget yang rawan bug di Kivy) ----
        self.wifi_ip_input = TextInput(text="192.168.0.10", multiline=False, size_hint_y=None, height=dp(44))
        self.wifi_port_input = TextInput(text="35000", multiline=False, size_hint_y=None, height=dp(44))
        self.wifi_row_ip = LabeledRow("IP Address:", self.wifi_ip_input)
        self.wifi_row_port = LabeledRow("Port:", self.wifi_port_input)
        layout.add_widget(self.wifi_row_ip)
        layout.add_widget(self.wifi_row_port)

        btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        self.scan_btn = Button(text="Scan Device")
        self.scan_btn.bind(on_release=lambda *_: self._scan_devices())
        btn_row.add_widget(self.scan_btn)

        self.add_bt_btn = Button(text="Tambah Perangkat Bluetooth")
        self.add_bt_btn.bind(on_release=lambda *_: self._open_add_bluetooth_popup())
        btn_row.add_widget(self.add_bt_btn)
        layout.add_widget(btn_row)

        self.connect_btn = Button(text="Sambungkan", size_hint_y=None, height=dp(48))
        self.connect_btn.bind(on_release=lambda *_: self._connect())
        layout.add_widget(self.connect_btn)

        self.status_label = Label(text="Status: belum terhubung", size_hint_y=None, height=dp(60))
        layout.add_widget(self.status_label)

        layout.add_widget(BoxLayout())  # spacer
        tab.add_widget(layout)

        self._on_conn_type_changed(self.conn_type_spinner, "Bluetooth")
        return tab

    @staticmethod
    def _set_row_visible(row, visible):
        """Sembunyikan/tampilkan sebuah LabeledRow tanpa reparenting -
        lebih aman di Kivy daripada add_widget/remove_widget berulang."""
        row.opacity = 1 if visible else 0
        row.disabled = not visible
        row.height = dp(44) if visible else 0

    def _on_conn_type_changed(self, spinner, value):
        is_wifi = value == "WiFi"
        self._set_row_visible(self.device_row, not is_wifi)
        self._set_row_visible(self.wifi_row_ip, is_wifi)
        self._set_row_visible(self.wifi_row_port, is_wifi)
        self.add_bt_btn.opacity = 1 if value == "Bluetooth" else 0
        self.add_bt_btn.disabled = value != "Bluetooth"

    def _scan_devices(self):
        conn_type = self.conn_type_spinner.text
        if conn_type == "WiFi":
            self.status_label.text = (
                "Status: scan jaringan WiFi belum tersedia di versi Android ini - "
                "isi IP:Port manual (default umum: 192.168.0.10:35000)."
            )
            return
        self.status_label.text = "Status: memindai perangkat..."
        threading.Thread(target=self._scan_devices_worker, args=(conn_type,), daemon=True).start()

    def _scan_devices_worker(self, conn_type):
        values = []
        error = None
        try:
            if conn_type == "Bluetooth":
                if not ANDROID:
                    error = "Scan Bluetooth cuma jalan di perangkat Android."
                else:
                    values = [f"{mac}|{name}" for mac, name in bt_scan.get_bonded_devices()]
            elif conn_type == "USB":
                if not ANDROID:
                    error = "Scan USB cuma jalan di perangkat Android."
                else:
                    from usb4a import usb
                    values = [f"{d.getDeviceName()}|{d.getDeviceName()}" for d in usb.get_usb_device_list()]
        except Exception as e:
            error = str(e)
        Clock.schedule_once(lambda dt: self._scan_devices_done(values, error))

    def _scan_devices_done(self, values, error):
        if error:
            self.status_label.text = f"Status: scan gagal - {error}"
            return
        if not values:
            self.status_label.text = "Status: tidak ada device ditemukan. Pasangkan dulu lewat 'Tambah Perangkat Bluetooth'."
            return
        display = [v.split("|", 1)[1] for v in values]
        self._device_scan_map = dict(zip(display, [v.split("|", 1)[0] for v in values]))
        self.device_spinner.values = display
        self.device_spinner.text = display[0]
        self.status_label.text = f"Status: ditemukan {len(values)} device. Pilih dari dropdown Device."

    def _connect(self):
        conn_type = self.conn_type_spinner.text
        self.status_label.text = "Status: menyambungkan..."
        threading.Thread(target=self._connect_worker, args=(conn_type,), daemon=True).start()

    def _connect_worker(self, conn_type):
        try:
            if conn_type == "WiFi":
                transport = WifiTransport(self.wifi_ip_input.text.strip(), self.wifi_port_input.text.strip())
            elif conn_type == "Bluetooth":
                mac = getattr(self, "_device_scan_map", {}).get(self.device_spinner.text, self.device_spinner.text)
                transport = BluetoothTransport(mac)
            else:  # USB
                device_name = getattr(self, "_device_scan_map", {}).get(self.device_spinner.text)
                transport = UsbTransport(device_name)

            client = ELM327Client(transport)
            client.connect()
            self.client = client
            self.transport = transport
            self.connected = True
            Clock.schedule_once(lambda dt: self._connect_done(True, conn_type))
        except TransportError as e:
            Clock.schedule_once(lambda dt: self._connect_done(False, str(e)))
        except Exception as e:
            Clock.schedule_once(lambda dt: self._connect_done(False, f"Error tak terduga: {e}"))

    def _connect_done(self, ok, info):
        if ok:
            self.status_label.text = f"Status: terhubung ({info})"
        else:
            self.connected = False
            self.status_label.text = f"Status: gagal konek - {info}"

    # ------------------------------------------------------------------
    # TAB: Tambah Perangkat Bluetooth (popup)
    # ------------------------------------------------------------------
    def _open_add_bluetooth_popup(self):
        if not ANDROID:
            show_popup(
                "Tambah Perangkat Bluetooth",
                "Fitur pairing dari dalam app ini cuma jalan di Android.\n"
                "Di desktop, pasangkan adapter lewat pengaturan Bluetooth OS.",
            )
            return

        content = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        info = Label(text="Cari perangkat Bluetooth baru di sekitar (±12 detik)...", size_hint_y=None, height=dp(50))
        content.add_widget(info)

        scroll = ScrollView(size_hint=(1, 1))
        device_list_layout = GridLayout(cols=1, size_hint_y=None, spacing=dp(4))
        device_list_layout.bind(minimum_height=device_list_layout.setter("height"))
        scroll.add_widget(device_list_layout)
        content.add_widget(scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        scan_btn = Button(text="Cari Perangkat")
        close_btn = Button(text="Tutup")
        btn_row.add_widget(scan_btn)
        btn_row.add_widget(close_btn)
        content.add_widget(btn_row)

        popup = Popup(title="Tambah Perangkat Bluetooth", content=content, size_hint=(0.9, 0.8))
        close_btn.bind(on_release=lambda *_: (bt_scan.stop_discovery(), popup.dismiss()))

        found = {}

        def add_device_button(mac, name):
            if mac in found:
                return
            found[mac] = name
            btn = Button(text=f"{name}\n{mac}", size_hint_y=None, height=dp(56))
            btn.bind(on_release=lambda *_: self._pair_and_use(mac, name, popup))
            device_list_layout.add_widget(btn)

        def on_found(mac, name):
            Clock.schedule_once(lambda dt: add_device_button(mac, name))

        def on_finished():
            Clock.schedule_once(lambda dt: info.__setattr__("text", f"Scan selesai. Ditemukan {len(found)} perangkat."))

        def start_scan(*_):
            found.clear()
            device_list_layout.clear_widgets()
            info.text = "Memindai... (±12 detik)"
            bt_scan.start_discovery(on_found, on_finished)

        scan_btn.bind(on_release=start_scan)
        popup.open()
        start_scan()

    def _pair_and_use(self, mac, name, popup):
        def worker():
            try:
                ok = bt_scan.pair_device(mac)
            except Exception as e:
                ok = False
                Clock.schedule_once(lambda dt: show_popup("Pairing Gagal", str(e)))
                return
            Clock.schedule_once(lambda dt: self._pair_done(ok, mac, name, popup))

        threading.Thread(target=worker, daemon=True).start()

    def _pair_done(self, ok, mac, name, popup):
        if ok:
            show_popup("Pairing Dimulai", f"Konfirmasi pairing '{name}' lewat dialog sistem Android kalau muncul.")
            self.conn_type_spinner.text = "Bluetooth"
            self.device_spinner.values = list(self.device_spinner.values) + [name]
            self.device_spinner.text = name
            self._device_scan_map = getattr(self, "_device_scan_map", {})
            self._device_scan_map[name] = mac
        else:
            show_popup("Pairing Gagal", f"Tidak bisa memulai pairing ke {name} ({mac}).")
        popup.dismiss()

    # ------------------------------------------------------------------
    # TAB: Profil Kendaraan
    # ------------------------------------------------------------------
    def _build_profile_tab(self):
        tab = TabbedPanelItem(text="Profil Kendaraan")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        self.brand_spinner = Spinner(text=CAR_BRANDS[0], values=CAR_BRANDS, size_hint_y=None, height=dp(44))
        self.brand_spinner.bind(text=lambda *_: self._on_profile_changed())
        layout.add_widget(LabeledRow("Merek:", self.brand_spinner))

        self.model_input = TextInput(multiline=False, size_hint_y=None, height=dp(44))
        self.model_input.bind(text=lambda *_: self._on_profile_changed())
        layout.add_widget(LabeledRow("Model:", self.model_input))

        self.year_input = TextInput(multiline=False, input_filter="int", size_hint_y=None, height=dp(44))
        self.year_input.bind(text=lambda *_: self._on_profile_changed())
        layout.add_widget(LabeledRow("Tahun:", self.year_input))

        self.fuel_type_spinner = Spinner(
            text="Bensin", values=("Bensin", "Diesel"), size_hint_y=None, height=dp(44)
        )
        self.fuel_type_spinner.bind(text=lambda *_: self._on_profile_changed())
        layout.add_widget(LabeledRow("Jenis Bahan Bakar:", self.fuel_type_spinner))

        layout.add_widget(Label(
            text="(Merek tidak ada di daftar? Pilih 'Lainnya' lalu tulis nama lengkapnya di kolom Model.)",
            size_hint_y=None, height=dp(40), color=(0.6, 0.6, 0.6, 1),
        ))
        layout.add_widget(BoxLayout())
        tab.add_widget(layout)
        return tab

    # ------------------------------------------------------------------
    # TAB: Live Monitor
    # ------------------------------------------------------------------
    def _build_monitor_tab(self):
        tab = TabbedPanelItem(text="Live Monitor")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        self.monitor_btn = Button(text="Mulai Monitoring", size_hint_y=None, height=dp(48))
        self.monitor_btn.bind(on_release=lambda *_: self._toggle_monitor())
        layout.add_widget(self.monitor_btn)

        scroll = ScrollView(size_hint=(1, 1))
        grid = GridLayout(cols=2, size_hint_y=None, spacing=dp(8), padding=dp(4))
        grid.bind(minimum_height=grid.setter("height"))

        for name in MONITOR_ORDER + ["VOLTAGE"]:
            card = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(90))
            title = Label(text=name, size_hint_y=None, height=dp(24), font_size="13sp")
            value = Label(text="--", size_hint_y=None, height=dp(40), font_size="22sp", bold=True)
            ref = Label(text="", size_hint_y=None, height=dp(24), font_size="11sp", color=(0.6, 0.6, 0.6, 1))
            card.add_widget(title)
            card.add_widget(value)
            card.add_widget(ref)
            grid.add_widget(card)
            self.value_labels[name] = value
            self.ref_labels[name] = ref

        scroll.add_widget(grid)
        layout.add_widget(scroll)

        # ---- Estimasi BBM: tangki, konsumsi, input manual % <-> liter ----
        fuel_box = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(190), spacing=dp(4), padding=dp(4))
        self.tank_capacity_input = TextInput(text="50", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        self.tank_capacity_input.bind(text=lambda *_: self._on_tank_capacity_change())
        fuel_box.add_widget(LabeledRow("Kapasitas Tangki (L):", self.tank_capacity_input, label_width=180))

        self.consumption_input = TextInput(text="10", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        fuel_box.add_widget(LabeledRow("Konsumsi (km/L):", self.consumption_input, label_width=180))

        self.manual_fuel_pct_input = TextInput(text="50", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        self.manual_fuel_pct_input.bind(text=lambda *_: self._on_manual_pct_change())
        fuel_box.add_widget(LabeledRow("BBM Manual (%):", self.manual_fuel_pct_input, label_width=180))

        self.manual_fuel_liter_input = TextInput(text="25", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        self.manual_fuel_liter_input.bind(text=lambda *_: self._on_manual_liter_change())
        fuel_box.add_widget(LabeledRow("BBM Manual (liter):", self.manual_fuel_liter_input, label_width=180))

        self.range_estimate_label = Label(text="Estimasi jarak: -", size_hint_y=None, height=dp(30))
        fuel_box.add_widget(self.range_estimate_label)

        layout.add_widget(fuel_box)
        tab.add_widget(layout)

        self._refresh_reference_labels()
        return tab

    def _refresh_reference_labels(self):
        if not hasattr(self, "ref_labels"):
            return
        ranges = get_normal_ranges(self.fuel_type_spinner.text)
        for name, label in self.ref_labels.items():
            text = ranges.get(name, "")
            label.text = f"Acuan: {text}" if text else ""

    def _on_profile_changed(self):
        """Dipanggil tiap kali Merek/Model/Tahun/Jenis Bahan Bakar di Profil
        Kendaraan berubah - refresh semua tab lain yang bergantung padanya."""
        self._refresh_reference_labels()
        if hasattr(self, "ai_context_input"):
            self._sync_ai_vehicle_context()
        if hasattr(self, "oil_guide_label"):
            self._refresh_oil_guide()

    def _pct_to_liters(self, pct):
        try:
            capacity = float(self.tank_capacity_input.text or 0)
        except ValueError:
            capacity = 0
        return round((pct / 100.0) * capacity, 1)

    def _liters_to_pct(self, liters):
        try:
            capacity = float(self.tank_capacity_input.text or 0)
        except ValueError:
            capacity = 0
        if capacity <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (liters / capacity) * 100.0)), 1)

    def _on_manual_pct_change(self):
        if self._fuel_sync_guard:
            return
        self._fuel_sync_guard = True
        try:
            pct = float(self.manual_fuel_pct_input.text or 0)
            self.manual_fuel_liter_input.text = str(self._pct_to_liters(pct))
        except ValueError:
            pass
        finally:
            self._fuel_sync_guard = False
        self._update_range_estimate()

    def _on_manual_liter_change(self):
        if self._fuel_sync_guard:
            return
        self._fuel_sync_guard = True
        try:
            liters = float(self.manual_fuel_liter_input.text or 0)
            self.manual_fuel_pct_input.text = str(self._liters_to_pct(liters))
        except ValueError:
            pass
        finally:
            self._fuel_sync_guard = False
        self._update_range_estimate()

    def _on_tank_capacity_change(self):
        if self._fuel_sync_guard:
            return
        self._on_manual_pct_change()

    def _update_range_estimate(self):
        try:
            pct = float(self.manual_fuel_pct_input.text or 0)
            liters = float(self.manual_fuel_liter_input.text or 0)
            consumption = float(self.consumption_input.text or 0)
        except ValueError:
            return
        km = round(liters * consumption, 1)
        self.range_estimate_label.text = f"BBM tersisa ≈ {liters} liter dari {pct}% - estimasi jarak ≈ {km} km"

    # ------------------------------------------------------------------
    # Polling Live Monitor
    # ------------------------------------------------------------------
    def _toggle_monitor(self):
        if not self.connected:
            show_popup("Belum Terhubung", "Sambungkan ke adapter dulu di tab Koneksi.")
            return
        if self.monitoring:
            self.monitoring = False
            self.monitor_btn.text = "Mulai Monitoring"
        else:
            self.monitoring = True
            self.monitor_btn.text = "Berhenti Monitoring"
            threading.Thread(target=self._monitor_loop, daemon=True).start()

    def _monitor_loop(self):
        while self.monitoring and self.connected:
            values = {}
            raw_values = {}
            for name in MONITOR_ORDER:
                try:
                    result = self.client.query(name)
                except Exception:
                    result = None
                if result:
                    value, unit = result
                    values[name] = f"{value:.1f} {unit}" if isinstance(value, float) else f"{value} {unit}"
                    raw_values[name] = value
                else:
                    values[name] = "N/A"
            try:
                voltage = self.client.read_voltage()
                values["VOLTAGE"] = f"{voltage:.1f} V" if voltage is not None else "N/A"
                if voltage is not None:
                    raw_values["VOLTAGE"] = voltage
            except Exception:
                values["VOLTAGE"] = "N/A"

            self.last_monitor_values = raw_values
            Clock.schedule_once(lambda dt, v=values: self._apply_monitor_values(v))
            time.sleep(1.0)

    def _apply_monitor_values(self, values):
        for name, text in values.items():
            if name in self.value_labels:
                self.value_labels[name].text = text

    # ------------------------------------------------------------------
    # TAB: Freeze Frame (mode 02 - snapshot data saat DTC terakhir muncul)
    # ------------------------------------------------------------------
    def _build_freeze_frame_tab(self):
        tab = TabbedPanelItem(text="Freeze Frame")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        info = Label(
            text="Data snapshot yang DIBEKUKAN persis saat DTC terakhir muncul (bukan data live).",
            size_hint_y=None, height=dp(40), color=(0.6, 0.6, 0.6, 1),
        )
        layout.add_widget(info)

        read_btn = Button(text="Baca Freeze Frame", size_hint_y=None, height=dp(48))
        read_btn.bind(on_release=lambda *_: self._read_freeze_frame())
        layout.add_widget(read_btn)

        scroll = ScrollView(size_hint=(1, 1))
        grid = GridLayout(cols=2, size_hint_y=None, spacing=dp(8), padding=dp(4))
        grid.bind(minimum_height=grid.setter("height"))

        self.freeze_frame_labels = {}
        for name in MONITOR_ORDER:
            card = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(70))
            card.add_widget(Label(text=name, size_hint_y=None, height=dp(22), font_size="13sp"))
            value = Label(text="--", size_hint_y=None, height=dp(36), font_size="18sp", bold=True)
            card.add_widget(value)
            grid.add_widget(card)
            self.freeze_frame_labels[name] = value

        scroll.add_widget(grid)
        layout.add_widget(scroll)
        tab.add_widget(layout)
        return tab

    def _read_freeze_frame(self):
        if not self.connected:
            show_popup("Belum Terhubung", "Sambungkan ke adapter dulu di tab Koneksi.")
            return
        for label in self.freeze_frame_labels.values():
            label.text = "..."
        threading.Thread(target=self._read_freeze_frame_worker, daemon=True).start()

    def _read_freeze_frame_worker(self):
        values = {}
        for name in MONITOR_ORDER:
            try:
                result = self.client.query_freeze_frame(name)
            except Exception:
                result = None
            if result:
                value, unit = result
                values[name] = f"{value:.1f} {unit}" if isinstance(value, float) else f"{value} {unit}"
            else:
                values[name] = "N/A (tidak ada freeze frame / PID tidak didukung)"
        Clock.schedule_once(lambda dt: self._apply_freeze_frame_values(values))

    def _apply_freeze_frame_values(self, values):
        for name, text in values.items():
            if name in self.freeze_frame_labels:
                self.freeze_frame_labels[name].text = text

    # ------------------------------------------------------------------
    # TAB: Readiness Monitor (PID 0101 - status monitor emisi siap/belum)
    # ------------------------------------------------------------------
    def _build_readiness_tab(self):
        tab = TabbedPanelItem(text="Readiness")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        info = Label(
            text="Status kesiapan monitor emisi sejak DTC terakhir dihapus/reset.",
            size_hint_y=None, height=dp(40), color=(0.6, 0.6, 0.6, 1),
        )
        layout.add_widget(info)

        read_btn = Button(text="Baca Readiness", size_hint_y=None, height=dp(48))
        read_btn.bind(on_release=lambda *_: self._read_readiness())
        layout.add_widget(read_btn)

        scroll = ScrollView(size_hint=(1, 1))
        self.readiness_label = Label(
            text="Belum ada data. Klik 'Baca Readiness'.",
            size_hint_y=None, valign="top", halign="left",
        )
        self.readiness_label.bind(texture_size=self.readiness_label.setter("size"))
        scroll.add_widget(self.readiness_label)
        layout.add_widget(scroll)
        tab.add_widget(layout)
        return tab

    def _read_readiness(self):
        if not self.connected:
            show_popup("Belum Terhubung", "Sambungkan ke adapter dulu di tab Koneksi.")
            return
        self.readiness_label.text = "Membaca readiness..."
        threading.Thread(target=self._read_readiness_worker, daemon=True).start()

    def _read_readiness_worker(self):
        try:
            result = self.client.read_readiness(self.fuel_type_spinner.text)
        except Exception as e:
            Clock.schedule_once(lambda dt: setattr(self.readiness_label, "text", f"Gagal baca readiness: {e}"))
            return
        if result is None:
            text = "Tidak ada respons/PID 0101 tidak didukung adapter ini."
        else:
            text = "\n".join(f"{k}: {v}" for k, v in result.items())
        Clock.schedule_once(lambda dt: setattr(self.readiness_label, "text", text))

    # ------------------------------------------------------------------
    # TAB: O2 Sensor (PID 0114-011B - tegangan & fuel trim tiap sensor)
    # ------------------------------------------------------------------
    def _build_o2_tab(self):
        tab = TabbedPanelItem(text="O2 Sensor")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        read_btn = Button(text="Baca O2 Sensor", size_hint_y=None, height=dp(48))
        read_btn.bind(on_release=lambda *_: self._read_o2())
        layout.add_widget(read_btn)

        scroll = ScrollView(size_hint=(1, 1))
        grid = GridLayout(cols=2, size_hint_y=None, spacing=dp(8), padding=dp(4))
        grid.bind(minimum_height=grid.setter("height"))

        self.o2_labels = {}
        for name in O2_SENSOR_PIDS:
            card = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(70))
            card.add_widget(Label(text=name, size_hint_y=None, height=dp(22), font_size="13sp"))
            value = Label(text="--", size_hint_y=None, height=dp(36), font_size="16sp", bold=True)
            card.add_widget(value)
            grid.add_widget(card)
            self.o2_labels[name] = value

        scroll.add_widget(grid)
        layout.add_widget(scroll)
        tab.add_widget(layout)
        return tab

    def _read_o2(self):
        if not self.connected:
            show_popup("Belum Terhubung", "Sambungkan ke adapter dulu di tab Koneksi.")
            return
        for label in self.o2_labels.values():
            label.text = "..."
        threading.Thread(target=self._read_o2_worker, daemon=True).start()

    def _read_o2_worker(self):
        values = {}
        for name in O2_SENSOR_PIDS:
            try:
                result = self.client.query_o2(name)
            except Exception:
                result = None
            if result:
                voltage, trim = result
                trim_text = f"{trim:+.1f}%" if trim is not None else "N/A"
                values[name] = f"{voltage:.3f} V\ntrim {trim_text}"
            else:
                values[name] = "N/A"
        Clock.schedule_once(lambda dt: self._apply_o2_values(values))

    def _apply_o2_values(self, values):
        for name, text in values.items():
            if name in self.o2_labels:
                self.o2_labels[name].text = text

    # ------------------------------------------------------------------
    # TAB: DTC
    # ------------------------------------------------------------------
    def _build_dtc_tab(self):
        tab = TabbedPanelItem(text="DTC")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(6))
        read_btn = Button(text="Baca DTC")
        read_btn.bind(on_release=lambda *_: self._read_dtc())
        clear_btn = Button(text="Hapus DTC")
        clear_btn.bind(on_release=lambda *_: self._clear_dtc())
        btn_row.add_widget(read_btn)
        btn_row.add_widget(clear_btn)
        layout.add_widget(btn_row)

        scroll = ScrollView(size_hint=(1, 1))
        self.dtc_result_label = Label(
            text="Belum ada data. Klik 'Baca DTC'.",
            size_hint_y=None, valign="top", halign="left",
        )
        self.dtc_result_label.bind(texture_size=self.dtc_result_label.setter("size"))
        scroll.add_widget(self.dtc_result_label)
        layout.add_widget(scroll)

        tab.add_widget(layout)
        return tab

    def _read_dtc(self):
        if not self.connected:
            show_popup("Belum Terhubung", "Sambungkan ke adapter dulu di tab Koneksi.")
            return
        self.dtc_result_label.text = "Membaca DTC..."
        threading.Thread(target=self._read_dtc_worker, daemon=True).start()

    def _read_dtc_worker(self):
        try:
            codes = self.client.read_dtc()
        except Exception as e:
            Clock.schedule_once(lambda dt: setattr(self.dtc_result_label, "text", f"Gagal baca DTC: {e}"))
            return
        text = "Tidak ada DTC tersimpan (mesin bersih)." if not codes else "\n".join(codes)
        Clock.schedule_once(lambda dt: setattr(self.dtc_result_label, "text", text))

    def _clear_dtc(self):
        if not self.connected:
            show_popup("Belum Terhubung", "Sambungkan ke adapter dulu di tab Koneksi.")
            return
        threading.Thread(target=self._clear_dtc_worker, daemon=True).start()

    def _clear_dtc_worker(self):
        try:
            ok = self.client.clear_dtc()
        except Exception as e:
            ok = False
            Clock.schedule_once(lambda dt: show_popup("Error", str(e)))
            return
        msg = "DTC berhasil dihapus & lampu Check Engine di-reset." if ok else "Gagal menghapus DTC."
        Clock.schedule_once(lambda dt: show_popup("Hapus DTC", msg))


    # ------------------------------------------------------------------
    # TAB: Diagnostic Assistant (rule-based, OFFLINE - tanpa AI/internet)
    # ------------------------------------------------------------------
    def _build_diagnostic_assistant_tab(self):
        tab = TabbedPanelItem(text="Diagnostic")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        info = Label(
            text="Analisis otomatis OFFLINE (bandingkan data Live Monitor terakhir dengan rentang normal). "
                 "Jalankan Live Monitor dulu beberapa detik sebelum pakai ini.",
            size_hint_y=None, height=dp(60), color=(0.6, 0.6, 0.6, 1),
        )
        layout.add_widget(info)

        run_btn = Button(text="Jalankan Diagnostic Assistant", size_hint_y=None, height=dp(48))
        run_btn.bind(on_release=lambda *_: self._run_diagnostic_assistant())
        layout.add_widget(run_btn)

        scroll = ScrollView(size_hint=(1, 1))
        self.diagnostic_result_label = Label(
            text="Belum dijalankan.",
            size_hint_y=None, valign="top", halign="left",
        )
        self.diagnostic_result_label.bind(texture_size=self.diagnostic_result_label.setter("size"))
        scroll.add_widget(self.diagnostic_result_label)
        layout.add_widget(scroll)
        tab.add_widget(layout)
        return tab

    def _run_diagnostic_assistant(self):
        findings = run_diagnostic_assistant(self.last_monitor_values)
        self.diagnostic_result_label.text = "\n".join(findings)

    # ------------------------------------------------------------------
    # TAB: AI Diagnostic (OPSIONAL - butuh API key & koneksi internet sendiri)
    # ------------------------------------------------------------------
    def _build_ai_diagnostic_tab(self):
        tab = TabbedPanelItem(text="AI Diagnostic")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        warn = Label(
            text="Fitur ini OPSIONAL - butuh API key & endpoint AI milik kamu sendiri "
                 "(kompatibel format OpenAI Chat Completions, mis. OpenAI/OpenRouter/dll), "
                 "dan mengirim data kendaraan + hasil scan ke internet. API key disimpan "
                 "di memori HP saja (tidak dikirim ke mana-mana selain endpoint yang kamu isi).",
            size_hint_y=None, height=dp(90), color=(0.6, 0.6, 0.6, 1),
        )
        layout.add_widget(warn)

        self.ai_endpoint_input = TextInput(
            text="https://api.openai.com/v1/chat/completions", multiline=False,
            size_hint_y=None, height=dp(44),
        )
        layout.add_widget(LabeledRow("API Endpoint:", self.ai_endpoint_input, label_width=110))

        self.ai_model_input = TextInput(text="gpt-4o-mini", multiline=False, size_hint_y=None, height=dp(44))
        layout.add_widget(LabeledRow("Model:", self.ai_model_input, label_width=110))

        self.ai_key_input = TextInput(password=True, multiline=False, size_hint_y=None, height=dp(44))
        layout.add_widget(LabeledRow("API Key:", self.ai_key_input, label_width=110))

        self.ai_context_input = TextInput(multiline=False, size_hint_y=None, height=dp(44))
        layout.add_widget(LabeledRow("Konteks Kendaraan:", self.ai_context_input, label_width=110))
        self._sync_ai_vehicle_context()

        send_btn = Button(text="Kirim ke AI", size_hint_y=None, height=dp(48))
        send_btn.bind(on_release=lambda *_: self._send_ai_diagnostic())
        layout.add_widget(send_btn)

        scroll = ScrollView(size_hint=(1, 1))
        self.ai_result_label = Label(
            text="Belum ada hasil.",
            size_hint_y=None, valign="top", halign="left",
        )
        self.ai_result_label.bind(texture_size=self.ai_result_label.setter("size"))
        scroll.add_widget(self.ai_result_label)
        layout.add_widget(scroll)
        tab.add_widget(layout)
        return tab

    def _sync_ai_vehicle_context(self):
        """Isi otomatis kolom konteks kendaraan dari Profil Kendaraan, tanpa
        menimpa kalau user sudah mengubahnya sendiri secara manual."""
        if not hasattr(self, "ai_context_input"):
            return
        brand = self.brand_spinner.text if hasattr(self, "brand_spinner") else ""
        if brand.startswith("--"):
            brand = ""
        model = self.model_input.text.strip() if hasattr(self, "model_input") else ""
        year = self.year_input.text.strip() if hasattr(self, "year_input") else ""
        fuel_type = self.fuel_type_spinner.text if hasattr(self, "fuel_type_spinner") else ""
        parts = " ".join(p for p in (brand, model, year) if p)
        auto_value = f"{parts} ({fuel_type})" if parts else fuel_type
        current = self.ai_context_input.text
        if current == "" or current == self._ai_context_auto_value:
            self.ai_context_input.text = auto_value
        self._ai_context_auto_value = auto_value

    def _send_ai_diagnostic(self):
        api_key = self.ai_key_input.text.strip()
        endpoint = self.ai_endpoint_input.text.strip()
        if not api_key or not endpoint:
            show_popup("AI Diagnostic", "Isi dulu API Endpoint & API Key.")
            return

        dtc_text = self.dtc_result_label.text if hasattr(self, "dtc_result_label") else "(belum dibaca)"
        findings = run_diagnostic_assistant(self.last_monitor_values)
        prompt = (
            f"Kendaraan: {self.ai_context_input.text or 'tidak disebutkan'}\n"
            f"Data live monitor terakhir: {json.dumps(self.last_monitor_values)}\n"
            f"Hasil Diagnostic Assistant offline:\n" + "\n".join(findings) + "\n"
            f"DTC terbaca: {dtc_text}\n\n"
            "Sebagai asisten diagnostik kendaraan, tolong analisis data di atas: "
            "kemungkinan penyebab masalah (kalau ada), dan saran tindak lanjut "
            "untuk pemilik kendaraan awam. Jawab singkat dalam Bahasa Indonesia."
        )

        body = json.dumps({
            "model": self.ai_model_input.text.strip() or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
        })
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        self.ai_result_label.text = "Mengirim ke AI..."
        UrlRequest(
            endpoint, req_body=body, req_headers=headers, method="POST",
            on_success=self._on_ai_success, on_failure=self._on_ai_failure,
            on_error=self._on_ai_failure, timeout=30,
        )

    def _on_ai_success(self, request, result):
        try:
            if isinstance(result, (bytes, str)):
                result = json.loads(result)
            text = result["choices"][0]["message"]["content"]
        except Exception:
            text = f"Respons tidak dikenali: {result}"
        self.ai_result_label.text = text

    def _on_ai_failure(self, request, error):
        self.ai_result_label.text = f"Gagal menghubungi AI: {error}"

    # ------------------------------------------------------------------
    # TAB: Oil Reset Tracker (per-merek)
    # ------------------------------------------------------------------
    def _build_oil_tab(self):
        tab = TabbedPanelItem(text="Oil Tracker")
        layout = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))

        layout.add_widget(Label(
            text="Panduan reset indikator servis/oli (ikuti merek dari tab Profil Kendaraan):",
            size_hint_y=None, height=dp(50), color=(0.6, 0.6, 0.6, 1),
        ))

        scroll_guide = ScrollView(size_hint=(1, 0.45))
        self.oil_guide_label = Label(text="", size_hint_y=None, valign="top", halign="left")
        self.oil_guide_label.bind(texture_size=self.oil_guide_label.setter("size"))
        scroll_guide.add_widget(self.oil_guide_label)
        layout.add_widget(scroll_guide)
        self._refresh_oil_guide()

        layout.add_widget(Label(
            text="Pelacak jadwal servis oli:",
            size_hint_y=None, height=dp(30), color=(0.6, 0.6, 0.6, 1),
        ))

        self.oil_last_km_input = TextInput(text="0", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        layout.add_widget(LabeledRow("KM ganti oli terakhir:", self.oil_last_km_input, label_width=200))

        self.oil_interval_input = TextInput(text="5000", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        layout.add_widget(LabeledRow("Interval servis (km):", self.oil_interval_input, label_width=200))

        self.oil_current_km_input = TextInput(text="0", multiline=False, input_filter="float", size_hint_y=None, height=dp(40))
        layout.add_widget(LabeledRow("KM odometer sekarang:", self.oil_current_km_input, label_width=200))

        compute_btn = Button(text="Hitung Status Servis", size_hint_y=None, height=dp(44))
        compute_btn.bind(on_release=lambda *_: self._compute_oil_status())
        layout.add_widget(compute_btn)

        self.oil_status_label = Label(text="", size_hint_y=None, height=dp(50))
        layout.add_widget(self.oil_status_label)

        layout.add_widget(BoxLayout())
        tab.add_widget(layout)
        return tab

    def _refresh_oil_guide(self):
        brand = self.brand_spinner.text if hasattr(self, "brand_spinner") else ""
        if brand.startswith("--"):
            brand = ""
        self.oil_guide_label.text = get_oil_guide_for_brand(brand)

    def _compute_oil_status(self):
        try:
            last_km = float(self.oil_last_km_input.text or 0)
            interval = float(self.oil_interval_input.text or 0)
            current_km = float(self.oil_current_km_input.text or 0)
        except ValueError:
            self.oil_status_label.text = "Isi semua angka dengan benar dulu."
            return
        _, status = compute_oil_status(last_km, interval, current_km)
        self.oil_status_label.text = status


if __name__ == "__main__":
    OBD2App().run()
