# fix_sse_dollar.py
# Corrige o R\$ invalido no bloco SSE do dashboard.html

path = r"D:\Projetos\finanalytics_ai_fresh\src\finanalytics_ai\interfaces\api\static\dashboard.html"

with open(path, encoding="utf-8") as f:
    content = f.read()

# O problema: o bloco SSE foi escrito com R\$ que e invalido em JS
# Precisa ser apenas R$ (sem barra)
before = content.count("R\\$ ")
content_fixed = content.replace("R\\$ ", "R$ ")
after = content_fixed.count("R\\$ ")

print(f"Substituicoes feitas: {before - after}")

with open(path, "w", encoding="utf-8") as f:
    f.write(content_fixed)

# Valida a linha corrigida
lines = content_fixed.splitlines()
for i, l in enumerate(lines[3046:3053], start=3047):
    if "fmtPrice" in l or "toLocale" in l or "return" in l:
        print(f"{i}: {l}")
