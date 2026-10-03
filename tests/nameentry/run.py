#!/usr/bin/env python3
"""Actual AOT game lifecycle and every asset key. Runs on the Linux renderer.
No gameplay fixtures are shipped: profile/lose exist only in host/main.c.
Outputs original screenshots, live coordinate snapshots, logs and a report.
"""
import ctypes as C
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'work/engineering/nameentry/validation'
OUT.mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)
subprocess.run(['cc', '-shared', '-fPIC', '-std=c11', '-Wall', '-Wextra', '-Werror',
                'aot/port/nameentry_layout.c', '-lm', '-o', str(OUT / 'layout.so')], check=True)
class Key(C.Structure):
    _fields_ = [(x, C.c_float) for x in ('x0','y0','x1','y1')] + [('code', C.c_ubyte)]
class Canvas(C.Structure):
    _fields_ = [(x, C.c_float) for x in ('scale','x','y')]
lib = C.CDLL(str(OUT / 'layout.so'))
lib.sm_name_parse.argtypes = [C.c_char_p, C.POINTER(Key), C.c_int]
lib.sm_name_fit.argtypes = [C.c_float]*6
lib.sm_name_fit.restype = Canvas
lib.sm_name_scene_fit.argtypes = [C.c_float]*6
lib.sm_name_scene_fit.restype = Canvas
lib.sm_name_hit.argtypes = [C.POINTER(Key), C.c_int, C.c_float, C.c_float]
lib.sm_name_next.argtypes = [C.POINTER(Key), C.c_int, C.c_int, C.c_int, C.c_int]
archive = (ROOT / 'work/apk_unzip/assets/asm.mp3').read_bytes()
mask = None
for i in range(struct.unpack_from('<I', archive)[0]):
    no, do, ds, ss, codec, dims = struct.unpack_from('<6I', archive, 4+24*i)
    if archive[no:archive.index(b'\0', no)] == b'KEYPAD/KEYPADMASK.TXT':
        assert codec == 0
        mask = archive[do:do+ds]
        break
assert mask
keys = (Key * 64)()
n = lib.sm_name_parse(mask, keys, 64)
assert n == 40
by_code = {chr(k.code): i for i,k in enumerate(keys[:n])}
assert set(by_code) == set('$1234567890qwertyuiopasdfghjklzxcvbnm@ #')
assert lib.sm_name_parse(b'a - 1 1 999 4\n', keys, 64) == -1
n = lib.sm_name_parse(mask, keys, 64)
checks = 0
for w,h in [(640,480),(800,480),(1920,1080),(2160,1080),(2340,1080),
             (2400,1080),(3200,1080),(1080,2400),(1280,720)]:
    for l,t,r,b in [(0,0,0,0),(96,24,32,20)]:
        cv = lib.sm_name_scene_fit(w,h,l,t,r,b)
        assert cv.x >= l-0.001 and cv.y >= t-0.001
        assert cv.x+480*cv.scale <= w-r+0.001
        assert cv.y+320*cv.scale <= h-b+0.001
        if w/h >= 16/9:
            assert cv.y >= h*64/320-0.001
            assert cv.y+320*cv.scale <= h*300/320+0.001
        for i,k in enumerate(keys[:n]):
            x,y = (k.x0+k.x1)/2, (k.y0+k.y1)/2
            px,py = cv.x+x*cv.scale,cv.y+y*cv.scale
            assert lib.sm_name_hit(keys,n,(px-cv.x)/cv.scale,(py-cv.y)/cv.scale) == (-1 if k.code==ord('$') else i)
            checks += 1
        assert lib.sm_name_hit(keys,n,-1,200) == -1
        assert lib.sm_name_hit(keys,n,480,200) == -1
        # Visible gaps remain gaps; padding cannot activate a neighbouring key.
        assert lib.sm_name_hit(keys,n,59,145) == -1
for code in '123456789qwertyuioasdfghjklzxcvbnm':
    i=by_code[code]
    nxt=lib.sm_name_next(keys,n,i,1,0)
    assert nxt >= 0 and keys[nxt].y0 == keys[i].y0
assert chr(keys[lib.sm_name_next(keys,n,by_code['1'],0,1)].code)=='q'
assert chr(keys[lib.sm_name_next(keys,n,by_code['a'],0,-1)].code)=='w'
assert chr(keys[lib.sm_name_next(keys,n,by_code['m'],0,1)].code)=='#'
assert lib.sm_name_next(keys,n,by_code['0'],1,0)==by_code['0']

runner = ROOT / 'build-host-controls/aot/snailmail_host'
cases = [(1920,1080,0,0),(2160,1080,0,0),(2340,1080,0,0),
         (2400,1080,0,0),(3200,1080,96,32),(3120,1440,0,0)]
results=[]
for w,h,left,right in cases:
    directory=OUT/f'{w}x{h}'
    directory.mkdir(exist_ok=True)
    cv=lib.sm_name_scene_fit(w,h,left,0,right,0)
    s=min(w/640,h/480); ox=(w-640*s)/2; oy=(h-480*s)/2
    lines=[]
    expected={}
    def tap(frame,x,y):
        lines.extend([f'{frame} down {x:.5f} {y:.5f}',f'{frame+1} up {x:.5f} {y:.5f}'])
    def oldtap(frame,x,y): tap(frame,ox+(x-80)*s,oy+y*s)
    def keytap(frame,code):
        k=keys[by_code[code]]
        tap(frame,cv.x+(k.x0+k.x1)/2*cv.scale,cv.y+(k.y0+k.y1)/2*cv.scale)
    def inspect(frame,name):
        lines.append(f'{frame} inspect'); expected[frame]=name
    lines.append('6000 profile')
    oldtap(6010,430,140); oldtap(6310,430,175); oldtap(6460,400,220)
    oldtap(6530,340,330); oldtap(6600,400,220)
    lines.extend(['6650 shot','6800 lose',f'7200 insets {left} 0 {right}','7250 shot'])
    f=7300
    for code in '1234567890qwertyuiopasdfghjklzxcvbnm ':
        keytap(f,code); inspect(f+12,code.upper())
        keytap(f+20,'@'); inspect(f+32,'')
        f+=40
    # Four directions traverse visible staggered rows; controller A activates
    # K after @ -> L -> K -> O -> K. Hardware delete uses the original editor.
    for key in (19,21,19,20,22,21):
        lines.append(f'{f} key {key}'); f+=10
    lines.append(f'{f} key 96');inspect(f+5,'K');f+=20
    lines.append(f'{f} key 67');inspect(f+5,'');f+=20
    for code in 'abcdefghijklmnopqrstuvwxyz0123456789':
        android=29+ord(code)-ord('a') if code.isalpha() else 7+int(code)
        lines.append(f'{f} key {android}');inspect(f+5,code.upper());f+=10
        lines.append(f'{f} key 67');inspect(f+5,'');f+=10
    # Original name editor capitalizes the first character only.
    name=''
    for code in 'snailmail9':
        keytap(f,code); name+=code.upper() if not name else code
        inspect(f+12,name); f+=30
    keytap(f,'@'); name=name[:-1];inspect(f+12,name);f+=30
    keytap(f,'9');name+='9';inspect(f+12,name);f+=30
    lines.append(f'{f} shot')
    if w == 2400: lines.append(f'{f+10} key 66')
    else: keytap(f+10,'#')
    lines.extend([f'{f+100} inspect',f'{f+100} shot'])
    # Submit is the original high-score action after Return closes the pad.
    tap(f+120,cv.x+282*cv.scale,cv.y+301*cv.scale)
    lines.extend([f'{f+250} inspect',f'{f+250} shot'])
    script=directory/'input.txt';script.write_text('\n'.join(lines)+'\n')
    env=dict(os.environ,LIBGL_ALWAYS_SOFTWARE='1',LP_NUM_THREADS='4')
    logpath=directory/'game.log'
    with logpath.open('w') as log:
        subprocess.run([str(runner),'--port-defaults','--size',f'{w}x{h}',
                        '--frames',str(f+300),'--input',str(script),'--out',str(directory)],
                       stdout=log,stderr=log,env=env,check=True)
    log=logpath.read_text()
    observed={int(frame):(int(screen),int(pad),text) for frame,screen,pad,text in
              re.findall(r"\[test\] frame=(\d+) screen=(\d+) keypad=(\d+) name='([^']*)'",log)}
    for frame,want in expected.items():
        screen,pad,text=observed[frame]
        got=text[:-1] if text and text[-1] in '| ' else text
        assert got == want, (w,h,frame,want,text)
        assert pad == 2
    assert observed[f+100][1] == 0, ('Return did not close keyboard',observed[f+100])
    assert observed[f+250][0] == 0, ('Submit did not return to selector',observed[f+250])
    live=(directory/'layout_07251.txt').read_text()
    assert 'count=40' in live
    results.append({'size':[w,h],'insets':[left,0,right,0],'every_character_and_delete':True,
                    'hardware_letters_numbers_delete':True,'four_directions_and_controller_activation':True,
                    'confirm_input':'hardware Enter' if w == 2400 else 'touch Return',
                    'name':name,'return_pad_state':observed[f+100][1],
                    'after_submit_screen':observed[f+250][0],'log':str(logpath.relative_to(ROOT))})
report={'source_mask_keys':n,'transform_round_trips':checks,'cases':results,
        'runtime':'AOT native host with GLES1-on-GLES2 Mesa software rendering',
        'android_device_test':'NOT RUN by this host suite; separate Android acceptance is required',
        'loss_fixture':'tutorial-completed profile, zero extra lives and qualifying score; original Kill/death/score/menu code handles transitions'}
(OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
