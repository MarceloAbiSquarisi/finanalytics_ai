"""
Patch D1 + D2 + D3 no dashboard.html

D2: Remove as 26 linhas de lixo no início do arquivo
    (bloco duplicado colado antes do <!DOCTYPE html> real)

D3: volSeries.setData usa bars (timestamp original)
    Corrige para _bars2 (timestamps convertidos, mesmos do priceSeries)

D1: Remove chamada initDaySeparators duplicada (usa implementacao antiga)
    drawDaySeparators(chart, bars, container) ja e chamado logo abaixo
"""
from __future__ import annotations
import argparse, hashlib, sys
from pathlib import Path

TARGET = Path(
    r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai"
    r"\interfaces\api\static\dashboard.html"
)

# Prefixo lixo que deve ser removido (linhas 0-25 do arquivo corrompido)
JUNK_PREFIX = """\
<!DOCTYPE html>
</script>
// -- END PROFIT AGENT STATUS --------------------------------------------------
<script>
function faDTToggle(btn){
  var s=document.getElementById('dt-submenu');
  btn.classList.toggle('open');
  s.classList.toggle('open');
  localStorage.setItem('fa_dt_open',s.classList.contains('open')?'1':'0');
}
(function(){
  var p=window.location.pathname;
  if(p.startsWith('/daytrade')||p==='/diario'){
    var t=document.getElementById('dt-toggle');
    var s=document.getElementById('dt-submenu');
    if(t)t.classList.add('open');
    if(s)s.classList.add('open');
  } else if(localStorage.getItem('fa_dt_open')==='1'){
    var t2=document.getElementById('dt-toggle');
    var s2=document.getElementById('dt-submenu');
    if(t2)t2.classList.add('open');
    if(s2)s2.classList.add('open');
  }
})();
</script>
</script>
"""

# D3: volume usa bar.time original em vez de _bars2
D3_OLD = (
    "  volSeries.setData(bars.map(b => ({\n"
    "    time:b.time, value:b.volume||0,\n"
    "    color: b.close>=b.open ? 'rgba(46,212,122,.22)' : 'rgba(224,85,85,.22)',\n"
    "  })));"
)
D3_NEW = (
    "  volSeries.setData(_bars2.map(b => ({\n"
    "    time:b.time, value:b.volume||0,\n"
    "    color: b.close>=b.open ? 'rgba(46,212,122,.22)' : 'rgba(224,85,85,.22)',\n"
    "  })));"
)

# D1: remove initDaySeparators duplicado (drawDaySeparators chamado 30 linhas abaixo)
D1_OLD = (
    "  // Separadores de dia apos render\n"
    "  setTimeout(function(){if(typeof initDaySeparators==='function'"
    "&&typeof bars!=='undefined')initDaySeparators(bars);},250);"
)
D1_NEW = (
    "  // Separadores de dia: drawDaySeparators(chart,bars,container) chamado abaixo"
)


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()[:12]


def apply_patch(path: Path, dry_run: bool = False) -> int:
    if not path.exists():
        print(f"[ERROR] Arquivo não encontrado: {path}", file=sys.stderr)
        return 2

    raw = path.read_bytes()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")

    patched = text
    applied = []

    # D2: remove junk prefix
    junk = JUNK_PREFIX.replace("\r\n", "\n")
    if junk in patched:
        patched = patched.replace(junk, "", 1)
        applied.append("D2 (junk prefix removido)")
    elif "// -- END PROFIT AGENT STATUS" in patched[:500]:
        print("[WARN] D2: prefixo lixo em formato diferente — verifique manualmente")
    else:
        print("[OK] D2: prefixo já removido")

    # D3: corrige volume timestamps
    if "_bars2.map(b => ({" in patched:
        print("[OK] D3: já corrigido")
    elif D3_OLD in patched:
        patched = patched.replace(D3_OLD, D3_NEW, 1)
        applied.append("D3 (volume usa _bars2)")
    else:
        print("[WARN] D3: âncora não encontrada")

    # D1: remove initDaySeparators call
    if "drawDaySeparators(chart,bars,container) chamado abaixo" in patched:
        print("[OK] D1: já corrigido")
    elif D1_OLD in patched:
        patched = patched.replace(D1_OLD, D1_NEW, 1)
        applied.append("D1 (initDaySeparators duplicado removido)")
    else:
        print("[WARN] D1: âncora não encontrada")

    if not applied:
        print("[OK] Nada a aplicar")
        return 0

    if dry_run:
        import difflib
        diff = list(difflib.unified_diff(
            text.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile="dashboard.html (original)",
            tofile="dashboard.html (patched)",
            n=2
        ))
        print("".join(diff[:120]) if diff else "[DRY-RUN] Sem diferença")
        print(f"\n[DRY-RUN] Patches que seriam aplicados: {', '.join(applied)}")
        return 0

    bak = path.with_suffix(f".html.bak_{_sha(text[:500])}")
    bak.write_bytes(raw)
    print(f"[BACKUP] {bak.name}")

    out = patched.encode("utf-8")
    if crlf:
        out = out.replace(b"\n", b"\r\n")
    path.write_bytes(out)
    print(f"[PATCHED] {path.name} — {', '.join(applied)}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Dashboard D1+D2+D3 fix")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--file", default=str(TARGET))
    a = p.parse_args()
    sys.exit(apply_patch(Path(a.file), a.dry_run))


if __name__ == "__main__":
    main()
