# Quick QR Scanner – Android build

## Folder layout
```
QRLocator_android/
├── main.py            (must be named main.py)
├── buildozer.spec
└── sound/
    └── qrsound.wav    <- copy your PCM WAV here
```
If the WAV is missing, the app plays a generated beep instead.

## Build the APK (Linux, or Windows via WSL2 Ubuntu)
```
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip autoconf libtool pkg-config zlib1g-dev libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev
pip install --user buildozer cython==0.29.36

cd QRLocator_android
buildozer android debug          # first build is slow (compiles OpenCV)
buildozer android deploy run     # with the phone connected via USB debugging
```
The APK ends up in `bin/`. You can also copy it to the phone and install it.

## Tuning (top of main.py)
- `CAMERA_ROTATION`: if the preview is sideways/upside-down, try 0, 90, 180 or 270.
- `CAMERA_INDEX`: 0 = back camera.
- `SCAN_FPS`: lower (e.g. 6) if the phone is slow.

## Notes
- Sound plays on the phone's media volume.
- Debug logs: `buildozer android logcat | grep python`
