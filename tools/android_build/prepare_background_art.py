#!/usr/bin/env python3
"""Extract two original RGB layers locally; never publish original input art."""
import argparse
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--archive',type=Path,default=ROOT/'work/apk_unzip/assets/asm.mp3')
p.add_argument('--output',type=Path,default=ROOT/'android/app/src/main/assets/port')
args=p.parse_args()
data=args.archive.read_bytes()
wanted={b'MENUSCREENHORIZ.PNG':'menu-original.png',b'LOADING.PNG':'loading-original.png'}
found={}
count=struct.unpack_from('<I',data)[0]
for i in range(count):
    no,do,decoded,stored,codec,dims=struct.unpack_from('<6I',data,4+24*i)
    name=data[no:data.index(b'\0',no)].upper().split(b'/')[-1]
    if name in wanted:
        if codec!=3 or not data[do:do+8].startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError(f'Unexpected encoding for {name!r}')
        found[wanted[name]]=data[do:do+stored]
if len(found)!=len(wanted): raise ValueError('Original menu/loading artwork missing')
args.output.mkdir(parents=True,exist_ok=True)
for name,pixels in found.items():
    target=args.output/name
    if not target.exists() or target.read_bytes()!=pixels: target.write_bytes(pixels)
print('Original menu/loading RGB layers extracted locally')
