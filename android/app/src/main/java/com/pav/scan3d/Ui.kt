package com.pav.scan3d

import android.app.Activity
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import kotlin.math.roundToInt

/** Colours used across the app (dark theme). */
object C {
    const val BG = 0xFF0F151B.toInt()
    const val CARD = 0xFF1B242E.toInt()
    const val ACCENT = 0xFF3D8FE0.toInt()
    const val OK = 0xFF2FB872.toInt()
    const val WARN = 0xFFE59A2F.toInt()
    const val BAD = 0xFFD9534F.toInt()
    const val MUTED = 0xFF3A4652.toInt()
    const val TEXT = 0xFFECEFF4.toInt()
    const val SUB = 0xFF9AA7B4.toInt()
}

fun Context.dp(v: Number): Int = (v.toFloat() * resources.displayMetrics.density).roundToInt()

fun Context.label(s: String, size: Float = 16f, color: Int = C.TEXT, bold: Boolean = false): TextView =
    TextView(this).apply {
        text = s
        textSize = size
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
        setLineSpacing(0f, 1.15f)
    }

fun Context.rounded(color: Int, radiusDp: Int = 14): GradientDrawable =
    GradientDrawable().apply {
        cornerRadius = dp(radiusDp).toFloat()
        setColor(color)
    }

fun Context.button(text: String, color: Int = C.ACCENT, onClick: () -> Unit): Button =
    Button(this).apply {
        this.text = text
        isAllCaps = false
        textSize = 16f
        setTextColor(Color.WHITE)
        background = rounded(color, 12)
        stateListAnimator = null
        minHeight = dp(52)
        setPadding(dp(16), dp(10), dp(16), dp(10))
        setOnClickListener { onClick() }
    }

fun Context.column(paddingDp: Int = 0): LinearLayout =
    LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(paddingDp), dp(paddingDp), dp(paddingDp), dp(paddingDp))
    }

fun Context.row(): LinearLayout =
    LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
    }

/** LayoutParams helpers. */
fun lp(w: Int = ViewGroup.LayoutParams.MATCH_PARENT, h: Int = ViewGroup.LayoutParams.WRAP_CONTENT, top: Int = 0): LinearLayout.LayoutParams =
    LinearLayout.LayoutParams(w, h).apply { topMargin = top }

fun weighted(weight: Float = 1f, start: Int = 0): LinearLayout.LayoutParams =
    LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, weight).apply { marginStart = start }

/** Keep content clear of the status/navigation bars (Android 15 draws edge-to-edge). */
fun View.padForSystemBars() {
    val l = paddingLeft
    val t = paddingTop
    val r = paddingRight
    val b = paddingBottom
    ViewCompat.setOnApplyWindowInsetsListener(this) { v, insets ->
        val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
        v.setPadding(l + bars.left, t + bars.top, r + bars.right, b + bars.bottom)
        insets
    }
    ViewCompat.requestApplyInsets(this)
}

fun Activity.toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
