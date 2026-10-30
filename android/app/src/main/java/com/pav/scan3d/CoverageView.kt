package com.pav.scan3d

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.View
import kotlin.math.min

/**
 * Shows which viewing angles have been photographed: three rings (low / middle /
 * high camera height) split into sectors around the item, plus the top view.
 */
class CoverageView(ctx: Context) : View(ctx) {

    data class Slot(val ring: Int, val sector: Int)

    companion object {
        val RING_NAMES = arrayOf("Low", "Middle", "High", "Top")
        val SECTORS = intArrayOf(24, 24, 12, 4)
        /** Camera tilt below horizontal (degrees) where each ring starts; ring i spans [LIMITS[i], LIMITS[i+1]). */
        val LIMITS = floatArrayOf(3f, 25f, 45f, 72f, 91f)

        fun ringFor(downDeg: Float): Int {
            for (i in 0 until 4) if (downDeg >= LIMITS[i] && downDeg < LIMITS[i + 1]) return i
            return -1
        }
    }

    private val filled = Array(4) { BooleanArray(SECTORS[it]) }
    var current: Slot? = null
        set(v) {
            field = v
            invalidate()
        }

    fun slotFor(downDeg: Float, headingDeg: Float): Slot? {
        val ring = ringFor(downDeg)
        if (ring < 0) return null
        val n = SECTORS[ring]
        val sector = (((headingDeg % 360f) + 360f) % 360f / (360f / n)).toInt().coerceIn(0, n - 1)
        return Slot(ring, sector)
    }

    fun isFilled(s: Slot) = filled[s.ring][s.sector]

    fun mark(s: Slot) {
        filled[s.ring][s.sector] = true
        invalidate()
    }

    fun count(ring: Int) = filled[ring].count { it }

    /** Rings 0-2 matter most; the top view is a bonus. */
    fun mainProgress(): Float {
        var have = 0
        var total = 0
        for (r in 0..2) {
            have += count(r)
            total += SECTORS[r]
        }
        return have.toFloat() / total
    }

    private val bg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(150, 0, 0, 0) }
    private val arc = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.BUTT
    }
    private val outline = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        color = Color.WHITE
    }
    private val rect = RectF()

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val size = min(width, height).toFloat()
        val cx = width / 2f
        val cy = height / 2f
        canvas.drawCircle(cx, cy, size / 2f, bg)
        val ringW = size * 0.11f
        arc.strokeWidth = ringW
        outline.strokeWidth = size * 0.02f
        val cur = current
        for (r in 0..2) {
            val radius = size / 2f - ringW * (0.75f + r * 1.25f)
            rect.set(cx - radius, cy - radius, cx + radius, cy + radius)
            val n = SECTORS[r]
            val step = 360f / n
            for (s in 0 until n) {
                arc.color = if (filled[r][s]) C.OK else C.MUTED
                // heading 0 drawn at the top, clockwise
                canvas.drawArc(rect, -90f + s * step + 1.5f, step - 3f, false, arc)
                if (cur != null && cur.ring == r && cur.sector == s) {
                    outline.color = Color.WHITE
                    arc.color = Color.WHITE
                    arc.alpha = 140
                    canvas.drawArc(rect, -90f + s * step + 1.5f, step - 3f, false, arc)
                    arc.alpha = 255
                }
            }
        }
        // top view: centre dot, filled when any top shot exists
        val topR = size * 0.07f
        arc.style = Paint.Style.FILL
        arc.color = if (count(3) > 0) C.OK else C.MUTED
        canvas.drawCircle(cx, cy, topR, arc)
        if (cur != null && cur.ring == 3) {
            canvas.drawCircle(cx, cy, topR + outline.strokeWidth, outline)
        }
        arc.style = Paint.Style.STROKE
    }
}
