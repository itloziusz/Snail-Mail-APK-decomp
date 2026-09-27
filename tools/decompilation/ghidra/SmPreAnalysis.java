// Snail Mail pre-analysis fixups (run with analyzeHeadless -preScript).
//
// Args: <data_regions.txt>
//   data_regions.txt : lines "0xSTART 0xEND" (half-open) derived from the ELF
//                      $d mapping symbols by tools/decompilation/ghidra_prepare.py
//
// 1. Forces the TMode context register to 0 (ARM state) over every executable
//    block, so nothing is decoded as Thumb (the libraries carry no $t mapping
//    symbols and no odd FUNC symbol values; see analysis/native/elf_audit.*.json).
// 2. Defines every $d region inside executable sections as data (dwords where
//    4-byte aligned, bytes otherwise) so literal pools and inline tables are
//    never disassembled.
//@category SnailMail
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.listing.ProgramContext;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.data.DWordDataType;
import ghidra.program.model.data.ByteDataType;

import java.math.BigInteger;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;

public class SmPreAnalysis extends GhidraScript {
    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("usage: SmPreAnalysis.java <data_regions.txt>");
        }
        ProgramContext ctx = currentProgram.getProgramContext();
        Register tmode = ctx.getRegister("TMode");
        int execBlocks = 0;
        if (tmode != null) {
            for (MemoryBlock b : currentProgram.getMemory().getBlocks()) {
                if (b.isExecute() && b.isInitialized()) {
                    ctx.setValue(tmode, b.getStart(), b.getEnd(), BigInteger.ZERO);
                    execBlocks++;
                }
            }
        }
        Listing listing = currentProgram.getListing();
        List<String> lines = Files.readAllLines(Paths.get(args[0]));
        int regions = 0, dwords = 0, bytes = 0, failures = 0;
        for (String line : lines) {
            line = line.trim();
            if (line.isEmpty() || line.startsWith("#")) continue;
            String[] p = line.split("\\s+");
            long s = Long.decode(p[0]);
            long e = Long.decode(p[1]);
            if (e <= s) continue;
            regions++;
            try {
                listing.clearCodeUnits(toAddr(s), toAddr(e - 1), false);
            } catch (Exception ex) {
                failures++;
            }
            long cur = s;
            while (cur < e) {
                Address a = toAddr(cur);
                try {
                    if ((cur & 3) == 0 && cur + 4 <= e) {
                        listing.createData(a, DWordDataType.dataType);
                        cur += 4; dwords++;
                    } else {
                        listing.createData(a, ByteDataType.dataType);
                        cur += 1; bytes++;
                    }
                } catch (Exception ex) {
                    failures++;
                    cur += 1;
                }
            }
        }
        println(String.format("SmPreAnalysis: TMode=0 on %d exec blocks; %d data regions -> %d dwords, %d bytes, %d failures",
                execBlocks, regions, dwords, bytes, failures));
    }
}
