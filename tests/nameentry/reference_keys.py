"""Original ARM32 instruction oracle for every key in the owner's APK.
Analysis only. This code and Unicorn are never linked into the Android app.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/validation/arm32_ref'))
from armref import ArmRef

so=ROOT/'work/apk_unzip/lib/armeabi-v7a/libsnailmail.so'
ref=ArmRef(str(so))
archive=(ROOT/'work/apk_unzip/assets/asm.mp3').read_bytes()
for i in range(struct.unpack_from('<I',archive)[0]):
    no,do,ds,ss,codec,dims=struct.unpack_from('<6I',archive,4+24*i)
    if archive[no:archive.index(b'\0',no)]==b'KEYPAD/KEYPADMASK.TXT':
        mask=archive[do:do+ds].decode();break
rows=[]
for line in mask.splitlines():
    m=re.fullmatch(r'(.) - (\d+) (\d+) (\d+) (\d+)',line)
    if m:
        code,x0,y0,x1,y1=m.groups()
        rows.append((code,*map(int,(x0,y0,x1,y1))))
letters=dict(zip('abcdefghijklmnopqrstuvwxyz',
    [0x1e,0x30,0x2e,0x20,0x12,0x21,0x22,0x23,0x17,0x24,0x25,0x26,0x32,
     0x31,0x18,0x19,0x10,0x13,0x1f,0x14,0x16,0x2f,0x11,0x2d,0x15,0x2c]))
expected={**letters,**{str(i):i+1 for i in range(1,10)},'0':11,' ':57,'@':14,'#':28,'$':0,'!':0}
pad=ref.alloc(0x124)
table=ref.alloc(len(rows)*20)
ref.write_u32(pad+4,table);ref.write_u32(pad+8,len(rows))
result=[]
for i,(code,x0,y0,x1,y1) in enumerate(rows):
    # Original Open converts the source mask to a legacy 640x480 grid.
    rect=(int(x0/480*640),int(96+y0/256*384),int(x1/480*640),int(96+y1/256*384))
    ref.write(table+20*i,struct.pack('<4iB3x',*rect,ord(code)))
out=ref.alloc(4)
for i,(code,*_) in enumerate(rows):
    rect=struct.unpack('<4i',ref.read(table+20*i,16))
    got=ref.call('_ZN7cKeyPad7KeyTestEiiPi',pad,(rect[0]+rect[2])//2,(rect[1]+rect[3])//2,out)
    assert got==ord(code) and ref.read_u32(out)==i
    scan=ref.call('_ZN7cKeyPad11ConvertCodeEc',pad,ord(code))
    assert scan==expected[code]
    result.append({'code':code,'index':i,'original_scancode':scan})
assert ref.call('_ZN7cKeyPad11ConvertCodeEc',pad,ord('!'))==0
assert ref.read(pad+0x120,1)==b'\x01'
report={'binary_sha256':hashlib.sha256(so.read_bytes()).hexdigest(),
        'reference':'original ARM32 instructions in Unicorn (analysis only)',
        'keys':result,'mismatches':0,
        'notes':'$ is a name-field rectangle, not a character key. ! is an original shift action absent from the visible asset.'}
dest=ROOT/'work/engineering/nameentry/reference-keys.json'
dest.write_text(json.dumps(report,indent=2)+'\n')
print(f'Original ARM32: {len(result)} asset records and shift flag verified, 0 mismatches')
