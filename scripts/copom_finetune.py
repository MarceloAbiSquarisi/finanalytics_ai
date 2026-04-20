"""copom_finetune.py — fine-tune BERTimbau para classificacao de sentimento
(hawkish/neutral/dovish) sobre atas e comunicados COPOM.

Le CSV gerado por copom_label_selic.py ou equivalente (cols: doc_date,
doc_type, label, text). Retorna modelo salvo em models/copom_bert_*/.

Uso:
    python scripts/copom_finetune.py --csv data/copom/labeled.csv
    python scripts/copom_finetune.py --csv ... --epochs 3 --batch 8

Requisitos: torch+CUDA, transformers, datasets. GPU recomendada
(CPU treina, mas demora ~30min por epoch em 100 docs).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path


LABELS = ["dovish", "neutral", "hawkish"]
L2I    = {l: i for i, l in enumerate(LABELS)}
I2L    = {i: l for l, i in L2I.items()}
MODEL_BASE = "neuralmind/bert-base-portuguese-cased"


def _load_model_with_layernorm_rename(model_base: str, n_labels: int):
    """Workaround para BERTimbau (checkpoint TF-style com beta/gamma)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoConfig
    from huggingface_hub import snapshot_download

    local = snapshot_download(repo_id=model_base)
    sd_path = Path(local) / "pytorch_model.bin"
    if not sd_path.exists():
        # safetensors ja tem naming correto; fallback padrao
        return AutoModelForSequenceClassification.from_pretrained(
            model_base, num_labels=n_labels, id2label=I2L, label2id=L2I,
        )

    state_dict = torch.load(str(sd_path), map_location="cpu", weights_only=True)
    renamed = {}
    for k, v in state_dict.items():
        nk = k.replace(".LayerNorm.gamma", ".LayerNorm.weight") \
              .replace(".LayerNorm.beta", ".LayerNorm.bias")
        renamed[nk] = v

    cfg = AutoConfig.from_pretrained(model_base, num_labels=n_labels,
                                     id2label=I2L, label2id=L2I)
    model = AutoModelForSequenceClassification.from_config(cfg)
    missing, unexpected = model.load_state_dict(renamed, strict=False)
    # Missing: classifier weights (esperado). Unexpected deveria ser vazio.
    expected_missing_prefixes = ("classifier.", "bert.pooler.")
    critical_missing = [k for k in missing
                        if not any(k.startswith(p) for p in expected_missing_prefixes)]
    if critical_missing:
        print(f"WARN missing criticos: {critical_missing[:5]}...")
    if unexpected:
        print(f"WARN unexpected: {unexpected[:5]}...")
    return model


def read_csv(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            lbl = r.get("label", "").strip().lower()
            txt = (r.get("text") or "").strip()
            if lbl not in L2I or not txt:
                continue
            rows.append({
                "doc_date": r.get("doc_date"),
                "doc_type": r.get("doc_type"),
                "label":    L2I[lbl],
                "text":     txt,
            })
    return rows


def split_rows(rows: list[dict], val_pct: float = 0.15, seed: int = 42):
    random.Random(seed).shuffle(rows)
    n_val = max(1, int(len(rows) * val_pct))
    return rows[:-n_val], rows[-n_val:]


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--model-base", default=MODEL_BASE)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--val-pct", type=float, default=0.15)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-name", default="")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"csv nao existe: {csv_path}", file=sys.stderr); return 2

    rows = read_csv(csv_path)
    if len(rows) < 10:
        print(f"poucos rows validos: {len(rows)}"); return 2

    random.seed(args.seed)
    train_rows, val_rows = split_rows(rows, args.val_pct, args.seed)
    print(f"total={len(rows)}  train={len(train_rows)}  val={len(val_rows)}")
    print(f"dist label train: {count_labels(train_rows)}")
    print(f"dist label val  : {count_labels(val_rows)}")

    # imports aqui p/ facilitar --help sem torch carregado
    import torch
    import numpy as np
    from datasets import Dataset
    from transformers import (
        AutoTokenizer, AutoModelForSequenceClassification,
        Trainer, TrainingArguments, DataCollatorWithPadding,
    )

    print(f"torch={torch.__version__} cuda={torch.cuda.is_available()}")
    device = "cuda" if torch.cuda.is_available() else "cpu"

    tok = AutoTokenizer.from_pretrained(args.model_base)
    # BERTimbau pretrained checkpoint usa naming antigo (beta/gamma) para LayerNorm.
    # transformers 5.x nao renomeia auto -> fazemos o rename ANTES do load.
    model = _load_model_with_layernorm_rename(args.model_base, len(LABELS))

    def tokenize(ex):
        return tok(ex["text"], truncation=True, max_length=args.max_len)

    ds_train = Dataset.from_list(train_rows).map(tokenize, batched=True)
    ds_val   = Dataset.from_list(val_rows).map(tokenize, batched=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = args.out_name or f"copom_bert_{ts}"
    out_dir = Path(__file__).resolve().parent.parent / "models" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    def compute_metrics(eval_pred):
        preds = eval_pred.predictions.argmax(axis=-1)
        labels = eval_pred.label_ids
        acc = float((preds == labels).mean())
        # macro F1
        from collections import Counter
        per_class_f1 = []
        for c in range(len(LABELS)):
            tp = int(((preds == c) & (labels == c)).sum())
            fp = int(((preds == c) & (labels != c)).sum())
            fn = int(((preds != c) & (labels == c)).sum())
            prec = tp / (tp + fp) if (tp + fp) else 0.0
            rec  = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2*prec*rec/(prec+rec) if (prec+rec) else 0.0
            per_class_f1.append(f1)
        return {"accuracy": acc, "macro_f1": float(np.mean(per_class_f1))}

    args_tr = TrainingArguments(
        output_dir=str(out_dir / "training"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        learning_rate=args.lr,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=1,
        load_best_model_at_end=True,
        metric_for_best_model="macro_f1",
        logging_steps=max(1, len(ds_train) // (args.batch * 4)),
        report_to=[],
        fp16=torch.cuda.is_available(),
        seed=args.seed,
    )

    trainer = Trainer(
        model=model, args=args_tr,
        train_dataset=ds_train, eval_dataset=ds_val,
        processing_class=tok, data_collator=DataCollatorWithPadding(tok),
        compute_metrics=compute_metrics,
    )
    trainer.train()
    eval_m = trainer.evaluate()
    print("eval:", eval_m)

    # Save final model
    trainer.save_model(str(out_dir))
    tok.save_pretrained(str(out_dir))
    meta = {
        "base": args.model_base, "trained_at": ts,
        "num_samples": len(rows), "train_size": len(train_rows),
        "val_size": len(val_rows),
        "labels": LABELS, "label2id": L2I,
        "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
        "max_len": args.max_len, "metrics": eval_m,
    }
    (out_dir / "copom_meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8",
    )
    print(f"modelo salvo em: {out_dir}")
    return 0


def count_labels(rows: list[dict]) -> dict[str, int]:
    cnt = {l: 0 for l in LABELS}
    for r in rows:
        cnt[I2L[r["label"]]] += 1
    return cnt


if __name__ == "__main__":
    sys.exit(main())
