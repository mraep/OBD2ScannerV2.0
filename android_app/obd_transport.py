"""
Transport layer abstraksi supaya ELM327Client (elm327.py) bisa jalan di atas
3 jenis koneksi (Bluetooth klasik Android, USB serial Android, WiFi TCP
socket) tanpa perlu tahu detail masing-masing. Setiap transport hanya perlu
punya method: connect(), write(bytes), read_until(terminator), close().
"""
import socket
import time


class TransportError(Exception):
    """Dilempar kalau koneksi gagal - pesannya ditampilkan langsung ke user."""


class WifiTransport:
    """Koneksi TCP biasa ke adapter ELM327 WiFi - jalan sama persis di
    Android maupun desktop, karena cuma modul socket standar Python."""

    def __init__(self, ip, port, timeout=5):
        self.ip = ip
        self.port = int(port)
        self.timeout = timeout
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.create_connection((self.ip, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
        except OSError as e:
            raise TransportError(f"Gagal konek ke {self.ip}:{self.port} - {e}")

    def write(self, data: bytes):
        self.sock.sendall(data)

    def read_until(self, terminator=b">", overall_timeout=5):
        buf = b""
        end_time = time.time() + overall_timeout
        while time.time() < end_time:
            try:
                chunk = self.sock.recv(256)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if terminator in buf:
                break
        return buf

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


class BluetoothTransport:
    """Koneksi Bluetooth klasik (RFCOMM/SPP) di Android lewat pyjnius,
    membungkus java.io.InputStream/OutputStream jadi API read/write mirip
    socket biasa. HANYA jalan di Android (butuh pyjnius + Android Bluetooth
    API bawaan) - ini bagian paling berisiko dari port ini, lihat catatan di
    README_ANDROID.md."""

    SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"

    def __init__(self, mac_address, timeout=5):
        self.mac_address = mac_address
        self.timeout = timeout
        self.socket = None
        self.input_stream = None
        self.output_stream = None

    def connect(self):
        try:
            from jnius import autoclass
        except ImportError:
            raise TransportError("Modul pyjnius tidak tersedia (cuma jalan di Android).")

        BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
        UUID = autoclass("java.util.UUID")

        adapter = BluetoothAdapter.getDefaultAdapter()
        if adapter is None:
            raise TransportError("Perangkat ini tidak punya adapter Bluetooth.")
        try:
            device = adapter.getRemoteDevice(self.mac_address)
            uuid = UUID.fromString(self.SPP_UUID)
            self.socket = device.createRfcommSocketToServiceRecord(uuid)
            try:
                adapter.cancelDiscovery()
            except Exception:
                pass
            self.socket.connect()
            self.input_stream = self.socket.getInputStream()
            self.output_stream = self.socket.getOutputStream()
        except Exception as e:
            raise TransportError(
                f"Gagal konek Bluetooth ke {self.mac_address} - {e}. "
                "Pastikan device sudah di-pair lewat tab Koneksi > Tambah Perangkat Bluetooth."
            )

    def write(self, data: bytes):
        self.output_stream.write(bytearray(data))
        self.output_stream.flush()

    def read_until(self, terminator=b">", overall_timeout=5):
        buf = b""
        end_time = time.time() + overall_timeout
        while time.time() < end_time:
            try:
                available = self.input_stream.available()
            except Exception:
                available = 0
            if available > 0:
                # Baca byte-per-byte lewat JNI - agak lambat tapi paling
                # kompatibel lintas versi Android tanpa buffered stream ekstra.
                chunk = bytes(self.input_stream.read() & 0xFF for _ in range(available))
                buf += chunk
                if terminator in buf:
                    break
            else:
                time.sleep(0.05)
        return buf

    def close(self):
        try:
            if self.socket:
                self.socket.close()
        except Exception:
            pass


class UsbTransport:
    """Koneksi USB serial di Android lewat usb4a/usbserial4a (kabel OBD2 USB
    dengan Android di mode USB host). HANYA jalan di Android."""

    def __init__(self, device_name=None, baudrate=38400, timeout=5):
        self.device_name = device_name
        self.baudrate = baudrate
        self.timeout = timeout
        self.serial_port = None

    def connect(self):
        try:
            from usb4a import usb
            from usbserial4a import serial4a
        except ImportError:
            raise TransportError("Modul usb4a/usbserial4a tidak tersedia (cuma jalan di Android).")

        device_list = usb.get_usb_device_list()
        if not device_list:
            raise TransportError("Tidak ada perangkat USB terdeteksi. Cek kabel OBD2 USB sudah dicolok.")

        target = None
        if self.device_name:
            for d in device_list:
                if d.getDeviceName() == self.device_name:
                    target = d
                    break
        if target is None:
            target = device_list[0]  # fallback: ambil device USB pertama yang ada

        if not usb.has_usb_permission(target):
            usb.request_usb_permission(target)
            raise TransportError(
                "Meminta izin akses USB ke Android - setujui dialog izin yang "
                "muncul, lalu tekan Sambungkan lagi."
            )

        self.serial_port = serial4a.get_serial_port(target.getDeviceName(), self.baudrate, 8, 1, "N")
        if self.serial_port is None or not self.serial_port.isOpen():
            raise TransportError("Gagal membuka port USB serial.")

    def write(self, data: bytes):
        self.serial_port.write(data)

    def read_until(self, terminator=b">", overall_timeout=5):
        buf = b""
        end_time = time.time() + overall_timeout
        while time.time() < end_time:
            n = self.serial_port.inWaiting()
            if n > 0:
                chunk = bytes(self.serial_port.read(n))
                buf += chunk
                if terminator in buf:
                    break
            else:
                time.sleep(0.05)
        return buf

    def close(self):
        try:
            if self.serial_port:
                self.serial_port.close()
        except Exception:
            pass
