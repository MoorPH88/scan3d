package com.pav.scan3d

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {

    private lateinit var prefs: Prefs
    private lateinit var pcStatus: TextView
    private lateinit var list: LinearLayout
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val scroll = ScrollView(this).apply { setBackgroundColor(C.BG) }
        val col = column(16)
        scroll.addView(col)
        scroll.padForSystemBars()

        col.addView(label("Scan3D", 28f, bold = true))
        col.addView(label("Photograph an item on the mat → true-scale STL", 14f, C.SUB), lp(top = dp(2)))

        // PC connection card
        val card = column(14).apply { background = rounded(C.CARD) }
        card.addView(label("PC (ScanStation)", 13f, C.SUB))
        pcStatus = label("", 15f)
        card.addView(pcStatus, lp(top = dp(4)))
        val pcButtons = row()
        pcButtons.addView(button("Find PC", C.MUTED) { findPc() }, weighted(1f))
        pcButtons.addView(button("PC settings", C.MUTED) { settingsDialog() }, weighted(1f, dp(10)))
        card.addView(pcButtons, lp(top = dp(10)))
        col.addView(card, lp(top = dp(16)))

        col.addView(button("＋  New scan") { newScan() }.apply { textSize = 18f; minHeight = dp(64) }, lp(top = dp(16)))
        col.addView(button("How to get an accurate scan", C.MUTED) { tips(null) }, lp(top = dp(10)))

        col.addView(label("Your scans", 18f, bold = true), lp(top = dp(24)))
        list = column()
        col.addView(list, lp(top = dp(8)))
        setContentView(scroll)
    }

    override fun onResume() {
        super.onResume()
        refreshList()
        checkPc()
    }

    override fun onDestroy() {
        super.onDestroy()
        io.shutdownNow()
    }

    // ---------------------------------------------------------------- scans

    private fun newScan() {
        if (!prefs.tipsShown) {
            tips { prefs.tipsShown = true; newScan() }
            return
        }
        val scan = ScanStore.create(this)
        startActivity(Intent(this, CaptureActivity::class.java).putExtra(CaptureActivity.EXTRA_SCAN, scan.name))
    }

    private fun refreshList() {
        list.removeAllViews()
        val scans = ScanStore.all(this)
        if (scans.isEmpty()) {
            list.addView(label("No scans yet.", 14f, C.SUB))
            return
        }
        for (s in scans) {
            val n = s.photos.size
            if (n == 0 && s.state.isEmpty()) {
                // abandoned before the first photo
                ScanStore.delete(s)
                continue
            }
            val item = column(12).apply { background = rounded(C.CARD, 12) }
            item.addView(label(s.name, 16f, bold = true))
            val state = when (s.state) {
                "done" -> "✓ STL ready  •  ${s.sizeText}"
                "queued", "running" -> "Processing on the PC…"
                "error" -> "⚠ Failed – tap to see why / retry"
                else -> "Not processed yet – tap to send to the PC"
            }
            item.addView(label("$n photos  •  $state", 13f, if (s.state == "done") C.OK else C.SUB), lp(top = dp(2)))
            item.setOnClickListener {
                startActivity(Intent(this, ScanActivity::class.java).putExtra(ScanActivity.EXTRA_SCAN, s.name))
            }
            item.setOnLongClickListener {
                AlertDialog.Builder(this)
                    .setTitle("Delete ${s.name}?")
                    .setMessage("Removes the photos and STL from this phone.")
                    .setPositiveButton("Delete") { _, _ ->
                        ScanStore.delete(s)
                        refreshList()
                    }
                    .setNegativeButton("Cancel", null)
                    .show()
                true
            }
            list.addView(item, lp(top = dp(8)))
        }
    }

    // ---------------------------------------------------------------- PC connection

    private fun checkPc() {
        val client = prefs.client()
        if (client == null) {
            pcStatus.text = "Not set up. Start ScanStation on your PC (start.bat), then tap Find PC."
            pcStatus.setTextColor(C.WARN)
            return
        }
        pcStatus.text = "Checking ${prefs.host}…"
        pcStatus.setTextColor(C.SUB)
        io.execute {
            val (text, color) = try {
                val info = client.info()
                if (info.optBoolean("key_ok")) {
                    prefs.pcName = info.optString("name")
                    "Connected to ${info.optString("name")} ✓" to C.OK
                } else {
                    "Pairing code is wrong – tap PC settings" to C.WARN
                }
            } catch (e: Exception) {
                "Can't reach ${prefs.host}. Is ScanStation running and are you on the same Wi-Fi?" to C.WARN
            }
            main.post {
                pcStatus.text = text
                pcStatus.setTextColor(color)
            }
        }
    }

    private fun findPc() {
        pcStatus.text = "Looking for ScanStation on your Wi-Fi…"
        pcStatus.setTextColor(C.SUB)
        io.execute {
            val found = try {
                PcClient.discover(this)
            } catch (e: Exception) {
                emptyList()
            }
            main.post { onFound(found) }
        }
    }

    private fun onFound(found: List<PcClient.Found>) {
        when {
            found.isEmpty() -> {
                checkPc()
                AlertDialog.Builder(this)
                    .setTitle("No PC found")
                    .setMessage(
                        "Make sure start.bat is running on the PC, the phone is on the same Wi-Fi, and Windows " +
                            "allowed ScanStation on private networks.\n\nYou can also type the PC address shown in the ScanStation window."
                    )
                    .setPositiveButton("Enter address") { _, _ -> settingsDialog() }
                    .setNegativeButton("Close", null)
                    .show()
            }
            found.size == 1 -> usePc(found[0])
            else -> {
                AlertDialog.Builder(this)
                    .setTitle("Choose your PC")
                    .setItems(found.map { "${it.name}  (${it.host})" }.toTypedArray()) { _, i -> usePc(found[i]) }
                    .show()
            }
        }
    }

    private fun usePc(f: PcClient.Found) {
        val changed = prefs.host != f.host
        prefs.host = f.host
        prefs.port = f.port
        prefs.pcName = f.name
        if (changed || prefs.key.isEmpty()) askCode(f.name) else checkPc()
    }

    private fun askCode(pcName: String) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            hint = "6-digit code"
            setText(prefs.key)
            gravity = Gravity.CENTER
            textSize = 22f
        }
        val box = column(20).apply { addView(input) }
        AlertDialog.Builder(this)
            .setTitle("Pairing code")
            .setMessage("Type the pairing code shown in the ScanStation window on $pcName.")
            .setView(box)
            .setPositiveButton("OK") { _, _ ->
                prefs.key = input.text.toString()
                checkPc()
            }
            .setNegativeButton("Cancel") { _, _ -> checkPc() }
            .show()
    }

    private fun settingsDialog() {
        val addr = EditText(this).apply {
            hint = "PC address, e.g. 192.168.1.23"
            setText(if (prefs.host.isEmpty()) "" else "${prefs.host}:${prefs.port}")
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
        }
        val code = EditText(this).apply {
            hint = "Pairing code"
            setText(prefs.key)
            inputType = InputType.TYPE_CLASS_NUMBER
        }
        val box = column(20).apply {
            addView(label("Address shown in the ScanStation window", 13f, C.SUB))
            addView(addr)
            addView(label("Pairing code", 13f, C.SUB), lp(top = dp(12)))
            addView(code)
        }
        AlertDialog.Builder(this)
            .setTitle("PC settings")
            .setView(box)
            .setPositiveButton("Save") { _, _ ->
                val a = addr.text.toString().trim().removePrefix("http://").trimEnd('/')
                val host = a.substringBefore(":")
                val port = a.substringAfter(":", "8765").toIntOrNull() ?: 8765
                prefs.host = host
                prefs.port = port
                prefs.key = code.text.toString()
                checkPc()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    // ---------------------------------------------------------------- help

    private fun tips(after: (() -> Unit)?) {
        val text = """
            Before you start
            • Print the mat (Scan3D-mat-A4.pdf from the ScanStation folder) at 100% / Actual size and keep it flat – glue it to card.
            • Bright, even light. Avoid harsh shadows and direct sun.
            • Shiny, clear, very dark or completely plain items: dust them with matte scanning spray first (a 3D-scanning spray that vanishes, or a light coat of dry shampoo).

            Scanning
            • Put the item on the centre cross. Don't move the item or the mat until you've finished.
            • Hold the phone 25–40 cm away so the item fills about half the screen and some black mat squares are visible.
            • Tap the item to lock focus, then keep roughly that distance.
            • Walk slowly all the way round: once low (phone just above the table), once about 35° down, once looking well down. With Auto on, a photo is taken for each new angle when you hold still – fill the green rings.
            • 50–80 photos is ideal.

            Then tap Done → Send to PC. The STL appears on the PC and can be saved to the phone.
        """.trimIndent()
        val tv = label(text, 14f).apply { setPadding(dp(20), dp(8), dp(20), dp(8)) }
        val sc = ScrollView(this).apply { addView(tv) }
        AlertDialog.Builder(this)
            .setTitle("How to get an accurate scan")
            .setView(sc)
            .setPositiveButton(if (after != null) "Start scanning" else "OK") { _, _ -> after?.invoke() }
            .show()
    }
}
