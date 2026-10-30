package com.pav.scan3d

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/** App settings (which PC to talk to). */
class Prefs(ctx: Context) {
    private val sp = ctx.getSharedPreferences("scan3d", Context.MODE_PRIVATE)

    var host: String
        get() = sp.getString("host", "") ?: ""
        set(v) = sp.edit().putString("host", v.trim()).apply()

    var port: Int
        get() = sp.getInt("port", 8765)
        set(v) = sp.edit().putInt("port", v).apply()

    var key: String
        get() = sp.getString("key", "") ?: ""
        set(v) = sp.edit().putString("key", v.trim()).apply()

    var pcName: String
        get() = sp.getString("pc_name", "") ?: ""
        set(v) = sp.edit().putString("pc_name", v).apply()

    var autoCapture: Boolean
        get() = sp.getBoolean("auto", true)
        set(v) = sp.edit().putBoolean("auto", v).apply()

    var tipsShown: Boolean
        get() = sp.getBoolean("tips_shown", false)
        set(v) = sp.edit().putBoolean("tips_shown", v).apply()

    val isConfigured: Boolean get() = host.isNotEmpty() && key.isNotEmpty()

    fun client(): PcClient? = if (isConfigured) PcClient("http://$host:$port", key) else null
}

/** One scan = a folder of photos on the phone plus a small JSON file. */
class LocalScan(val dir: File) {
    val name: String get() = dir.name
    private val metaFile get() = File(dir, "scan.json")

    val photos: List<File>
        get() = dir.listFiles { f -> f.isFile && f.name.endsWith(".jpg") }?.sortedBy { it.name } ?: emptyList()

    fun meta(): JSONObject =
        try {
            JSONObject(metaFile.readText())
        } catch (e: Exception) {
            JSONObject()
        }

    fun update(block: (JSONObject) -> Unit) {
        val m = meta()
        block(m)
        metaFile.writeText(m.toString(2))
    }

    val clientId: String
        get() {
            val id = meta().optString("client_id")
            if (id.isNotEmpty()) return id
            val fresh = UUID.randomUUID().toString()
            update { it.put("client_id", fresh) }
            return fresh
        }

    var serverId: String
        get() = meta().optString("server_id")
        set(v) = update { it.put("server_id", v) }

    /** Last state the PC reported: receiving / queued / running / done / error. */
    var state: String
        get() = meta().optString("state")
        set(v) = update { it.put("state", v) }

    var sizeText: String
        get() = meta().optString("size_text")
        set(v) = update { it.put("size_text", v) }

    val stlFile: File get() = File(dir, "$name.stl")
    val previewFile: File get() = File(dir, "preview.png")

    fun nextPhotoFile(): File {
        val last = photos.lastOrNull()?.name?.removePrefix("IMG_")?.removeSuffix(".jpg")?.toIntOrNull() ?: 0
        return File(dir, String.format(Locale.US, "IMG_%04d.jpg", last + 1))
    }
}

object ScanStore {
    fun root(ctx: Context): File =
        File(ctx.getExternalFilesDir(null) ?: ctx.filesDir, "scans").apply { mkdirs() }

    fun all(ctx: Context): List<LocalScan> =
        root(ctx).listFiles()?.filter { it.isDirectory }?.sortedByDescending { it.lastModified() }?.map { LocalScan(it) }
            ?: emptyList()

    fun get(ctx: Context, name: String): LocalScan? =
        File(root(ctx), name).takeIf { it.isDirectory }?.let { LocalScan(it) }

    fun create(ctx: Context): LocalScan {
        val name = "scan_" + SimpleDateFormat("yyyyMMdd_HHmmss", Locale.UK).format(Date())
        val dir = File(root(ctx), name).apply { mkdirs() }
        val scan = LocalScan(dir)
        scan.update {
            it.put("created", System.currentTimeMillis())
            it.put("client_id", UUID.randomUUID().toString())
        }
        return scan
    }

    fun delete(scan: LocalScan) {
        scan.dir.deleteRecursively()
    }
}
