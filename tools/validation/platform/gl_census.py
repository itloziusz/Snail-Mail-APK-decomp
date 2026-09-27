#!/usr/bin/env python3
"""OpenGL ES 1.x call-site census with constant-argument recovery.

Writes analysis/native/gl_usage.json (v7a primary; v5 call counts for
cross-check).

For every direct call (BL/B) to a gl* PLT stub, the per-function constant
propagation in armelf.ConstProp gives the abstract value of r0-r3 and of the
outgoing stack slots [sp,#0..] immediately before the call. Under the
ARM EABI base (softfp) procedure-call standard used by both builds
(Tag_ABI_VFP_args absent), GLfloat arguments travel in core registers / stack
words as raw IEEE-754 bit patterns; constant float bit patterns are decoded.

Where an argument is an unchanged incoming argument of the calling function
(e.g. G0SetBlend(int) -> glBlendFunc), the direct callers of that function
are analysed one level up and their constant values reported under
`via_callers`. Nothing is guessed: non-constant values are reported as
`unresolved`, `global_load` (loaded from a writable global at a known
address), or `caller_arg`.

Usage: gl_census.py [--v7a PATH] [--v5 PATH] [--out analysis/native/gl_usage.json]
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from armelf import ArmElf, ConstProp, f32, fmt_value, is_const  # noqa: E402
from capstone import arm as A  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEFAULT_V7A = os.path.join(REPO, "work/apk_unzip/lib/armeabi-v7a/libsnailmail.so")
DEFAULT_V5 = os.path.join(REPO, "work/apk_unzip/lib/armeabi/libsnailmail.so")

# ---------------------------------------------------------------- GL enums
E = {}


def _grp(name, table):
    E[name] = table


_grp("cap", {0x0DE1: "GL_TEXTURE_2D", 0x0BE2: "GL_BLEND", 0x0B71: "GL_DEPTH_TEST", 0x0B44: "GL_CULL_FACE",
             0x0B60: "GL_FOG", 0x0C11: "GL_SCISSOR_TEST", 0x0BC0: "GL_ALPHA_TEST", 0x0B50: "GL_LIGHTING",
             0x4000: "GL_LIGHT0", 0x0B57: "GL_COLOR_MATERIAL", 0x0BA1: "GL_NORMALIZE", 0x803A: "GL_RESCALE_NORMAL",
             0x0BD0: "GL_DITHER", 0x0B90: "GL_STENCIL_TEST", 0x8037: "GL_POLYGON_OFFSET_FILL",
             0x809D: "GL_MULTISAMPLE", 0x0B10: "GL_POINT_SMOOTH", 0x0B20: "GL_LINE_SMOOTH",
             0x0BF2: "GL_COLOR_LOGIC_OP", 0x809E: "GL_SAMPLE_ALPHA_TO_COVERAGE", 0x3000: "GL_CLIP_PLANE0"})
_grp("client_state", {0x8074: "GL_VERTEX_ARRAY", 0x8075: "GL_NORMAL_ARRAY", 0x8076: "GL_COLOR_ARRAY",
                      0x8078: "GL_TEXTURE_COORD_ARRAY", 0x8B9C: "GL_POINT_SIZE_ARRAY_OES"})
_grp("matrix_mode", {0x1700: "GL_MODELVIEW", 0x1701: "GL_PROJECTION", 0x1702: "GL_TEXTURE"})
_grp("blend", {0: "GL_ZERO", 1: "GL_ONE", 0x300: "GL_SRC_COLOR", 0x301: "GL_ONE_MINUS_SRC_COLOR",
               0x302: "GL_SRC_ALPHA", 0x303: "GL_ONE_MINUS_SRC_ALPHA", 0x304: "GL_DST_ALPHA",
               0x305: "GL_ONE_MINUS_DST_ALPHA", 0x306: "GL_DST_COLOR", 0x307: "GL_ONE_MINUS_DST_COLOR",
               0x308: "GL_SRC_ALPHA_SATURATE"})
_grp("texenv_target", {0x2300: "GL_TEXTURE_ENV", 0x8861: "GL_POINT_SPRITE_OES"})
_grp("texenv_pname", {0x2200: "GL_TEXTURE_ENV_MODE", 0x2201: "GL_TEXTURE_ENV_COLOR", 0x8571: "GL_COMBINE_RGB",
                      0x8572: "GL_COMBINE_ALPHA"})
_grp("texenv_mode", {0x2100: "GL_MODULATE", 0x2101: "GL_DECAL", 0x1E01: "GL_REPLACE", 0x0BE2: "GL_BLEND",
                     0x0104: "GL_ADD", 0x8570: "GL_COMBINE"})
_grp("fog_pname", {0x0B65: "GL_FOG_MODE", 0x0B62: "GL_FOG_DENSITY", 0x0B63: "GL_FOG_START",
                   0x0B64: "GL_FOG_END", 0x0B66: "GL_FOG_COLOR"})
_grp("fog_mode", {0x0800: "GL_EXP", 0x0801: "GL_EXP2", 0x2601: "GL_LINEAR"})
_grp("depth_func", {0x200: "GL_NEVER", 0x201: "GL_LESS", 0x202: "GL_EQUAL", 0x203: "GL_LEQUAL",
                    0x204: "GL_GREATER", 0x205: "GL_NOTEQUAL", 0x206: "GL_GEQUAL", 0x207: "GL_ALWAYS"})
_grp("cull", {0x404: "GL_FRONT", 0x405: "GL_BACK", 0x408: "GL_FRONT_AND_BACK"})
_grp("shade", {0x1D00: "GL_FLAT", 0x1D01: "GL_SMOOTH"})
_grp("hint_target", {0x0C50: "GL_PERSPECTIVE_CORRECTION_HINT", 0x0C51: "GL_POINT_SMOOTH_HINT",
                     0x0C52: "GL_LINE_SMOOTH_HINT", 0x0C54: "GL_FOG_HINT", 0x8192: "GL_GENERATE_MIPMAP_HINT"})
_grp("hint_mode", {0x1100: "GL_DONT_CARE", 0x1101: "GL_FASTEST", 0x1102: "GL_NICEST"})
_grp("pixelstore", {0x0CF5: "GL_UNPACK_ALIGNMENT", 0x0D05: "GL_PACK_ALIGNMENT"})
_grp("tex_target", {0x0DE1: "GL_TEXTURE_2D"})
_grp("format", {0x1906: "GL_ALPHA", 0x1907: "GL_RGB", 0x1908: "GL_RGBA", 0x1909: "GL_LUMINANCE",
                0x190A: "GL_LUMINANCE_ALPHA"})
_grp("pixtype", {0x1401: "GL_UNSIGNED_BYTE", 0x8033: "GL_UNSIGNED_SHORT_4_4_4_4",
                 0x8034: "GL_UNSIGNED_SHORT_5_5_5_1", 0x8363: "GL_UNSIGNED_SHORT_5_6_5"})
_grp("datatype", {0x1400: "GL_BYTE", 0x1401: "GL_UNSIGNED_BYTE", 0x1402: "GL_SHORT", 0x1403: "GL_UNSIGNED_SHORT",
                  0x1406: "GL_FLOAT", 0x140C: "GL_FIXED"})
_grp("texparam_pname", {0x2800: "GL_TEXTURE_MAG_FILTER", 0x2801: "GL_TEXTURE_MIN_FILTER",
                        0x2802: "GL_TEXTURE_WRAP_S", 0x2803: "GL_TEXTURE_WRAP_T", 0x8191: "GL_GENERATE_MIPMAP"})
_grp("texparam_value", {0x2600: "GL_NEAREST", 0x2601: "GL_LINEAR", 0x2700: "GL_NEAREST_MIPMAP_NEAREST",
                        0x2701: "GL_LINEAR_MIPMAP_NEAREST", 0x2702: "GL_NEAREST_MIPMAP_LINEAR",
                        0x2703: "GL_LINEAR_MIPMAP_LINEAR", 0x2901: "GL_REPEAT", 0x812F: "GL_CLAMP_TO_EDGE",
                        1: "GL_TRUE", 0: "GL_FALSE"})
_grp("buffer_target", {0x8892: "GL_ARRAY_BUFFER", 0x8893: "GL_ELEMENT_ARRAY_BUFFER"})
_grp("buffer_usage", {0x88E4: "GL_STATIC_DRAW", 0x88E8: "GL_DYNAMIC_DRAW", 0x88E0: "GL_STREAM_DRAW(not ES1.1)"})
_grp("draw_mode", {0: "GL_POINTS", 1: "GL_LINES", 2: "GL_LINE_LOOP", 3: "GL_LINE_STRIP", 4: "GL_TRIANGLES",
                   5: "GL_TRIANGLE_STRIP", 6: "GL_TRIANGLE_FAN"})
_grp("index_type", {0x1401: "GL_UNSIGNED_BYTE", 0x1403: "GL_UNSIGNED_SHORT"})
_grp("bool", {0: "GL_FALSE", 1: "GL_TRUE"})


def clear_mask(v):
    names = []
    for bit, n in ((0x100, "GL_DEPTH_BUFFER_BIT"), (0x400, "GL_STENCIL_BUFFER_BIT"), (0x4000, "GL_COLOR_BUFFER_BIT")):
        if v & bit:
            names.append(n)
    rest = v & ~0x4500
    if rest:
        names.append(f"0x{rest:x}?")
    return "|".join(names) or "0"


# prototypes: list of (arg name, kind) with kind:
#   enum:<group>  int  float  ptr  bitfield  floatenum:<group> (GLfloat param carrying an enum)
PROTO = {
    "glBindBuffer": [("target", "enum:buffer_target"), ("buffer", "int")],
    "glBindTexture": [("target", "enum:tex_target"), ("texture", "int")],
    "glBlendFunc": [("sfactor", "enum:blend"), ("dfactor", "enum:blend")],
    "glBufferData": [("target", "enum:buffer_target"), ("size", "int"), ("data", "ptr"), ("usage", "enum:buffer_usage")],
    "glClear": [("mask", "bitfield")],
    "glClearColor": [("red", "float"), ("green", "float"), ("blue", "float"), ("alpha", "float")],
    "glClearDepthf": [("depth", "float")],
    "glColor4f": [("red", "float"), ("green", "float"), ("blue", "float"), ("alpha", "float")],
    "glCullFace": [("mode", "enum:cull")],
    "glDeleteTextures": [("n", "int"), ("textures", "ptr")],
    "glDepthFunc": [("func", "enum:depth_func")],
    "glDepthMask": [("flag", "enum:bool")],
    "glDepthRangef": [("zNear", "float"), ("zFar", "float")],
    "glDisable": [("cap", "enum:cap")],
    "glEnable": [("cap", "enum:cap")],
    "glDisableClientState": [("array", "enum:client_state")],
    "glEnableClientState": [("array", "enum:client_state")],
    "glDrawElements": [("mode", "enum:draw_mode"), ("count", "int"), ("type", "enum:index_type"), ("indices", "ptr")],
    "glFinish": [],
    "glFogf": [("pname", "enum:fog_pname"), ("param", "float")],
    "glFogfv": [("pname", "enum:fog_pname"), ("params", "ptr4f")],
    "glFrustumf": [("left", "float"), ("right", "float"), ("bottom", "float"), ("top", "float"),
                   ("zNear", "float"), ("zFar", "float")],
    "glGenBuffers": [("n", "int"), ("buffers", "ptr")],
    "glGenTextures": [("n", "int"), ("textures", "ptr")],
    "glHint": [("target", "enum:hint_target"), ("mode", "enum:hint_mode")],
    "glLineWidth": [("width", "float")],
    "glLoadIdentity": [],
    "glMatrixMode": [("mode", "enum:matrix_mode")],
    "glMultMatrixf": [("m", "ptr16f")],
    "glOrthof": [("left", "float"), ("right", "float"), ("bottom", "float"), ("top", "float"),
                 ("zNear", "float"), ("zFar", "float")],
    "glPixelStorei": [("pname", "enum:pixelstore"), ("param", "int")],
    "glPopMatrix": [],
    "glPushMatrix": [],
    "glReadPixels": [("x", "int"), ("y", "int"), ("width", "int"), ("height", "int"),
                     ("format", "enum:format"), ("type", "enum:pixtype"), ("pixels", "ptr")],
    "glRotatef": [("angle", "float"), ("x", "float"), ("y", "float"), ("z", "float")],
    "glScalef": [("x", "float"), ("y", "float"), ("z", "float")],
    "glScissor": [("x", "int"), ("y", "int"), ("width", "int"), ("height", "int")],
    "glShadeModel": [("mode", "enum:shade")],
    "glTexCoordPointer": [("size", "int"), ("type", "enum:datatype"), ("stride", "int"), ("pointer", "ptr")],
    "glTexEnvf": [("target", "enum:texenv_target"), ("pname", "enum:texenv_pname"), ("param", "floatenum:texenv_mode")],
    "glTexImage2D": [("target", "enum:tex_target"), ("level", "int"), ("internalformat", "enum:format"),
                     ("width", "int"), ("height", "int"), ("border", "int"), ("format", "enum:format"),
                     ("type", "enum:pixtype"), ("pixels", "ptr")],
    "glTexParameteri": [("target", "enum:tex_target"), ("pname", "enum:texparam_pname"), ("param", "enum:texparam_value")],
    "glTranslatef": [("x", "float"), ("y", "float"), ("z", "float")],
    "glVertexPointer": [("size", "int"), ("type", "enum:datatype"), ("stride", "int"), ("pointer", "ptr")],
    "glViewport": [("x", "int"), ("y", "int"), ("width", "int"), ("height", "int")],
}


def arg_value(st, idx):
    if idx < 4:
        return st.r.get(f"r{idx}")
    return st.stk.get(4 * (idx - 4))


def describe(elf, v, kind):
    d = fmt_value(elf, v, as_float=kind in ("float",) or kind.startswith("floatenum"))
    if d["kind"] != "const":
        return d
    vals = [int(x, 16) for x in d["values"]]
    if kind.startswith("enum:"):
        tab = E[kind[5:]]
        d["names"] = [tab.get(x, f"UNKNOWN_0x{x:x}") for x in vals]
    elif kind.startswith("floatenum:"):
        tab = E[kind[10:]]
        d["names"] = [tab.get(int(f32(x)), f"UNKNOWN({f32(x)})") if f32(x) == int(f32(x)) else f"non-integral {f32(x)}" for x in vals]
    elif kind == "bitfield":
        d["names"] = [clear_mask(x) for x in vals]
    elif kind == "int":
        d["int"] = [((x ^ 0x80000000) - 0x80000000) for x in vals]
    elif kind in ("ptr", "ptr4f", "ptr16f"):
        out = []
        for x in vals:
            p = {"addr": f"0x{x:x}", "target": elf.describe_addr(x), "section": elf.section_of(x)}
            n = {"ptr4f": 4, "ptr16f": 16}.get(kind)
            if n and p["section"] in (".rodata", ".data"):
                b = elf.read_bytes(x, 4 * n)
                p["float_contents"] = list(struct.unpack(f"<{n}f", b))
                p["contents_note"] = ("immutable .rodata" if p["section"] == ".rodata"
                                      else "initial value of writable .data; may change at runtime")
            elif n and p["section"] == ".bss":
                p["contents_note"] = ".bss (zero-initialised, written at runtime)"
            out.append(p)
        d["pointers"] = out
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v7a", default=DEFAULT_V7A)
    ap.add_argument("--v5", default=DEFAULT_V5)
    ap.add_argument("--out", default=os.path.join(REPO, "analysis/native/gl_usage.json"))
    args = ap.parse_args()

    elf = ArmElf(args.v7a, "v7a")
    v5 = ArmElf(args.v5, "v5")

    # callers index (direct BL/B to a function start)
    callers = defaultdict(list)
    gl_sites = []
    for insn in elf.all_code_insns():
        if insn.id not in (A.ARM_INS_B, A.ARM_INS_BL):
            continue
        t = elf.branch_target(insn)
        if t is None:
            continue
        if t in elf.plt_map and elf.plt_map[t].startswith("gl"):
            gl_sites.append(insn)
        elif insn.id == A.ARM_INS_BL or (elf.func_at(insn.address) and elf.func_at(t) and elf.func_at(t).addr == t
                                          and elf.func_at(insn.address).addr != t):
            callers[t].append(insn)

    cp_cache = {}

    def cp_for(addr):
        f = elf.func_at(addr)
        if f is None:
            return None
        if f.addr not in cp_cache:
            cp_cache[f.addr] = ConstProp(elf, f)
        return cp_cache[f.addr]

    def resolve_caller_arg(func_addr, arg_idx, kind):
        out = []
        for c in callers.get(func_addr, []):
            cp = cp_for(c.address)
            if cp is None:
                continue
            st = cp.pre_state.get(c.address)
            v = arg_value(st, arg_idx) if st is not None else None
            out.append({"call_site": f"0x{c.address:x}", "caller": cp.func.name, "value": describe(elf, v, kind)})
        return out

    sites = defaultdict(list)
    for insn in gl_sites:
        name = elf.plt_map[elf.branch_target(insn)]
        cp = cp_for(insn.address)
        st = cp.pre_state.get(insn.address) if cp else None
        rec = {"addr": f"0x{insn.address:x}", "func": cp.func.name if cp else None,
               "func_addr": f"0x{cp.func.addr:x}" if cp else None,
               "kind": ("bl" if insn.id == A.ARM_INS_BL else "b (tail call)") +
                       (f" cond={insn.mnemonic}" if insn.cc != A.ARM_CC_AL else ""),
               "args": {}}
        for idx, (an, kind) in enumerate(PROTO[name]):
            v = arg_value(st, idx) if st is not None else None
            d = describe(elf, v, kind)
            if d["kind"] == "caller_arg" and cp is not None:
                d["via_callers"] = resolve_caller_arg(cp.func.addr, d["arg_index"], kind)
            rec["args"][an] = d
        sites[name].append(rec)

    # ------------------------------------------------------------ summary
    def const_names(rec, an):
        d = rec["args"].get(an, {})
        if d.get("kind") == "const":
            return d.get("names") or d.get("values")
        if d.get("kind") == "caller_arg":
            names = []
            for c in d.get("via_callers", []):
                if c["value"].get("kind") == "const":
                    names += c["value"].get("names") or c["value"].get("values")
                else:
                    names.append("unresolved(via caller)")
            return names or ["unresolved(caller_arg, no direct callers)"]
        return [d.get("kind", "unresolved") + (f":{d['symbol']}" if "symbol" in d else "")]

    def tally(fn, *argnames):
        c = Counter()
        for rec in sites.get(fn, []):
            combos = [const_names(rec, an) for an in argnames]
            key = " , ".join("/".join(map(str, x)) for x in combos)
            c[key] += 1
        return dict(sorted(c.items()))

    def floats(fn, *argnames):
        out = []
        for rec in sites.get(fn, []):
            row = {"site": rec["addr"], "func": rec["func"]}
            for an in argnames:
                d = rec["args"][an]
                row[an] = d.get("float") if d["kind"] == "const" else d["kind"] + (f":{d.get('symbol')}" if d.get("symbol") else "")
            out.append(row)
        return out

    summary = {
        "glEnable": tally("glEnable", "cap"),
        "glDisable": tally("glDisable", "cap"),
        "glEnableClientState": tally("glEnableClientState", "array"),
        "glDisableClientState": tally("glDisableClientState", "array"),
        "glMatrixMode": tally("glMatrixMode", "mode"),
        "glBlendFunc": tally("glBlendFunc", "sfactor", "dfactor"),
        "glTexEnvf": tally("glTexEnvf", "target", "pname", "param"),
        "glFogf": tally("glFogf", "pname", "param"),
        "glFogfv": tally("glFogfv", "pname"),
        "glDepthFunc": tally("glDepthFunc", "func"),
        "glDepthMask": tally("glDepthMask", "flag"),
        "glDepthRangef": floats("glDepthRangef", "zNear", "zFar"),
        "glClearDepthf": floats("glClearDepthf", "depth"),
        "glClearColor": floats("glClearColor", "red", "green", "blue", "alpha"),
        "glColor4f": floats("glColor4f", "red", "green", "blue", "alpha"),
        "glCullFace": tally("glCullFace", "mode"),
        "glShadeModel": tally("glShadeModel", "mode"),
        "glHint": tally("glHint", "target", "mode"),
        "glPixelStorei": tally("glPixelStorei", "pname", "param"),
        "glTexImage2D": tally("glTexImage2D", "target", "level", "internalformat", "border", "format", "type"),
        "glTexParameteri": tally("glTexParameteri", "target", "pname", "param"),
        "glBindBuffer": tally("glBindBuffer", "target"),
        "glBufferData": tally("glBufferData", "target", "usage"),
        "glDrawElements": tally("glDrawElements", "mode", "type"),
        "glVertexPointer": tally("glVertexPointer", "size", "type", "stride"),
        "glTexCoordPointer": tally("glTexCoordPointer", "size", "type", "stride"),
        "glReadPixels": tally("glReadPixels", "format", "type"),
        "glClear": tally("glClear", "mask"),
        "glLineWidth": floats("glLineWidth", "width"),
        "glOrthof": floats("glOrthof", "left", "right", "bottom", "top", "zNear", "zFar"),
        "glFrustumf": floats("glFrustumf", "left", "right", "bottom", "top", "zNear", "zFar"),
        "glRotatef": floats("glRotatef", "angle", "x", "y", "z"),
        "glScalef": floats("glScalef", "x", "y", "z"),
        "glTranslatef": floats("glTranslatef", "x", "y", "z"),
    }

    total = sum(len(v) for v in sites.values())
    resolved = unresolved = 0
    for fn, recs in sites.items():
        for rec in recs:
            for an, d in rec["args"].items():
                if d["kind"] == "const" or (d["kind"] == "caller_arg" and d.get("via_callers") and all(
                        c["value"]["kind"] == "const" for c in d["via_callers"])):
                    resolved += 1
                else:
                    unresolved += 1

    v5_counts = Counter()
    for insn in v5.all_code_insns():
        if insn.id in (A.ARM_INS_B, A.ARM_INS_BL):
            t = v5.branch_target(insn)
            if t in v5.plt_map and v5.plt_map[t].startswith("gl"):
                v5_counts[v5.plt_map[t]] += 1

    imported_gl = sorted(n for n, _, _ in elf.dyn_undef if n.startswith("gl"))
    out = {
        "schema": "gl_usage/1",
        "generated_by": "tools/validation/platform/gl_census.py",
        "binary": {"label": "v7a", "sha256": elf.sha256, "path": os.path.relpath(elf.path, REPO)},
        "cross_check": {"label": "v5", "sha256": v5.sha256, "gl_call_counts": dict(sorted(v5_counts.items()))},
        "api_evidence": {
            "manifest_glEsVersion": "0x00010001 (work/apktool/AndroidManifest.xml <uses-feature android:glEsVersion>)",
            "DT_NEEDED": "libGLESv1_CM.so",
            "imported_gl_functions": imported_gl,
            "not_imported_examples": ["glColorPointer", "glNormalPointer", "glLightfv", "glMaterialfv", "glAlphaFunc",
                                      "glTexSubImage2D", "glCompressedTexImage2D", "glColor4ub", "glPointSize",
                                      "glDrawArrays", "glLoadMatrixf", "glGetError", "glGetString",
                                      "glPolygonOffset", "glStencilFunc", "glTexEnvi", "glTexParameterf",
                                      "glActiveTexture", "glClientActiveTexture", "glBufferSubData",
                                      "glDeleteBuffers", "glColorMask"],
        },
        "method": [
            "direct BL/B to gl* PLT stubs (see platform_imports.json)",
            "forward constant propagation per function with block merges (armelf.ConstProp): mov/mvn/movw/movt, ldr literal, GOT loads, add/sub/orr/and/eor/bic/rsb/shifts, vldr/vmov transfers, str/stm/vstr to outgoing stack slots",
            "softfp: float args are core-register/stack bit patterns, decoded as IEEE-754 binary32",
            "caller_arg values are resolved one level up at every direct caller",
        ],
        "stats": {"call_sites": total, "args_resolved": resolved, "args_unresolved_or_runtime": unresolved},
        "state_summary": summary,
        "call_sites": dict(sorted(sites.items())),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(f"{total} GL call sites; {resolved} args resolved, {unresolved} unresolved/runtime")
    print(f"wrote {os.path.relpath(args.out, REPO)}")


if __name__ == "__main__":
    main()
