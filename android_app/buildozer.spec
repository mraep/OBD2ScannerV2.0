[app]
title = OBD2 Scanner
package.name = obd2scanner
package.domain = org.obd2scanner
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0

# python3, kivy = UI. pyjnius = akses API Android (Bluetooth) dari Python.
# usb4a/usbserial4a = akses USB serial host mode Android (kabel OBD2 USB).
requirements = python3,kivy==2.3.0,pyjnius,usb4a,usbserial4a

orientation = portrait
fullscreen = 0

# Izin Android yang dibutuhkan:
# - BLUETOOTH/BLUETOOTH_ADMIN: koneksi Bluetooth classic (semua versi Android)
# - BLUETOOTH_SCAN/BLUETOOTH_CONNECT: wajib di Android 12+ (API 31+)
# - ACCESS_FINE_LOCATION/ACCESS_COARSE_LOCATION: wajib untuk discovery Bluetooth di Android <12
# - INTERNET/ACCESS_NETWORK_STATE: koneksi WiFi ke adapter ELM327 WiFi
android.permissions = BLUETOOTH,BLUETOOTH_ADMIN,BLUETOOTH_SCAN,BLUETOOTH_CONNECT,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION,INTERNET,ACCESS_NETWORK_STATE

android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
