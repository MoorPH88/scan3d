package com.pav.scan3d

import android.content.ContentValues
import android.content.Intent
import android.graphics.BitmapFactory
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.util.Locale
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/** Send a scan's photos to the PC, follow processing, then save/share the STL. */
class ScanActivity : ComponentActivity() {

    companion object {
        const val EXTRA_SCAN = "scan"
    }

    private lateinit var scan: LocalScan
    private lateinit var prefs: Prefs
    private val io: ExecutorService = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var polling = false
    private var busy = false
    private var resumed = false

    private lateinit var titleText: TextView
    private lateinit var info: TextView
    private lateinit var stateText: TextView
    private lateinit var progress: ProgressBar
    private lateinit var preview: ImageView
    private lateinit var report: TextView
    private lateinit var actions: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val s = ScanStore.get(this, intent.getStringExtra(EXTRA_SCAN) ?: "")
        if (s == null) {
            finish()
            return
        }
        scan = s
        buildUi()
    }

    private fun buildUi() {
        val scroll = ScrollView(this).apply { setBackgroundColor(C.BG) }
        val col = column(16)
        scroll.addView(col)
        scroll.padForSystemBars()

        titleText = label(scan.name, 22f, bold = true)
        info = label("", 14f, C.SUB)
        col.addView(titleText)
        col.addView(info, lp(top = dp(2)))

        val card = column(16).apply { background = rounded(C.CARD) }
        stateText = label("", 16f)
        progress = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 1000
            visibility = View.GONE
        }
        card.addView(stateText)
        card.addView(progress, lp(top = dp(10)))
        col.addView(card, lp(top = dp(14)))

        preview = ImageView(this).apply {
            adjustViewBounds = true
            visibility = View.GONE
            background = rounded(0xFFFFFFFF.toInt())
            setPadding(dp(6), dp(6), dp(6), dp(6))
        }
        col.addView(preview, lp(top = dp(14)))
        report = label("", 14f, C.TEXT)
        col.addView(report, lp(top = dp(10)))

        actions = column()
        col.addView(actions, lp(top = dp(12)))
        setContentView(scroll)
    }

    override fun onResume() {
        super.onResume()
        resumed = true
        refreshFromDisk()
        if (scan.serverId.isNotEmpty() && scan.state !in listOf("", "local")) startPolling()
    }

    override fun onPause() {
        super.onPause()
        resumed = false
        polling = false
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdownNow()
    }

    // ---------------------------------------------------------------- UI states

    private fun refreshFromDisk() {
        val n = scan.photos.size
        info.text = "$n photos on this phone"
        when (scan.state) {
            "done" -> showDone(null)
            "queued", "running", "receiving" -> showWorking("Checking with the PC…", null)
            "error" -> showError(scan.meta().optString("message", "Processing failed"))
            else -> showLocal()
        }
    }

    private fun setActions(vararg buttons: View) {
        actions.removeAllViews()
        buttons.forEachIndexed { i, b -> actions.addView(b, lp(top = if (i == 0) 0 else dp(10))) }
    }

    private fun showLocal() {
        progress.visibility = View.GONE
        stateText.text = if (prefs.isConfigured) {
            "Ready to send to ${prefs.pcName.ifEmpty { prefs.host }}.\nKeep ScanStation running on the PC and the phone on the same Wi-Fi."
        } else {
            "Set up your PC connection first (Scan3D home screen → PC settings)."
        }
        setActions(
            button("Send to PC and make STL") { send() },
            button("Add more photos", C.MUTED) { addPhotos() },
            button("Delete scan", C.BAD) { confirmDelete() },
        )
    }

    private fun showWorking(msg: String, frac: Float?) {
        stateText.text = msg
        progress.visibility = View.VISIBLE
        progress.isIndeterminate = frac == null
        if (frac != null) progress.progress = (frac * 1000).toInt()
        if (actions.childCount != 0) actions.removeAllViews()
    }

    private fun showError(msg: String) {
        progress.visibility = View.GONE
        stateText.text = "⚠ $msg"
        setActions(
            button("Try again") { send() },
            button("Add more photos", C.MUTED) { addPhotos() },
            button("Delete scan", C.BAD) { confirmDelete() },
        )
    }

    private fun showDone(st: JSONObject?) {
        progress.visibility = View.GONE
        val rep = st?.optJSONObject("report") ?: scan.meta().optJSONObject("report")
        if (rep != null) {
            report.text = describe(rep)
            stateText.text = "✓ STL ready: ${scan.sizeText}"
        } else {
            stateText.text = "✓ STL ready"
        }
        if (scan.previewFile.exists()) {
            BitmapFactory.decodeFile(scan.previewFile.path)?.let {
                preview.setImageBitmap(it)
                preview.visibility = View.VISIBLE
            }
        }
        setActions(
            button("Save STL to phone (Downloads)", C.OK) { withStl { saveToDownloads() } },
            button("Share / send STL") { withStl { share() } },
            button("Process again on PC", C.MUTED) { send() },
            button("Delete scan", C.BAD) { confirmDelete() },
        )
    }

    private fun describe(r: JSONObject): String {
        val size = r.optJSONArray("size_mm")
        val sb = StringBuilder()
        if (size != null && size.length() == 3) {
            sb.append(String.format(Locale.UK, "Size: %.2f × %.2f × %.2f mm\n", size.getDouble(0), size.getDouble(1), size.getDouble(2)))
        }
        sb.append(String.format(Locale.UK, "Scale check: %.3f mm RMS over %d mat points\n", r.optDouble("scale_fit_rms_mm"), r.optInt("mat_points")))
        if (!r.isNull("surface_noise_mm")) {
            sb.append(String.format(Locale.UK, "Surface noise: %.2f mm\n", r.optDouble("surface_noise_mm")))
        }
        sb.append("Photos used: ${r.optInt("photos_aligned")} of ${r.optInt("photos")}\n")
        sb.append(if (r.optBoolean("watertight")) "Watertight: yes (ready to slice)\n" else "Watertight: no – check in your slicer\n")
        sb.append(String.format(Locale.UK, "Triangles: %,d  •  PC time: %.1f min\n", r.optInt("triangles"), r.optDouble("minutes")))
        sb.append("\nThe STL is also saved on the PC in ScanStation's scans folder.")
        return sb.toString()
    }

    // ---------------------------------------------------------------- actions

    private fun addPhotos() {
        startActivity(Intent(this, CaptureActivity::class.java).putExtra(CaptureActivity.EXTRA_SCAN, scan.name))
        finish()
    }

    private fun confirmDelete() {
        android.app.AlertDialog.Builder(this)
            .setTitle("Delete ${scan.name}?")
            .setMessage("Removes the photos and STL from this phone. Anything on the PC stays.")
            .setPositiveButton("Delete") { _, _ ->
                ScanStore.delete(scan)
                finish()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun friendly(e: Exception): String = when (e) {
        is PcClient.HttpError -> e.message ?: "The PC refused the request"
        is ConnectException, is NoRouteToHostException, is SocketTimeoutException ->
            "Can't reach the PC at ${prefs.host}. Is ScanStation running (start.bat) and is the phone on the same Wi-Fi?"
        is IOException -> "Network problem: ${e.message}"
        else -> "Error: ${e.message}"
    }

    private fun send() {
        val client = prefs.client()
        if (client == null) {
            toast("Set up the PC connection on the home screen first.")
            return
        }
        if (busy) return
        val photos = scan.photos
        if (photos.size < 15) {
            toast("Only ${photos.size} photos – take more first.")
            return
        }
        busy = true
        showWorking("Connecting to the PC…", null)
        io.execute {
            try {
                val pcInfo = client.info()
                if (!pcInfo.optBoolean("key_ok")) throw PcClient.HttpError(401, "Wrong pairing code – check the code in the ScanStation window.")
                val meta = JSONObject()
                    .put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
                    .put("android", Build.VERSION.RELEASE)
                    .put("photos", photos.size)
                val created = client.createScan(scan.name, scan.clientId, meta)
                val id = created.getString("id")
                scan.serverId = id
                photos.forEachIndexed { i, f ->
                    main.post { showWorking("Sending photo ${i + 1} of ${photos.size}…", i.toFloat() / photos.size) }
                    var attempt = 0
                    while (true) {
                        try {
                            client.uploadPhoto(id, f)
                            break
                        } catch (e: IOException) {
                            if (e is PcClient.HttpError || ++attempt >= 3) throw e
                            Thread.sleep(1500)
                        }
                    }
                }
                client.process(id)
                scan.state = "queued"
                main.post {
                    busy = false
                    showWorking("Queued on the PC…", 0f)
                    startPolling()
                }
            } catch (e: Exception) {
                val msg = friendly(e)
                main.post {
                    busy = false
                    showError(msg)
                }
            }
        }
    }

    private fun startPolling() {
        if (polling) return
        polling = true
        poll()
    }

    private fun poll() {
        if (!polling || !resumed) return
        val client = prefs.client() ?: return
        val id = scan.serverId
        io.execute {
            try {
                val st = client.status(id)
                val state = st.optString("state")
                if (state == "done") {
                    val rep = st.optJSONObject("report")
                    val size = rep?.optJSONArray("size_mm")
                    try {
                        client.download(id, "preview.png", scan.previewFile)
                    } catch (e: IOException) {
                        // preview is optional
                    }
                    scan.update {
                        it.put("state", "done")
                        if (rep != null) it.put("report", rep)
                        if (size != null && size.length() == 3) {
                            it.put(
                                "size_text",
                                String.format(Locale.UK, "%.1f × %.1f × %.1f mm", size.getDouble(0), size.getDouble(1), size.getDouble(2)),
                            )
                        }
                    }
                }
                main.post { onStatus(st) }
            } catch (e: Exception) {
                val msg = friendly(e)
                main.post {
                    if (e is PcClient.HttpError && e.code == 404) {
                        polling = false
                        scan.update { it.put("state", "local") }
                        showLocal()
                    } else {
                        stateText.text = "$msg\n(retrying…)"
                        main.postDelayed({ poll() }, 4000)
                    }
                }
            }
        }
    }

    private fun onStatus(st: JSONObject) {
        val state = st.optString("state")
        when (state) {
            "done" -> {
                polling = false
                showDone(st)
            }
            "error" -> {
                polling = false
                val msg = st.optString("message", "Processing failed")
                scan.update {
                    it.put("state", "error")
                    it.put("message", msg)
                }
                showError(msg)
            }
            "queued" -> {
                val pos = st.optInt("queue_position", 1)
                showWorking(if (pos > 1) "Waiting – $pos scans ahead on the PC" else "Queued on the PC…", 0f)
                main.postDelayed({ poll() }, 2000)
            }
            "running" -> {
                val stage = st.optString("stage_label", st.optString("message"))
                val p = st.optDouble("progress", 0.0)
                showWorking(String.format(Locale.UK, "%s\n%.0f%%", stage, p * 100), p.toFloat())
                main.postDelayed({ poll() }, 2000)
            }
            else -> {
                // "receiving": photos are there but processing hasn't started
                polling = false
                scan.update { it.put("state", "local") }
                showLocal()
            }
        }
    }

    /** Make sure the STL is on the phone, downloading it first if needed. */
    private fun withStl(then: () -> Unit) {
        if (scan.stlFile.exists() && scan.stlFile.length() > 0) {
            then()
            return
        }
        val client = prefs.client() ?: return toast("Set up the PC connection first.")
        showWorking("Downloading STL…", null)
        io.execute {
            try {
                client.download(scan.serverId, "model.stl", scan.stlFile)
                main.post {
                    showDone(null)
                    then()
                }
            } catch (e: Exception) {
                val msg = friendly(e)
                main.post {
                    showDone(null)
                    toast(msg)
                }
            }
        }
    }

    private fun saveToDownloads() {
        val src = scan.stlFile
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            share()
            return
        }
        try {
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, src.name)
                put(MediaStore.MediaColumns.MIME_TYPE, "application/octet-stream")
                put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Scan3D")
            }
            val uri = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IOException("Downloads folder unavailable")
            contentResolver.openOutputStream(uri)?.use { out -> src.inputStream().use { it.copyTo(out) } }
                ?: throw IOException("Could not write file")
            toast("Saved to Downloads/Scan3D/${src.name}")
        } catch (e: Exception) {
            toast("Couldn't save: ${e.message}")
        }
    }

    private fun share() {
        val uri = FileProvider.getUriForFile(this, "$packageName.files", scan.stlFile)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "application/octet-stream"
            putExtra(Intent.EXTRA_STREAM, uri)
            putExtra(Intent.EXTRA_SUBJECT, scan.stlFile.name)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        startActivity(Intent.createChooser(send, "Send ${scan.stlFile.name}"))
    }
}
