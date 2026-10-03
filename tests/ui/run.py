#!/usr/bin/env python3
"""Replay all ten original galaxy panels; check measured renderer geometry.

Runs on Linux/Mesa, never modifies Android player saves. The host catalogue
fixture selects unlocked routes only inside its temporary test profile.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'work/engineering/ui-release'
OUT.mkdir(parents=True, exist_ok=True)
RUNNER = ROOT / 'build-host-controls/aot/snailmail_host'
SIZES = [(1280,720),(1440,720),(1560,720),(1600,720),(1920,720),(3120,1440)]

def run(size):
    w,h=size
    d=OUT/f'{w}x{h}'
    d.mkdir(exist_ok=True)
    s=min(w/640,h/480);ox=(w-640*s)/2;oy=(h-480*s)/2
    lines=['6000 profile']
    for frame,x,y in [(6010,350,140),(6310,350,175)]:
        px,py=ox+x*s,oy+y*s
        lines += [f'{frame} down {px} {py}',f'{frame+1} up {px} {py}']
    lines += ['6390 move 0 0']
    shots=[]
    for i in range(10):
        frame=6400+120*i
        lines += [f'{frame} mapinfo {1+5*i}',f'{frame+80} shot']
        shots.append(frame+81)
    # Exercise the actual moved Back button and verify the next screen.
    lines += ['7600 mapoff',f'7620 down {60*s} {oy+440*s}',
              f'7621 up {60*s} {oy+440*s}',f'7700 down {60*s} {oy+440*s}',
              f'7701 up {60*s} {oy+440*s}','7750 inspect','7750 shot']
    p=d/'input.txt';p.write_text('\n'.join(lines)+'\n')
    env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1',LP_NUM_THREADS='2')
    with (d/'game.log').open('w') as log:
        subprocess.run([str(RUNNER),'--port-defaults','--size',f'{w}x{h}',
                        '--frames','7752','--input',str(p),'--out',str(d)],
                       cwd=ROOT,env=env,stdout=log,stderr=log,check=True)
    log=(d/'game.log').read_text()
    selections=[int(x) for x in re.findall(r'galaxy shot selection=(\d+)',log)]
    assert selections[:10]==[1+5*i for i in range(10)],(size,selections)
    assert '[test] frame=7750 screen=0' in log,(size,'Back failed')
    panels=[]
    for shot in shots:
        widgets=[list(map(float,line.split())) for line in (d/f'ui_{shot:05}.txt').read_text().splitlines()]
        popup=[row for row in widgets if row[0]==1]
        assert len(popup)==1,(size,shot,'description missing or split',popup)
        _,x0,y0,x1,y1,px0,py0,px1,py1=popup[0]
        assert abs((px0+px1)/2-w/2)<0.1,(size,shot,'horizontal centre')
        assert abs((py0+py1)/2-h/2)<0.1,(size,shot,'vertical centre')
        assert abs((px1-px0)/(x1-x0)-(py1-py0)/(y1-y0))<0.001,(size,shot,'nonuniform scaling')
        assert py0>=60*s-0.1 and py1<=420*s+0.1,(size,shot,'chrome overlap',popup)
        for row in widgets:
            assert row[5]>=-0.1 and row[6]>=-0.1 and row[7]<=w+0.1 and row[8]<=h+0.1,(size,shot,'clipped widget',row)
        panels.append({'route':1+5*len(panels),'bounds':popup[0][5:]})
    return {'resolution':size,'panels':panels,'back_touch_passed':True}

with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
    cases=list(pool.map(run,SIZES))
report={'runtime':'Actual AOT game, Mesa software renderer','cases':cases,
        'physical_phone_test':False}
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS: 60 galaxy descriptions across six sizes; centred, uniform, unclipped; Back touch passed')
