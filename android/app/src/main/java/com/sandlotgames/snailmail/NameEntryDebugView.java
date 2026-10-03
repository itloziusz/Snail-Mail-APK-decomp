package com.sandlotgames.snailmail;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.view.View;
import java.util.Locale;

/** Development only; launch with --ez nameentryDebug true. Never receives
 * touch events. Rectangles are the game's live layout, not another copy. */
final class NameEntryDebugView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private static native float[] nativeSnapshot();
    NameEntryDebugView(Context context) { super(context); setFocusable(false); setClickable(false); }
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float[] d = nativeSnapshot();
        if (d.length >= 2) {
            int count = (int)d[0], selected = (int)d[1];
            paint.setStrokeWidth(2); paint.setTextSize(16);
            for (int i = 0; i < count && 2 + (i + 1) * 9 <= d.length; ++i) {
                int j = 2 + i * 9;
                paint.setStyle(Paint.Style.STROKE);
                paint.setColor(i == selected ? Color.YELLOW : d[j] == '$' ? Color.GRAY : Color.CYAN);
                canvas.drawRect(d[j+5], d[j+6], d[j+7], d[j+8], paint);
                paint.setStyle(Paint.Style.FILL);
                canvas.drawText(i + ":" + (char)d[j], d[j+5]+3, d[j+6]+16, paint);
                if (i == selected) {
                    paint.setColor(Color.WHITE);
                    canvas.drawText(String.format(Locale.US,
                        "key %d/%c | ref %.1f,%.1f..%.1f,%.1f | pixel %.1f,%.1f..%.1f,%.1f",
                        i, (char)d[j], d[j+1], d[j+2], d[j+3], d[j+4], d[j+5], d[j+6], d[j+7], d[j+8]),
                        12, getHeight()-20, paint);
                }
            }
        }
        postInvalidateDelayed(100);
    }
}
