ScanStation - the PC half of Scan3D
====================================

Your phone takes the photos; this PC (NVIDIA GPU) turns them into a
true-scale (1:1, millimetre) STL.

ONE-TIME SETUP
--------------
1. Unzip this folder somewhere simple, e.g. C:\Scan3D\ScanStation
2. Double-click setup.bat. It installs Python 3.12 (if needed), the
   libraries, and COLMAP (the photogrammetry engine, ~1 GB download).
   At the end it prints a check list - everything should say [ok].
3. Print Scan3D-mat-A4.pdf (or -A3 for bigger items) at 100% / "Actual
   size". Never "Fit to page".
4. Measure line A (180 mm) and line B (100 mm) on the print with calipers
   or a steel rule. If they're off, open config.json in Notepad and set
   your measured values, e.g.  "mat_check_mm": {"A4": [179.6, 99.8], ...}
   This corrects for printer scaling, which matters at 0.5 mm accuracy.
5. Glue the mat to stiff card or foam board so it stays perfectly flat.

EVERY SCAN
----------
1. Double-click start.bat and leave the window open. The first time,
   Windows asks about network access: tick "Private networks", Allow.
2. In the Scan3D phone app: Find PC, enter the pairing code shown in the
   window (first time only).
3. Scan, tap Done, then "Send to PC and make STL".
4. The STL lands in the "scans" folder here (and can be saved to the phone).
   Typical time: 10-30 minutes depending on the GPU and photo count.

No phone app handy? Copy the photos into a folder and drag that folder onto
process_folder.bat.

FOR 0.5 mm ACCURACY
-------------------
- Matte, textured surfaces scan best. Shiny, clear, black or plain items
  need a thin coat of 3D-scanning spray (vanishing sprays such as AESUB
  leave only a very thin layer and evaporate afterwards).
- Bright, even light. The mat must be flat and in view in most photos.
- 50-80 sharp photos, all the way round, three heights. Lock focus first.
- Check the report: "Scale check" under ~0.15 mm RMS and "Surface noise"
  under ~0.2 mm are good. Verify a known dimension with calipers the first
  few times.
- The underside isn't photographed, so the STL's base is closed flat where
  the item touches the mat.

SETTINGS (config.json)
----------------------
dense_image_size   2400 default. 3200 = more detail, needs ~16 GB GPU memory.
max_triangles      size of the STL (600,000 is fine for slicers).
keep_work_files    true keeps COLMAP's intermediate files for debugging.
port / pairing_code  network settings for the phone app.

TROUBLESHOOTING
---------------
Phone can't find the PC  - same Wi-Fi? Windows network set to "Private"?
                           Type the address shown in the window instead.
"photos could not be aligned" - blurry photos or a shiny/plain item.
"mat wasn't visible"      - step back or tilt down so black squares show.
Each scan's full log is in scans\<name>\pipeline.log
