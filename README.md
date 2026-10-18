# PB-XDelete

Enterprise turbo delete tool (engine + PyQt6 GUI).

## Release

- `release/pb_xdelete_gui.exe` — GUI (Nuitka onefile, V1.0.8)
- `release/pb_xdelete.exe` — delete engine

## Build

```bat
build_pb_xdelete.bat
build_pb_xdelete_gui.bat
```

## Source

- `pb_xdelete.cpp` — Win32 turbo delete engine
- `pb_xdelete_gui.py` — PyQt6 launcher

## Test data

```bat
python scripts\gen_xdelete_testdata.py --files 10000 --clean
```
