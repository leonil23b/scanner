[app]

title = Quick QR Scanner
package.name = quickqrscanner
package.domain = org.jnt

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,wav
source.include_patterns = sound/*

version = 1.0

# opencv + numpy are built from source by python-for-android (slow first build)
requirements = python3,kivy,pillow,pyzbar,pyjnius,android

orientation = portrait
fullscreen = 0

# Camera is requested at runtime in main.py; VIBRATE is a normal permission
android.permissions = CAMERA,VIBRATE

# minapi 26 = Android 8.0 (works on 8.0 and anything newer)
android.api = 33
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a

android.accept_sdk_license = True
android.debug_artifact = apk

[buildozer]
log_level = 2
warn_on_root = 1
