"""Check complete native-renderer captures (requires Pillow on the host).

Reject empty sidebars/diagonal holes and verify the highlighted '9' is inside
its live transformed hitbox. Human inspection still checks composition/art.
"""
import json
from pathlib import Path
from PIL import Image

root = Path(__file__).resolve().parents[2]
out = root / 'work/engineering/nameentry/validation'
report = json.loads((out / 'report.json').read_text())
results = []
for case in report['cases']:
    w, h = case['size']
    folder = out / f'{w}x{h}'
    image = Image.open(folder / 'frame_09961.png').convert('RGB')
    assert image.size == (w, h)
    pixels = image.load()
    longest = 0
    for fraction in (.2, .35, .5, .75, .85):
        run = 0
        y = int(h * fraction)
        # Authored horizontal outlines can be long, but are thin. A missing
        # backdrop band stays black across this vertical neighbourhood.
        ys = (y - int(h * .012), y, y + int(h * .012))
        for x in range(w):
            run = run + 1 if all(sum(pixels[x, yy]) < 12 for yy in ys) else 0
            longest = max(longest, run)
    assert longest < w * .03, (w, h, 'unfilled background', longest)
    line = next(line for line in (folder / 'layout_09961.txt').read_text().splitlines()
                if line.startswith('9 ref '))
    x0, y0, x1, y1 = map(float, line.split(' pixel ')[1].split())
    # Glow stays around its visible key, including its small outer halo.
    halo = max(2, round((x1 - x0) * .12))
    bright = sum(min(pixels[x, y]) > 170
                 for y in range(max(0, int(y0)-halo), min(h, int(y1)+halo))
                 for x in range(max(0, int(x0)-halo), min(w, int(x1)+halo)))
    assert bright > 100, (w, h, 'selection/hitbox misalignment', bright)
    results.append({'size': [w, h], 'longest_dark_run': longest,
                    'selected_key_glow_pixels': bright})
(out / 'capture-report.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
