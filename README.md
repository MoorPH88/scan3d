# Scan3D

Phone photos of an item on a printed marker mat → a true-scale (1:1, mm) STL.

- `android/` – Scan3D phone app (Kotlin, CameraX). Guided capture: coverage rings
  for low / middle / high angles, auto-capture when the phone is steady, focus lock,
  upload to the PC, progress, save/share the STL.
- `pc/` – ScanStation for a Windows PC with an NVIDIA GPU. COLMAP (CUDA) does the
  photogrammetry; the mat's ArUco markers are triangulated to set exact millimetre
  scale and a Z-up frame; Open3D/trimesh turn the dense cloud into a watertight STL
  with a flat base. See `pc/README.txt`.
- Every push to `main` builds `Scan3D.apk` and `ScanStation-PC.zip` and publishes
  them as the **latest** release.

Accuracy checks (synthetic scans with known geometry, `pc/tests/`): scale error
0.008 % (≈0.01 mm over 150 mm), mesh-to-truth deviation 0.06 mm RMS.
