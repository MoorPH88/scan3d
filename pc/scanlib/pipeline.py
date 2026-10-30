"""Photos -> COLMAP (GPU) -> true-scale alignment -> watertight STL."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import traceback
from pathlib import Path

from . import markers, mat, mesh

ROOT = Path(__file__).resolve().parent.parent

# share of the progress bar each stage takes
STAGES = [
    ("features", "Finding features in photos", 0.06),
    ("matching", "Matching photos", 0.12),
    ("mapping", "Solving camera positions", 0.14),
    ("scale", "Measuring the mat (true scale)", 0.05),
    ("undistort", "Preparing images", 0.03),
    ("stereo", "Dense 3D reconstruction (GPU)", 0.42),
    ("fusion", "Merging depth maps", 0.08),
    ("mesh", "Building the STL", 0.10),
]
IMAGE_EXT = (".jpg", ".jpeg", ".png")


class PipelineError(RuntimeError):
    pass


def find_colmap(cfg: dict) -> str | None:
    p = cfg.get("colmap_path") or ""
    if p and Path(p).exists():
        return str(Path(p))
    tools = ROOT / "tools"
    if tools.exists():
        for pattern in ("**/bin/colmap.exe", "**/colmap.exe", "**/COLMAP.bat", "**/bin/colmap"):
            hits = sorted(tools.glob(pattern))
            if hits:
                return str(hits[0])
    for name in ("colmap", "COLMAP.bat", "colmap.exe"):
        w = shutil.which(name)
        if w:
            return w
    return None


class Pipeline:
    def __init__(self, scan_dir: str | Path, cfg: dict, on_status=None):
        self.scan = Path(scan_dir)
        self.cfg = cfg
        self.images = self.scan / "images"
        self.work = self.scan / "work"
        self.on_status = on_status or (lambda s: None)
        self.log_file = open(self.scan / "pipeline.log", "a", encoding="utf-8")
        self._stage_i = 0
        self.status = dict(state="running", stage="", stage_label="", progress=0.0, message="")

    # ------------------------------------------------------------ reporting
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        self.log_file.write(line + "\n")
        self.log_file.flush()

    def _set(self, frac: float | None = None, message: str | None = None) -> None:
        done = sum(w for _, _, w in STAGES[: self._stage_i])
        w = STAGES[self._stage_i][2]
        if frac is not None:
            self.status["progress"] = round(min(0.999, done + w * max(0.0, min(1.0, frac))), 4)
        if message is not None:
            self.status["message"] = message
        self.on_status(dict(self.status))

    def _stage(self, key: str) -> None:
        self._stage_i = [k for k, _, _ in STAGES].index(key)
        label = STAGES[self._stage_i][1]
        self.status.update(stage=key, stage_label=label)
        self.log(f"=== {label}")
        self._set(0.0, label)

    # ------------------------------------------------------------ COLMAP
    def colmap(self, *args: str, progress_re: str | None = None, progress_total: int | None = None) -> None:
        exe = find_colmap(self.cfg)
        if not exe:
            raise PipelineError("COLMAP was not found. Run setup.bat again.")
        cmd = [exe, *args]
        env = os.environ.copy()
        exe_dir = Path(exe).parent
        env["PATH"] = os.pathsep.join([str(exe_dir), str(exe_dir.parent / "lib"), env.get("PATH", "")])
        self.log("$ " + " ".join(cmd))
        rx = re.compile(progress_re) if progress_re else None
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        tail: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            self.log_file.write(line + "\n")
            tail = (tail + [line])[-25:]
            if rx:
                m = rx.search(line)
                if m:
                    a = int(m.group(1))
                    b = int(m.group(2)) if m.re.groups > 1 else (progress_total or 0)
                    if b > 0:
                        self._set(a / b)
        proc.wait()
        self.log_file.flush()
        if proc.returncode != 0:
            joined = "\n".join(tail)
            if "CUDA" in joined and ("requires" in joined or "not available" in joined or "No CUDA" in joined):
                raise PipelineError(
                    "COLMAP could not use your NVIDIA GPU. Update the NVIDIA driver and make sure "
                    "the CUDA build of COLMAP is installed (run setup.bat)."
                )
            raise PipelineError(f"COLMAP step '{args[0]}' failed (code {proc.returncode}). Last output:\n{joined}")

    # ------------------------------------------------------------ main
    def run(self) -> dict:
        t0 = time.time()
        try:
            return self._run(t0)
        except (PipelineError, markers.ScaleError, mesh.MeshError) as e:
            self.log(f"ERROR: {e}")
            self.status.update(state="error", message=str(e))
            self.on_status(dict(self.status))
            raise
        except Exception as e:
            self.log("ERROR: " + traceback.format_exc())
            self.status.update(state="error", message=f"Unexpected error: {e}")
            self.on_status(dict(self.status))
            raise
        finally:
            self.log_file.close()

    def _run(self, t0: float) -> dict:
        photos = sorted(p for p in self.images.iterdir() if p.suffix.lower() in IMAGE_EXT)
        if len(photos) < 15:
            raise PipelineError(f"Only {len(photos)} photos - take at least 40 (60-80 is best).")
        self.log(f"Scan {self.scan.name}: {len(photos)} photos")
        if self.work.exists():
            shutil.rmtree(self.work, ignore_errors=True)
        self.work.mkdir(parents=True)
        db = self.work / "database.db"
        sparse = self.work / "sparse"
        sparse.mkdir()

        self._stage("features")
        self.colmap(
            "feature_extractor",
            "--database_path", str(db),
            "--image_path", str(self.images),
            "--ImageReader.single_camera", "1",
            "--ImageReader.camera_model", "OPENCV",
            progress_re=r"Processed file \[(\d+)/(\d+)\]",
        )

        self._stage("matching")
        self.colmap(
            "exhaustive_matcher",
            "--database_path", str(db),
            progress_re=r"Processing block \[(\d+)/(\d+)",
        )

        self._stage("mapping")
        n_photos = len(photos)
        self.colmap(
            "mapper",
            "--database_path", str(db),
            "--image_path", str(self.images),
            "--output_path", str(sparse),
            progress_re=r"num_reg_frames=(\d+)",
            progress_total=n_photos,
        )
        models = [d for d in sparse.iterdir() if d.is_dir()]
        if not models:
            raise PipelineError(
                "The photos could not be aligned. Usually this means blurry photos, a shiny or "
                "plain item without texture, or too few overlapping shots."
            )
        best, best_n = None, -1
        for m in models:
            txt = self.work / f"txt_{m.name}"
            txt.mkdir(exist_ok=True)
            self.colmap("model_converter", "--input_path", str(m), "--output_path", str(txt), "--output_type", "TXT")
            _, imgs = markers.read_text_model(str(txt))
            if len(imgs) > best_n:
                best, best_n = m, len(imgs)
        self.log(f"Aligned {best_n} of {n_photos} photos (model {best.name})")
        if best_n < max(12, 0.5 * n_photos):
            raise PipelineError(
                f"Only {best_n} of {n_photos} photos could be aligned. Keep the item and mat in frame, "
                "move slowly, and avoid blur. Shiny/clear items need matte scanning spray."
            )
        txt_dir = self.work / f"txt_{best.name}"

        self._stage("scale")
        transform = markers.align(
            str(txt_dir), str(self.images), self._print_correction(), progress=lambda f: self._set(f), log=self.log
        )
        (self.work / "transform.json").write_text(json.dumps(transform, indent=2))

        dense = self.work / "dense"
        self._stage("undistort")
        self.colmap(
            "image_undistorter",
            "--image_path", str(self.images),
            "--input_path", str(best),
            "--output_path", str(dense),
            "--output_type", "COLMAP",
            "--max_image_size", str(int(self.cfg.get("dense_image_size", 2400))),
        )

        self._stage("stereo")
        self.colmap(
            "patch_match_stereo",
            "--workspace_path", str(dense),
            "--workspace_format", "COLMAP",
            "--PatchMatchStereo.geom_consistency", "true",
            progress_re=r"Processing view (\d+) / (\d+)",
        )

        self._stage("fusion")
        fused = self.work / "fused.ply"
        self.colmap(
            "stereo_fusion",
            "--workspace_path", str(dense),
            "--workspace_format", "COLMAP",
            "--input_type", "geometric",
            "--output_path", str(fused),
            progress_re=r"Fusing image \[(\d+)/(\d+)\]",
        )

        self._stage("mesh")
        name = self.scan.name
        stl = self.scan / f"{name}.stl"
        L = mat.layout(transform["mat"])
        info = mesh.build(
            str(fused),
            transform,
            str(stl),
            mat_half=L["inner_half"],
            max_triangles=int(self.cfg.get("max_triangles", 600_000)),
            flat_base=bool(self.cfg.get("flat_base", True)),
            log=self.log,
        )
        self._set(0.8, "Rendering preview")
        try:
            mesh.render_preview(str(stl), str(self.scan / "preview.png"))
        except Exception as e:
            self.log(f"Preview failed (not important): {e}")

        if not self.cfg.get("keep_work_files", False):
            shutil.rmtree(dense, ignore_errors=True)
            try:
                db.unlink()
            except OSError:
                pass

        report = dict(
            name=name,
            stl=stl.name,
            photos=n_photos,
            photos_aligned=best_n,
            mat=transform["mat"],
            scale_fit_rms_mm=round(transform["rms_mm"], 3),
            scale_fit_max_mm=round(transform["max_mm"], 3),
            mat_points=transform["points_used"],
            photos_with_mat=transform["photos_with_markers"],
            print_correction=[round(v, 5) for v in transform["print_correction"]],
            minutes=round((time.time() - t0) / 60, 1),
            **info,
        )
        (self.scan / "report.json").write_text(json.dumps(report, indent=2))
        self.log(f"Done in {report['minutes']} min -> {stl}")
        self.status.update(state="done", progress=1.0, message="Finished", stage="done", stage_label="Finished")
        self.on_status(dict(self.status))
        return report

    def _print_correction(self) -> dict:
        """Per-mat scale factors from the user's measured check lines A and B."""
        measured = self.cfg.get("mat_check_mm", {}) or {}
        out = {}
        for name in mat.MATS:
            design = mat.layout(name)["check_len"]
            m = measured.get(name) or design
            out[name] = (float(m[0]) / design[0], float(m[1]) / design[1])
        return out
