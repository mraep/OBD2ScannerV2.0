"""
Helper Bluetooth classic Android: list device yang sudah di-pair, scan device
BARU di sekitar, dan mulai proses pairing (createBond). HANYA berjalan di
Android - modul ini di-import lazy (di dalam fungsi) supaya file ini tetap
bisa di-import di desktop tanpa error saat cuma dibaca/dicek.

Ini bagian PALING BERISIKO dari port Android ini (lihat README_ANDROID.md) -
Bluetooth classic di Android tidak sesederhana di Linux/Windows, dan pairing
lewat app pihak ketiga kadang tetap memunculkan dialog sistem Android sendiri.
"""


def get_bonded_devices():
    """Ambil daftar device yang SUDAH pernah di-pair sebelumnya (lewat
    Settings Android ataupun lewat pair_device() di bawah)."""
    from jnius import autoclass
    BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")

    adapter = BluetoothAdapter.getDefaultAdapter()
    if adapter is None:
        return []
    bonded = adapter.getBondedDevices().toArray()
    return [(d.getAddress(), d.getName() or d.getAddress()) for d in bonded]


_active_receiver = {"receiver": None}


def start_discovery(on_device_found, on_finished):
    """Mulai scan device Bluetooth baru di sekitar (Android otomatis
    menghentikan setelah ~12 detik). on_device_found(mac, name) dipanggil
    tiap device baru ketemu; on_finished() dipanggil saat scan selesai."""
    from jnius import autoclass, PythonJavaClass, java_method

    BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
    BluetoothDevice = autoclass("android.bluetooth.BluetoothDevice")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    IntentFilter = autoclass("android.content.IntentFilter")

    class _DiscoveryReceiver(PythonJavaClass):
        __javainterfaces__ = ["android/content/BroadcastReceiver"]
        __javacontext__ = "app"

        @java_method("(Landroid/content/Context;Landroid/content/Intent;)V")
        def onReceive(self, context, intent):
            action = intent.getAction()
            if action == BluetoothDevice.ACTION_FOUND:
                device = intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                if device is not None:
                    on_device_found(device.getAddress(), device.getName() or device.getAddress())
            elif action == BluetoothAdapter.ACTION_DISCOVERY_FINISHED:
                on_finished()

    adapter = BluetoothAdapter.getDefaultAdapter()
    if adapter is None:
        on_finished()
        return

    activity = PythonActivity.mActivity
    receiver = _DiscoveryReceiver()
    _active_receiver["receiver"] = receiver

    activity.registerReceiver(receiver, IntentFilter(BluetoothDevice.ACTION_FOUND))
    activity.registerReceiver(receiver, IntentFilter(BluetoothAdapter.ACTION_DISCOVERY_FINISHED))

    if adapter.isDiscovering():
        adapter.cancelDiscovery()
    adapter.startDiscovery()


def stop_discovery():
    from jnius import autoclass
    BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")

    adapter = BluetoothAdapter.getDefaultAdapter()
    if adapter and adapter.isDiscovering():
        adapter.cancelDiscovery()
    receiver = _active_receiver.get("receiver")
    if receiver is not None:
        try:
            PythonActivity.mActivity.unregisterReceiver(receiver)
        except Exception:
            pass
        _active_receiver["receiver"] = None


def pair_device(mac_address):
    """Mulai proses pairing (createBond) ke device MAC tertentu. Android
    akan menampilkan dialog konfirmasi PIN sistemnya sendiri - fungsi ini
    cuma memicu proses itu, bukan mengisi PIN otomatis."""
    from jnius import autoclass
    BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
    BluetoothDevice = autoclass("android.bluetooth.BluetoothDevice")

    adapter = BluetoothAdapter.getDefaultAdapter()
    device = adapter.getRemoteDevice(mac_address)
    if device.getBondState() == BluetoothDevice.BOND_BONDED:
        return True
    return device.createBond()


def request_runtime_permissions():
    """Android 12+ (API 31+) butuh izin runtime BLUETOOTH_SCAN/CONNECT,
    selain ACCESS_FINE_LOCATION untuk versi di bawahnya. Panggil ini sekali
    saat app start."""
    try:
        from android.permissions import request_permissions, Permission
        request_permissions([
            Permission.BLUETOOTH,
            Permission.BLUETOOTH_ADMIN,
            Permission.ACCESS_FINE_LOCATION,
            Permission.ACCESS_COARSE_LOCATION,
        ])
        # BLUETOOTH_SCAN/BLUETOOTH_CONNECT (API 31+) belum tentu ada di semua
        # versi python-for-android's Permission enum - coba tambahkan kalau ada.
        extra = []
        for name in ("BLUETOOTH_SCAN", "BLUETOOTH_CONNECT"):
            perm = getattr(Permission, name, None)
            if perm:
                extra.append(perm)
        if extra:
            request_permissions(extra)
    except ImportError:
        pass  # bukan di Android, lewati saja
