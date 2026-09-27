// Snail Mail Ghidra export (run with analyzeHeadless -process ... -noanalysis -postScript).
//
// Args: <binary short name> <sha256> <analysis/native dir> <decompile timeout seconds>
//
// Writes (deterministic ordering: everything sorted by address):
//   <out>/generated/decomp/<bin>/<addr8>_<sanitized>.c   decompiled C per function (analysis aid only)
//   <out>/generated/<bin>_callgraph.json                 call edges from Ghidra references/flows
//   <out>/strings_xrefs.<bin>.json                       defined strings + referencing functions
//   <out>/ghidra_export_summary.<bin>.json               counts, failures, symbol-less functions
//
// Before decompiling, every function in a program whose compiler spec offers
// "__stdcall_softfp" is switched to it: the v7a library is softfp (float
// arguments in core registers, Tag_ABI_HardFP_use: deprecated) while
// ARM.cspec's default prototype passes floats in s0-s15. This is an in-memory
// change only (the program is opened -readOnly).
//@category SnailMail
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSetView;
import ghidra.program.model.data.StringDataInstance;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.DataIterator;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.FlowType;
import ghidra.program.model.symbol.RefType;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceIterator;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import ghidra.program.model.lang.PrototypeModel;
import ghidra.program.model.listing.Parameter;
import ghidra.program.model.listing.ParameterImpl;
import ghidra.program.model.listing.Variable;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.TreeSet;

public class SmExport extends GhidraScript {

    private static String q(String s) {
        if (s == null) return "null";
        StringBuilder b = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20 || c > 0x7e) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.append('"').toString();
    }

    private static String hx(Address a) {
        return a == null ? "null" : "\"0x" + Long.toHexString(a.getOffset()) + "\"";
    }

    private static String sanitize(String s) {
        String r = s.replaceAll("[^A-Za-z0-9_]", "_");
        return r.length() > 120 ? r.substring(0, 120) : r;
    }

    private static void write(File f, String s) throws Exception {
        f.getParentFile().mkdirs();
        try (Writer w = new OutputStreamWriter(new FileOutputStream(f), StandardCharsets.UTF_8)) {
            w.write(s);
        }
    }

    private boolean hasImportedSymbol(Address a) {
        for (Symbol s : currentProgram.getSymbolTable().getSymbols(a)) {
            if (s.getSource() == SourceType.IMPORTED) return true;
        }
        return false;
    }

    private List<String> symbolNames(Address a) {
        TreeSet<String> names = new TreeSet<>();
        for (Symbol s : currentProgram.getSymbolTable().getSymbols(a)) {
            names.add(s.getName(true));
        }
        return new ArrayList<>(names);
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String bin = args[0];
        String sha = args[1];
        File out = new File(args[2]);
        int timeout = Integer.parseInt(args[3]);
        File decompDir = new File(out, "generated/decomp/" + bin);
        decompDir.mkdirs();

        Listing listing = currentProgram.getListing();
        SymbolTable st = currentProgram.getSymbolTable();

        // --- calling convention fixup (softfp)
        String cc = null;
        for (PrototypeModel m : currentProgram.getCompilerSpec().getCallingConventions()) {
            if ("__stdcall_softfp".equals(m.getName())) cc = m.getName();
        }
        int ccChanged = 0;
        List<String> ccFailures = new ArrayList<>();
        if (cc != null) {
            FunctionIterator it = listing.getFunctions(true);
            while (it.hasNext()) {
                Function f = it.next();
                if (f.isExternal()) continue;
                try {
                    if (f.getSignatureSource() == SourceType.DEFAULT) {
                        // no applied signature: keep parameters unlocked so the
                        // decompiler still infers them, only switch the model
                        f.setCallingConvention(cc);
                    } else {
                        // demangler-applied signature (possibly __thiscall with an
                        // auto 'this'): re-apply it with every parameter explicit so
                        // 'this' survives the switch to the softfp model
                        List<Variable> np = new ArrayList<>();
                        for (Parameter p : f.getParameters()) {
                            String pn = p.getName();
                            if (p.isAutoParameter() && "this".equals(pn)) pn = "this_";
                            np.add(new ParameterImpl(pn, p.getDataType(), currentProgram));
                        }
                        f.updateFunction(cc, f.getReturn(), np,
                                Function.FunctionUpdateType.DYNAMIC_STORAGE_ALL_PARAMS, true, f.getSignatureSource());
                    }
                    ccChanged++;
                } catch (Exception e) {
                    ccFailures.add("{\"addr\": " + hx(f.getEntryPoint()) + ", \"error\": " + q(e.toString()) + "}");
                }
            }
        }

        // --- functions, sorted by entry (listing iterator is address-ordered)
        List<Function> funcs = new ArrayList<>();
        FunctionIterator fit = listing.getFunctions(true);
        while (fit.hasNext()) funcs.add(fit.next());

        DecompInterface ifc = new DecompInterface();
        DecompileOptions opts = new DecompileOptions();
        opts.grabFromProgram(currentProgram);
        ifc.setOptions(opts);
        ifc.toggleCCode(true);
        ifc.toggleSyntaxTree(true);
        ifc.setSimplificationStyle("decompile");
        if (!ifc.openProgram(currentProgram)) {
            throw new RuntimeException("decompiler failed to open program: " + ifc.getLastMessage());
        }

        StringBuilder cg = new StringBuilder();
        cg.append("{\n  \"binary\": ").append(q(bin)).append(",\n  \"binary_sha256\": ").append(q(sha))
          .append(",\n  \"source\": \"Ghidra ").append(getGhidraVersion()).append(" references/flow types after auto-analysis\",\n  \"functions\": [\n");
        List<String> okList = new ArrayList<>();
        List<String> failList = new ArrayList<>();
        List<String> noSym = new ArrayList<>();
        int timeouts = 0, thunks = 0, processed = 0, skipped = 0;
        boolean firstFn = true;
        for (Function f : funcs) {
            if (monitor.isCancelled()) break;
            Address ep = f.getEntryPoint();
            AddressSetView body = f.getBody();
            boolean imported = hasImportedSymbol(ep);
            if (f.isThunk()) thunks++;
            String blk = currentProgram.getMemory().getBlock(ep) == null ? "" : currentProgram.getMemory().getBlock(ep).getName();
            if (!imported && !f.isExternal() && !"EXTERNAL".equals(blk)) {
                noSym.add("{\"addr\": " + hx(ep) + ", \"name\": " + q(f.getName(true)) + ", \"body_size\": "
                          + body.getNumAddresses() + ", \"block\": " + q(currentProgram.getMemory().getBlock(ep) == null ? null : currentProgram.getMemory().getBlock(ep).getName())
                          + ", \"thunk\": " + f.isThunk() + "}");
            }
            // call edges
            TreeMap<String, String> edges = new TreeMap<>();
            InstructionIterator ii = listing.getInstructions(body, true);
            while (ii.hasNext()) {
                Instruction ins = ii.next();
                FlowType ft = ins.getFlowType();
                Reference[] refs = ins.getReferencesFrom();
                boolean emitted = false;
                for (Reference r : refs) {
                    RefType rt = r.getReferenceType();
                    if (!rt.isFlow()) continue;
                    Address to = r.getToAddress();
                    Function tf = getFunctionAt(to);
                    boolean isCall = rt.isCall();
                    boolean tail = !isCall && rt.isJump() && tf != null && !body.contains(to);
                    if (!isCall && !tail) continue;
                    String toName = tf == null ? null : tf.getName(true);
                    String ext = null;
                    if (tf != null && tf.isThunk()) {
                        Function thunked = tf.getThunkedFunction(true);
                        if (thunked != null) ext = thunked.getName(true);
                    }
                    edges.put(String.format("%08x:%08x:a", ins.getAddress().getOffset(), to.getOffset()),
                        "{\"site\": " + hx(ins.getAddress()) + ", \"to\": " + hx(to) + ", \"to_name\": " + q(toName)
                        + ", \"kind\": " + q(tail ? "tailcall" : (rt.isComputed() ? "computed_call_resolved" : "call"))
                        + (ext != null ? ", \"thunk_target\": " + q(ext) : "") + "}");
                    emitted = true;
                }
                if (!emitted && ft.isCall() && ft.isComputed()) {
                    edges.put(String.format("%08x:ffffffff:u", ins.getAddress().getOffset()),
                        "{\"site\": " + hx(ins.getAddress()) + ", \"to\": null, \"to_name\": null, \"kind\": \"computed_call_unresolved\", \"insn\": "
                        + q(ins.toString()) + "}");
                }
            }
            // decompile (skip Ghidra's synthetic EXTERNAL-block placeholders for imports)
            String status;
            String reason = null;
            DecompileResults res = null;
            String blockName = currentProgram.getMemory().getBlock(ep) == null ? "" : currentProgram.getMemory().getBlock(ep).getName();
            boolean skip = f.isExternal() || "EXTERNAL".equals(blockName);
            if (!skip) {
                try {
                    res = ifc.decompileFunction(f, timeout, monitor);
                } catch (Exception e) {
                    reason = "exception: " + e;
                }
            }
            if (skip) {
                status = "skipped_external";
                skipped++;
            } else if (res != null && res.decompileCompleted() && res.getDecompiledFunction() != null) {
                status = "ok";
                StringBuilder c = new StringBuilder();
                c.append("/*\n * DECOMPILED OUTPUT (Ghidra ").append(getGhidraVersion()).append(") - analysis aid, NOT reconstructed source.\n")
                 .append(" * binary: ").append(bin).append(" sha256=").append(sha).append("\n")
                 .append(" * function: ").append(f.getName(true)).append(" @ ").append(bin).append(":0x").append(Long.toHexString(ep.getOffset()))
                 .append(" body=").append(body.getNumAddresses()).append(" bytes\n")
                 .append(" * symbols at entry: ").append(String.join(", ", symbolNames(ep))).append("\n")
                 .append(" * calling convention forced: ").append(cc == null ? "(language default)" : cc).append("\n */\n");
                c.append(res.getDecompiledFunction().getC());
                String fname = String.format("%08x_%s.c", ep.getOffset(), sanitize(f.getName(true)));
                write(new File(decompDir, fname), c.toString());
                okList.add(hx(ep));
            } else {
                status = "failed";
                if (res != null) {
                    if (res.isTimedOut()) { reason = "timeout"; timeouts++; }
                    else if (res.failedToStart()) reason = "failed_to_start: " + res.getErrorMessage();
                    else if (reason == null) reason = res.getErrorMessage();
                    if (reason == null || reason.isBlank()) reason = "no output";
                }
                failList.add("{\"addr\": " + hx(ep) + ", \"name\": " + q(f.getName(true)) + ", \"reason\": " + q(reason == null ? null : reason.trim()) + "}");
                // decompiler process may be dead after a crash/timeout: reopen
                ifc.dispose();
                ifc = new DecompInterface();
                ifc.setOptions(opts);
                ifc.toggleCCode(true);
                ifc.toggleSyntaxTree(true);
                ifc.setSimplificationStyle("decompile");
                ifc.openProgram(currentProgram);
            }
            processed++;
            if (!firstFn) cg.append(",\n");
            firstFn = false;
            cg.append("    {\"addr\": ").append(hx(ep)).append(", \"name\": ").append(q(f.getName(true)))
              .append(", \"body_size\": ").append(body.getNumAddresses())
              .append(", \"max_addr\": ").append(hx(body.getMaxAddress()))
              .append(", \"has_elf_symbol\": ").append(imported)
              .append(", \"thunk\": ").append(f.isThunk())
              .append(", \"external\": ").append(f.isExternal())
              .append(", \"calling_convention\": ").append(q(f.getCallingConventionName()))
              .append(", \"decompile\": ").append(q(status))
              .append(", \"calls\": [");
            boolean fe = true;
            for (String e : edges.values()) {
                if (!fe) cg.append(", ");
                fe = false;
                cg.append(e);
            }
            cg.append("]}");
        }
        ifc.dispose();
        cg.append("\n  ]\n}\n");
        write(new File(out, "generated/" + bin + "_callgraph.json"), cg.toString());

        // --- strings and their referencing functions
        StringBuilder sx = new StringBuilder();
        sx.append("{\n  \"binary\": ").append(q(bin)).append(",\n  \"binary_sha256\": ").append(q(sha))
          .append(",\n  \"source\": \"Ghidra ").append(getGhidraVersion()).append(" defined string data + ReferenceManager.getReferencesTo (includes refs Ghidra's ARM constant analyzer created for PC/GOT-relative address computations)\",\n  \"strings\": [\n");
        DataIterator di = listing.getDefinedData(true);
        boolean firstS = true;
        int nStr = 0, nStrRef = 0;
        while (di.hasNext()) {
            Data d = di.next();
            if (!d.hasStringValue()) continue;
            String val;
            try {
                val = StringDataInstance.getStringDataInstance(d).getStringValue();
            } catch (Exception e) {
                val = String.valueOf(d.getValue());
            }
            TreeMap<Long, String> refs = new TreeMap<>();
            ReferenceIterator ri = currentProgram.getReferenceManager().getReferencesTo(d.getAddress());
            while (ri.hasNext()) {
                Reference r = ri.next();
                Address from = r.getFromAddress();
                Function rf = getFunctionContaining(from);
                refs.put(from.getOffset(), "{\"from\": " + hx(from) + ", \"function\": " + (rf == null ? "null" : hx(rf.getEntryPoint()))
                         + ", \"function_name\": " + q(rf == null ? null : rf.getName(true)) + ", \"type\": " + q(r.getReferenceType().getName()) + "}");
            }
            nStr++;
            if (!refs.isEmpty()) nStrRef++;
            if (!firstS) sx.append(",\n");
            firstS = false;
            sx.append("    {\"addr\": ").append(hx(d.getAddress())).append(", \"block\": ")
              .append(q(currentProgram.getMemory().getBlock(d.getAddress()).getName()))
              .append(", \"value\": ").append(q(val)).append(", \"refs\": [").append(String.join(", ", refs.values())).append("]}");
        }
        sx.append("\n  ],\n  \"count\": ").append(nStr).append(",\n  \"count_referenced\": ").append(nStrRef).append("\n}\n");
        write(new File(out, "strings_xrefs." + bin + ".json"), sx.toString());

        // --- summary
        StringBuilder sm = new StringBuilder();
        sm.append("{\n  \"binary\": ").append(q(bin))
          .append(",\n  \"binary_sha256\": ").append(q(sha))
          .append(",\n  \"ghidra_version\": ").append(q(getGhidraVersion()))
          .append(",\n  \"language\": ").append(q(currentProgram.getLanguageID().getIdAsString()))
          .append(",\n  \"compiler_spec\": ").append(q(currentProgram.getCompilerSpec().getCompilerSpecID().getIdAsString()))
          .append(",\n  \"calling_convention_forced\": ").append(q(cc))
          .append(",\n  \"calling_convention_changed_functions\": ").append(ccChanged)
          .append(",\n  \"calling_convention_failures\": [").append(String.join(", ", ccFailures)).append("]")
          .append(",\n  \"decompile_timeout_s\": ").append(timeout)
          .append(",\n  \"functions_processed\": ").append(processed)
          .append(",\n  \"thunks\": ").append(thunks)
          .append(",\n  \"skipped_external_placeholders\": ").append(skipped)
          .append(",\n  \"decompiled_ok_count\": ").append(okList.size())
          .append(",\n  \"decompile_failed_count\": ").append(failList.size())
          .append(",\n  \"decompile_timeouts\": ").append(timeouts)
          .append(",\n  \"decompile_failures\": [").append(String.join(", ", failList)).append("]")
          .append(",\n  \"functions_without_elf_symbol_count\": ").append(noSym.size())
          .append(",\n  \"functions_without_elf_symbol\": [\n    ").append(String.join(",\n    ", noSym)).append("\n  ]")
          .append(",\n  \"strings_defined\": ").append(nStr)
          .append(",\n  \"strings_referenced\": ").append(nStrRef)
          .append(",\n  \"decompiled_ok\": [").append(String.join(", ", okList)).append("]\n}\n");
        write(new File(out, "ghidra_export_summary." + bin + ".json"), sm.toString());
        println(String.format("SmExport %s: %d functions, %d decompiled, %d failed (%d timeouts), %d without ELF symbol, %d strings",
                bin, processed, okList.size(), failList.size(), timeouts, noSym.size(), nStr));
    }
}
