"""Lista exports da ProfitDLL filtrando por Subscribe/Trade/Quote/Tick/Callback."""

import struct
import sys

DLL_PATH = r"C:\Nelogica\profitdll.dll"
FILTERS = ["Subscribe", "Trade", "Quote", "Tick", "Callback", "Set"]

with open(DLL_PATH, "rb") as f:
    data = f.read()

# DOS header → PE offset
pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
# PE signature check
assert data[pe_offset : pe_offset + 4] == b"PE\x00\x00"

# COFF header
machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
is_64 = machine == 0x8664

# Optional header
opt_offset = pe_offset + 24
magic = struct.unpack_from("<H", data, opt_offset)[0]

# Export table RVA
if is_64:
    export_rva = struct.unpack_from("<I", data, opt_offset + 112)[0]
else:
    export_rva = struct.unpack_from("<I", data, opt_offset + 96)[0]

# Section headers — find section containing export RVA
num_sections = struct.unpack_from("<H", data, pe_offset + 6)[0]
opt_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
sec_offset = pe_offset + 24 + opt_size


def rva_to_offset(rva):
    for i in range(num_sections):
        s = sec_offset + i * 40
        vaddr = struct.unpack_from("<I", data, s + 12)[0]
        vsize = struct.unpack_from("<I", data, s + 16)[0]
        raw = struct.unpack_from("<I", data, s + 20)[0]
        if vaddr <= rva < vaddr + vsize:
            return raw + (rva - vaddr)
    return None


exp_off = rva_to_offset(export_rva)
if exp_off is None:
    print("Export table nao encontrada")
    sys.exit(1)

num_names = struct.unpack_from("<I", data, exp_off + 24)[0]
names_rva = struct.unpack_from("<I", data, exp_off + 32)[0]
names_off = rva_to_offset(names_rva)

all_exports = []
for i in range(num_names):
    name_rva = struct.unpack_from("<I", data, names_off + i * 4)[0]
    name_off = rva_to_offset(name_rva)
    end = data.index(b"\x00", name_off)
    name = data[name_off:end].decode("ascii", errors="replace")
    all_exports.append(name)

print(f"Total exports: {len(all_exports)}\n")
print("=== Filtrados ===")
for name in sorted(all_exports):
    if any(f in name for f in FILTERS):
        print(name)

print("\n=== Todos os exports ===")
for name in sorted(all_exports):
    print(name)
