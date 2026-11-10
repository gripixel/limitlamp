"""Render the panel headlessly to PNG:  python tests/preview.py out.png [--compact] [--scale 2] [--wallpaper] [--nodata]"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from PIL import Image
from limitlamp import model, render, store

def main(a):
    out = a[0]
    compact = "--compact" in a
    scale = float(a[a.index("--scale") + 1]) if "--scale" in a else 2.0
    opacity = int(a[a.index("--opacity") + 1]) if "--opacity" in a else 88
    if "--nodata" in a:
        view = model.build_view(None, [], False, "", store.now())
    else:
        view = model.build_view(model.demo_snapshot(), model.demo_history(), True, "", store.now())
    t = time.perf_counter()
    img, hits = render.render(view, opacity, compact, scale)
    dt = (time.perf_counter() - t) * 1000
    if "--wallpaper" in a:
        pad = int(36 * scale)
        bg = render.paint_wallpaper(img.width + 2 * pad, img.height + 2 * pad, scale)
        bg.alpha_composite(img, (pad, pad))
        img = bg
    img.save(out)
    print("render %.0f ms  size=%s  hits=%s" % (dt, img.size, sorted(hits)))

main(sys.argv[1:])
