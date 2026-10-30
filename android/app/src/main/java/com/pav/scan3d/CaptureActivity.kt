package com.pav.scan3d

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.media.MediaActionSound
import android.os.Bundle
import android.os.SystemClock
import android.util.Size
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.asin
import kotlin.math.atan2
import kotlin.math.sqrt

/** Guided photo capture: walk around the item; photos are taken automatically
 *  for each new angle once the phone is held still. */
class CaptureActivity : ComponentActivity(), SensorEventListener {

    companion object {
        const val EXTRA_SCAN = "scan"
    }

    private lateinit var scan: LocalScan
    private lateinit var prefs: Prefs
    private lateinit var previewView: PreviewView
    private lateinit var coverage: CoverageView
    private lateinit var guide: TextView
    private lateinit var focusInfo: TextView
    private lateinit var countText: TextView
    private lateinit var autoBtn: Button
    private lateinit var flash: View

    private var camera: Camera? = null
    private var imageCapture: ImageCapture? = null
    private lateinit var cameraExecutor: ExecutorService
    private val sound = MediaActionSound()

    private lateinit var sensors: SensorManager
    private val rot = FloatArray(9)
    private var downDeg = 0f
    private var headingDeg = 0f
    private var lastMoveAt = 0L
    private var lastShotAt = 0L
    private var capturing = false
    private var focusLocked = false
    private var photoCount = 0

    private val permission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) startCamera() else {
            toast("Scan3D needs the camera to take photos.")
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val s = ScanStore.get(this, intent.getStringExtra(EXTRA_SCAN) ?: "")
        if (s == null) {
            finish()
            return
        }
        scan = s
        photoCount = scan.photos.size
        cameraExecutor = Executors.newSingleThreadExecutor()
        sensors = getSystemService(SENSOR_SERVICE) as SensorManager
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        sound.load(MediaActionSound.SHUTTER_CLICK)
        buildUi()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            permission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun buildUi() {
        val root = FrameLayout(this).apply { setBackgroundColor(Color.BLACK) }
        previewView = PreviewView(this).apply {
            scaleType = PreviewView.ScaleType.FIT_CENTER // show exactly what the photo will contain
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
        }
        root.addView(previewView, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        flash = View(this).apply {
            setBackgroundColor(Color.WHITE)
            alpha = 0f
        }
        root.addView(flash, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))

        val overlay = FrameLayout(this).apply { setPadding(dp(12), dp(8), dp(12), dp(12)) }
        root.addView(overlay, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        overlay.padForSystemBars()

        // top: guidance text (left) + coverage rings (right)
        val top = row().apply { gravity = Gravity.TOP }
        val texts = column().apply {
            background = rounded(Color.argb(150, 0, 0, 0), 12)
            setPadding(dp(12), dp(10), dp(12), dp(10))
        }
        guide = label("Tap the item to lock focus", 15f, Color.WHITE, bold = true)
        focusInfo = label("Focus not locked yet", 13f, C.WARN)
        texts.addView(guide)
        texts.addView(focusInfo, lp(top = dp(4)))
        top.addView(texts, weighted(1f))
        coverage = CoverageView(this)
        top.addView(coverage, LinearLayout.LayoutParams(dp(132), dp(132)).apply { marginStart = dp(10) })
        overlay.addView(top, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP))

        // bottom bar
        val bottom = column().apply {
            background = rounded(Color.argb(160, 0, 0, 0), 16)
            setPadding(dp(12), dp(10), dp(12), dp(12))
        }
        countText = label("", 14f, Color.WHITE)
        countText.gravity = Gravity.CENTER
        bottom.addView(countText, lp())
        val buttons = row()
        autoBtn = button("", C.MUTED) {
            prefs.autoCapture = !prefs.autoCapture
            updateAutoButton()
        }
        val shutter = button("Take photo", C.ACCENT) { takePhoto(coverage.slotFor(downDeg, headingDeg)) }
        val done = button("Done", C.OK) { finishCapture() }
        buttons.addView(autoBtn, weighted(1f))
        buttons.addView(shutter, weighted(1.3f, dp(8)))
        buttons.addView(done, weighted(1f, dp(8)))
        bottom.addView(buttons, lp(top = dp(8)))
        overlay.addView(bottom, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))

        previewView.setOnTouchListener { v, ev ->
            if (ev.action == MotionEvent.ACTION_UP) {
                lockFocus(ev.x, ev.y)
                v.performClick()
            }
            true
        }
        setContentView(root)
        updateAutoButton()
        updateCount()
    }

    private fun updateAutoButton() {
        autoBtn.text = if (prefs.autoCapture) "Auto: ON" else "Auto: OFF"
        autoBtn.background = rounded(if (prefs.autoCapture) C.OK else C.MUTED, 12)
    }

    private fun updateCount() {
        val parts = (0..2).joinToString("   ") { r ->
            "${CoverageView.RING_NAMES[r]} ${coverage.count(r)}/${CoverageView.SECTORS[r]}"
        }
        countText.text = "$photoCount photos  •  $parts"
    }

    private fun startCamera() {
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            val provider = try {
                future.get()
            } catch (e: Exception) {
                toast("Camera unavailable: ${e.message}")
                return@addListener
            }
            val ratio = ResolutionSelector.Builder()
                .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
                .build()
            val preview = Preview.Builder().setResolutionSelector(ratio).build()
            preview.setSurfaceProvider(previewView.surfaceProvider)
            // ~12 MP stills: plenty of detail without huge uploads
            val stills = ResolutionSelector.Builder()
                .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
                .setResolutionStrategy(
                    ResolutionStrategy(Size(4032, 3024), ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER)
                )
                .build()
            val capture = ImageCapture.Builder()
                .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
                .setJpegQuality(95)
                .setResolutionSelector(stills)
                .build()
            try {
                provider.unbindAll()
                camera = provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, capture)
                imageCapture = capture
            } catch (e: Exception) {
                toast("Could not start the camera: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    /** Focus on the tapped point and keep focus locked for the whole scan. */
    private fun lockFocus(x: Float, y: Float) {
        val cam = camera ?: return
        val point = previewView.meteringPointFactory.createPoint(x, y)
        val action = FocusMeteringAction.Builder(point, FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE)
            .disableAutoCancel()
            .build()
        focusInfo.text = "Focusing…"
        focusInfo.setTextColor(Color.WHITE)
        val result = cam.cameraControl.startFocusAndMetering(action)
        result.addListener({
            val ok = try {
                result.get().isFocusSuccessful
            } catch (e: Exception) {
                false
            }
            focusLocked = ok
            if (ok) {
                focusInfo.text = "Focus locked ✓  Keep about the same distance"
                focusInfo.setTextColor(C.OK)
            } else {
                focusInfo.text = "Couldn't focus – tap the item again"
                focusInfo.setTextColor(C.WARN)
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun takePhoto(slot: CoverageView.Slot?) {
        val ic = imageCapture ?: return
        if (capturing) return
        capturing = true
        lastShotAt = SystemClock.elapsedRealtime()
        val file = scan.nextPhotoFile()
        val options = ImageCapture.OutputFileOptions.Builder(file).build()
        ic.takePicture(options, cameraExecutor, object : ImageCapture.OnImageSavedCallback {
            override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                runOnUiThread {
                    capturing = false
                    photoCount++
                    slot?.let { coverage.mark(it) }
                    sound.play(MediaActionSound.SHUTTER_CLICK)
                    flash.alpha = 0.6f
                    flash.animate().alpha(0f).setDuration(180).start()
                    updateCount()
                }
            }

            override fun onError(exception: ImageCaptureException) {
                runOnUiThread {
                    capturing = false
                    toast("Photo failed: ${exception.message}")
                }
            }
        })
    }

    // ---------------------------------------------------------------- sensors

    override fun onResume() {
        super.onResume()
        val rv = sensors.getDefaultSensor(Sensor.TYPE_GAME_ROTATION_VECTOR)
            ?: sensors.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
        rv?.let { sensors.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME) }
        sensors.getDefaultSensor(Sensor.TYPE_GYROSCOPE)?.let {
            sensors.registerListener(this, it, SensorManager.SENSOR_DELAY_GAME)
        }
    }

    override fun onPause() {
        super.onPause()
        sensors.unregisterListener(this)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onSensorChanged(e: SensorEvent) {
        when (e.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> {
                val m = sqrt(e.values[0] * e.values[0] + e.values[1] * e.values[1] + e.values[2] * e.values[2])
                if (m > 0.12f) lastMoveAt = SystemClock.elapsedRealtime()
            }
            Sensor.TYPE_GAME_ROTATION_VECTOR, Sensor.TYPE_ROTATION_VECTOR -> {
                SensorManager.getRotationMatrixFromVector(rot, e.values)
                // The back camera looks along the device's -Z axis.
                val dx = -rot[2]
                val dy = -rot[5]
                val dz = -rot[8]
                downDeg = Math.toDegrees(asin((-dz).coerceIn(-1f, 1f).toDouble())).toFloat()
                headingDeg = ((Math.toDegrees(atan2(dx.toDouble(), dy.toDouble())) + 360.0) % 360.0).toFloat()
                onPose()
            }
        }
    }

    private fun onPose() {
        val slot = coverage.slotFor(downDeg, headingDeg)
        if (slot != coverage.current) coverage.current = slot
        guide.text = when {
            !focusLocked -> "Tap the item to lock focus"
            downDeg < 3f -> "Tilt the phone down towards the item"
            slot == null -> "Hold steady"
            coverage.isFilled(slot) -> nextHint()
            else -> "${CoverageView.RING_NAMES[slot.ring]} angle – hold still…"
        }
        if (!prefs.autoCapture || !focusLocked || slot == null || capturing) return
        if (coverage.isFilled(slot)) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastMoveAt > 350 && now - lastShotAt > 700) takePhoto(slot)
    }

    private fun nextHint(): String {
        for (r in 0..2) {
            if (coverage.count(r) < CoverageView.SECTORS[r]) {
                val where = when (r) {
                    0 -> "Low: phone near table height, tilted slightly down"
                    1 -> "Middle: about 35° down"
                    else -> "High: looking well down on the item"
                }
                return "Walk on around the item.\n$where"
            }
        }
        if (coverage.count(3) == 0) return "Nearly done – add a shot from directly above"
        return "All angles covered – tap Done"
    }

    private fun finishCapture() {
        if (photoCount < 30) {
            android.app.AlertDialog.Builder(this)
                .setTitle("Only $photoCount photos")
                .setMessage("Accurate scans need 50–80 photos from all the way around. Finish anyway?")
                .setPositiveButton("Keep scanning", null)
                .setNegativeButton("Finish") { _, _ -> openScan() }
                .show()
        } else {
            openScan()
        }
    }

    private fun openScan() {
        startActivity(Intent(this, ScanActivity::class.java).putExtra(ScanActivity.EXTRA_SCAN, scan.name))
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        if (::cameraExecutor.isInitialized) cameraExecutor.shutdown()
        sound.release()
    }
}
