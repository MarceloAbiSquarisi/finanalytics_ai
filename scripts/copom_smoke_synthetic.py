"""copom_smoke_synthetic.py — valida pipeline completo com dataset sintetico.

Gera ~60 docs artificiais (20 por classe) com frases tipicas COPOM,
treina BERTimbau em 1 epoch, faz inferencia e verifica que o modelo
aprende a separar as classes. NAO insere no DB.

Uso:
    python scripts/copom_smoke_synthetic.py
    python scripts/copom_smoke_synthetic.py --epochs 1 --skip-train

Objetivo: provar que o fluxo `fetch -> label -> finetune -> infer`
funciona com o stack instalado (torch+transformers+CUDA) antes de
operar com dados reais quando BCB voltar.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import random
import sys
import tempfile

HAWKISH_FRAGMENTS = [
    "A inflacao segue pressionada e exige postura mais restritiva do Comite.",
    "As expectativas desancoraram, justificando elevacao adicional da taxa basica.",
    "O balanco de riscos aponta para vieses altistas, demandando politica monetaria contracionista.",
    "A persistencia do hiato inflacionario requer manutencao de juros em patamar elevado por periodo prolongado.",
    "O Comite sinaliza novo ajuste para cima da Selic se o cenario nao convergir.",
    "O grau de aperto deve ser ampliado para assegurar a convergencia da inflacao.",
    "A desancoragem das expectativas justifica elevacao do juro real ex-ante.",
    "O Comite avalia que medidas adicionais de aperto podem ser necessarias.",
]
NEUTRAL_FRAGMENTS = [
    "O Comite decidiu manter a taxa basica de juros inalterada em 13,75%.",
    "A atual postura de politica monetaria mostra-se adequada ao cenario prospectivo.",
    "Dadas as incertezas domesticas e externas, a cautela e a parcimonia sao recomendaveis.",
    "O Comite segue monitorando os dados e reforca o compromisso com a convergencia.",
    "A trajetoria inflacionaria se encontra dentro do esperado.",
    "O Comite mantem a taxa Selic e acompanhara os desdobramentos.",
    "A perspectiva e de manutencao da taxa de juros pelo horizonte relevante.",
    "As projecoes permanecem em linha com a meta.",
]
DOVISH_FRAGMENTS = [
    "A inflacao em queda abre espaco para inicio de ciclo de flexibilizacao monetaria.",
    "O Comite decidiu reduzir a Selic em 50 pontos base, em resposta ao arrefecimento inflacionario.",
    "A atividade desacelera e o hiato do produto se torna negativo, justificando cortes adicionais.",
    "A convergencia das expectativas permite avancar no ciclo de flexibilizacao.",
    "O balanco de riscos se tornou mais benigno, favorecendo a reducao do juro real.",
    "O Comite sinaliza novos cortes se o cenario desinflacionario persistir.",
    "A desinflacao mais rapida que a esperada viabiliza um afrouxamento adicional.",
    "O Comite avalia espaco para reduzir a taxa basica nas proximas reunioes.",
]

BASE_TEXT = (
    "Reuniao ordinaria do COPOM. Apos avaliacao do cenario prospectivo, "
    "do balanco de riscos e do amplo conjunto de informacoes disponiveis, "
    "o Comite analisa a conjuntura atual. "
)


def gen(n: int, fragments: list[str], label: str, seed: int) -> list[dict]:
    rng = random.Random(seed)
    docs = []
    for i in range(n):
        picks = rng.sample(fragments, k=min(3, len(fragments)))
        text = BASE_TEXT + " ".join(picks) + (
            f" Observacao {i}: o Comite destaca que o cenario requer atencao continua."
        )
        docs.append({
            "doc_date": f"2024-{1 + (i%12):02d}-15",
            "doc_type": "minute" if i % 2 == 0 else "communique",
            "label":    label,
            "text":     text,
        })
    return docs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=20)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--skip-train", action="store_true")
    args = ap.parse_args()

    rows = (gen(args.per_class, HAWKISH_FRAGMENTS, "hawkish", 1)
            + gen(args.per_class, NEUTRAL_FRAGMENTS, "neutral", 2)
            + gen(args.per_class, DOVISH_FRAGMENTS,  "dovish",  3))
    print(f"synthetic dataset: {len(rows)} rows, {args.per_class} per class")

    tmp = Path(tempfile.mkdtemp(prefix="copom_smoke_"))
    csv_path = tmp / "labeled.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_date","doc_type","label","text"])
        w.writeheader()
        for r in rows: w.writerow(r)
    print(f"csv: {csv_path}")

    if args.skip_train:
        return 0

    # Invoca finetune como subprocess
    import subprocess
    root = Path(__file__).resolve().parent.parent
    python = Path(sys.executable)
    cmd = [str(python), str(root / "scripts" / "copom_finetune.py"),
           "--csv", str(csv_path),
           "--epochs", str(args.epochs),
           "--batch", str(args.batch),
           "--max-len", "256",
           "--out-name", f"copom_bert_smoke_{args.epochs}e"]
    print("\n=== FINETUNE ===")
    cp = subprocess.run(cmd, cwd=str(root), text=True)
    if cp.returncode != 0:
        print("finetune FAIL"); return 2

    # Inferencia em 3 textos novos (um por classe) para sanity
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    model_dir = root / "models" / f"copom_bert_smoke_{args.epochs}e"
    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device); model.eval()

    # Sanity usa vocabulario proximo das training fragments (proposito: validar
    # que modelo aprendeu os padroes-chave, nao generalizar para paraphrases).
    samples = [
        ("hawkish", "A inflacao segue pressionada e o Comite avalia que medidas adicionais "
                    "de aperto podem ser necessarias, com postura mais restritiva."),
        ("neutral", "O Comite decidiu manter a taxa basica inalterada e mantem acompanhamento "
                    "dos dados; a trajetoria inflacionaria esta dentro do esperado."),
        ("dovish",  "A desinflacao mais rapida que a esperada viabiliza um afrouxamento adicional; "
                    "o Comite sinaliza novos cortes se o cenario desinflacionario persistir."),
    ]
    print("\n=== SANITY INFER ===")
    import json
    meta = json.loads((model_dir / "copom_meta.json").read_text(encoding="utf-8"))
    labels = meta["labels"]
    hits = 0
    for expect, txt in samples:
        inp = tok(txt, return_tensors="pt", truncation=True, max_length=256).to(device)
        with torch.no_grad():
            p = torch.softmax(model(**inp).logits[0], dim=-1).cpu().numpy().tolist()
        top = labels[int(max(range(len(p)), key=lambda i: p[i]))]
        ok = top == expect
        hits += int(ok)
        print(f"  exp={expect:8} got={top:8} probs={ {labels[i]:round(p[i],3) for i in range(len(p))} }  {'OK' if ok else 'MISS'}")
    print(f"\nsanity hits: {hits}/3")
    return 0 if hits >= 2 else 1


if __name__ == "__main__":
    sys.exit(main())
