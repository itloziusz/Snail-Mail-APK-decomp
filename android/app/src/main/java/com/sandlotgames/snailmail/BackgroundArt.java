package com.sandlotgames.snailmail;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.util.Log;
import java.io.InputStream;

/** Optional widescreen artwork. Original archive remains the fallback. */
final class BackgroundArt {
    private static native void nativeAsset(int slot, int width, int height, int[] argb);
    static native void nativeContextCreated();

    static void load(Context context) {
        // Original colours must precede their generated alpha mattes.
        String[] names = {"sandlot-wide.png", "alpha72-wide.png", "menu-original.png",
                "menu-frame-mask.png", "menu-background.png", "loading-original.png", "loading-mask.png", "galaxy-map-wide.png"};
        int[] slots = {0, 1, 4, 2, 3, 6, 5, 7};
        for (int i = 0; i < names.length; i++) {
            try (InputStream stream = context.getAssets().open("port/" + names[i])) {
                Bitmap bitmap = BitmapFactory.decodeStream(stream);
                if (bitmap == null) throw new java.io.IOException("Invalid background bitmap");
                int w = bitmap.getWidth(), h = bitmap.getHeight();
                int[] pixels = new int[w * h];
                bitmap.getPixels(pixels, 0, w, 0, 0, w, h);
                nativeAsset(slots[i], w, h, pixels);
                bitmap.recycle();
            } catch (java.io.IOException e) {
                Log.w("SnailMail", "Widescreen asset missing: " + names[i], e);
            }
        }
    }
}
