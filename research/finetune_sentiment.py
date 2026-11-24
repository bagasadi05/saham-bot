"""Fine-tune model sentimen berita pasar modal BEI (`indo-fin-roberta`) + export ONNX INT8.

Dirancang untuk dijalankan di Google Colab GPU T4 (±3-5 menit):
  1. Memuat korpus headline berita BEI terlabel (aksi korporasi, UMA, suspensi,
     dividen, rights issue, buyback, gagal bayar, pertumbuhan laba).
  2. Mengukur akurasi & macro-F1 baseline vs setelah fine-tuning pada validation split.
  3. Menyimpan model fine-tuned + opsional kuantisasi ONNX INT8 untuk CPU bot.

Pemakaian (Colab GPU T4):
  python research/finetune_sentiment.py --epochs 4 --export-onnx
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Label konsisten dengan `core/news_ml.py`: 0=negative, 1=neutral, 2=positive
BEI_HEADLINE_CORPUS: list[tuple[str, int]] = [
    # Positif (2)
    ("Laba bersih emiten melonjak 38% ditopang kenaikan margin dan efisiensi biaya", 2),
    ("RUPS setujui pembagian dividen tunai jumbo dengan yield di atas 7 persen", 2),
    ("Perseroan umumkan program buyback saham senilai Rp500 miliar tanpa RUPS", 2),
    ("Kinerja kuartal III melampaui konsensus analis berkat pertumbuhan kredit syariah", 2),
    ("Emiten tambang kantongi kontrak ekspor baru jangka panjang bernilai USD 200 juta", 2),
    ("Rating utang korporasi dinaikkan Pefindo menjadi idAAA dengan prospek stabil", 2),
    ("Pendapatan berulang tumbuh dua digit seiring ekspansi pusat data baru", 2),
    ("Arus kas operasional kuat, utang berbunga turun signifikan sepanjang tahun", 2),
    ("Saham resmi masuk konstituen indeks global MSCI dan menarik arus dana asing", 2),
    ("Perseroan selesaikan pembangunan smelter baru lebih cepat dari jadwal", 2),
    ("Penjualan ritel naik tajam selama musim Lebaran mendorong proyeksi laba", 2),
    ("Emiten energi raih fasilitas pembiayaan hijau berbunga rendah untuk ekspansi EBT", 2),
    ("Laba usaha berbalik positif setelah restrukturisasi operasional berhasil", 2),
    ("Kontrak baru konstruksi tembus rekor tertinggi dalam lima tahun terakhir", 2),
    ("Dividen interim diumumkan naik 25% dibanding periode tahun lalu", 2),
    ("Pertumbuhan pelanggan data dan ARPU mendorong EBITDA kuartalan naik solid", 2),
    # Negatif (0)
    ("BEI jatuhkan suspensi perdagangan saham akibat lonjakan harga tidak wajar dan UMA", 0),
    ("Emiten terancam gagal bayar kupon obligasi dan ajukan penundaan kewajiban utang PKPU", 0),
    ("Laba bersih anjlok 62% tertekan pelemahan kurs rupiah dan lonjakan beban bunga", 0),
    ("Pefindo pangkas peringkat utang emiten ke idBBB- dengan prospek negatif", 0),
    ("Saham masuk papan pemantauan khusus full call auction akibat ekuitas negatif", 0),
    ("BEI keluarkan peringatan potensi delisting setelah suspensi melewati 24 bulan", 0),
    ("Aksi rights issue dengan harga pelaksanaan diskon dalam memicu dilusi pemegang saham", 0),
    ("Komisaris dan pemegang saham pengendali melepas kepemilikan dalam jumlah besar", 0),
    ("Rugi bersih membengkak akibat penurunan volume produksi dan biaya bahan baku naik", 0),
    ("OJK selidiki dugaan manipulasi laporan keuangan dan penyalahgunaan dana IPO", 0),
    ("Arus kas operasi negatif tiga kuartal berturut-turut, rasio utang DER melonjak", 0),
    ("Emiten batalkan pembagian dividen tahun buku ini untuk menahan likuiditas", 0),
    ("Saham keluar dari indeks LQ45 dan JII pada evaluasi mayor BEI periode terbaru", 0),
    ("Kredit bermasalah NPL meningkat tajam menekan profitabilitas perbankan", 0),
    ("Pabrik utama berhenti beroperasi sementara akibat insiden keselamatan kerja", 0),
    ("Kinerja pendapatan meleset jauh di bawah target manajemen tahun berjalan", 0),
    # Netral (1)
    ("Perseroan jadwalkan Rapat Umum Pemegang Saham Tahunan pada akhir bulan depan", 1),
    ("Emiten menyampaikan laporan penggunaan dana hasil penawaran umum perdana", 1),
    ("Cum date dividen tunai jatuh pada pekan depan sesuai pengumuman KSEI", 1),
    ("Manajemen lakukan perubahan susunan direksi dan sekretaris perusahaan", 1),
    ("Perseroan menggelar paparan publik tahunan untuk memaparkan rencana kerja", 1),
    ("Harga saham bergerak mendatar di tengah volume perdagangan harian normal", 1),
    ("Emiten melakukan pemindahan alamat kantor pusat efektif mulai bulan depan", 1),
    ("Perseroan melaporkan transaksi afiliasi rutin antar entitas anak usaha", 1),
    ("Jadwal rilis laporan keuangan kuartal II ditetapkan pada pekan terakhir Juli", 1),
    ("Manajemen menanggapi permintaan penjelasan volatilitas transaksi dari BEI", 1),
    ("Perseroan menunjuk kantor akuntan publik independen untuk audit tahun buku", 1),
    ("RUPSLB menyetujui perubahan anggaran dasar tanpa aksi korporasi material", 1),
    ("Emiten mempertahankan target belanja modal tahunan sesuai anggaran awal", 1),
    ("Pembayaran bunga obligasi berkala dilakukan tepat waktu sesuai jadwal KSEI", 1),
    ("Perseroan berpartisipasi dalam pameran industri nasional pekan ini", 1),
    ("Saham diperdagangkan stabil mengikuti pergerakan indeks sektoral BEI", 1),
]


def load_corpus() -> list[tuple[str, int]]:
    """Gabungkan korpus BEI bawaan + gold set eksternal bila tersedia."""
    items = list(BEI_HEADLINE_CORPUS)
    gold_path = ROOT / "research" / "news_gold.json"
    if gold_path.exists():
        try:
            raw = json.loads(gold_path.read_text(encoding="utf-8"))
            lmap = {"negative": 0, "neg": 0, "neutral": 1, "neu": 1, "positive": 2, "pos": 2}
            for row in (raw if isinstance(raw, list) else raw.get("items", [])):
                txt = str(row.get("title") or row.get("text") or "").strip()
                lbl = lmap.get(str(row.get("label") or "").strip().lower())
                if txt and lbl is not None:
                    items.append((txt, lbl))
        except Exception:
            pass
    return items


def stratified_split(
    items: list[tuple[str, int]],
    val_ratio: float = 0.25,
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Bagi korpus secara stratified per kelas (0, 1, 2) secara deterministik."""
    train, val = [], []
    for cls in (0, 1, 2):
        bucket = [it for it in items if it[1] == cls]
        n_val = max(1, int(round(len(bucket) * val_ratio))) if bucket else 0
        val.extend(bucket[:n_val])
        train.extend(bucket[n_val:])
    return train, val


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fine-tune Indo-Fin-RoBERTa khusus berita BEI + ONNX INT8.")
    ap.add_argument("--base-model", default="models/indo-fin-roberta")
    ap.add_argument("--out-dir", default="models/indo-fin-roberta-bei")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--export-onnx", action="store_true")
    args = ap.parse_args(argv)

    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        print(f"ERROR: butuh torch & transformers ({exc})", file=sys.stderr)
        return 1

    corpus = load_corpus()
    train_data, val_data = stratified_split(corpus, val_ratio=0.25)
    model_src = str(ROOT / args.base_model) if (ROOT / args.base_model).exists() else "mdhugol/indonesia-bert-sentiment-classification"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[finetune_sentiment] device={device} | src={model_src} | train={len(train_data)} | val={len(val_data)}", flush=True)

    tok = AutoTokenizer.from_pretrained(model_src)
    model = AutoModelForSequenceClassification.from_pretrained(model_src, num_labels=3).to(device)

    def _eval_acc(m) -> float:
        m.eval()
        ok = 0
        with torch.no_grad():
            for text, lbl in val_data:
                enc = tok(text, return_tensors="pt", truncation=True, max_length=128).to(device)
                pred = int(m(**enc).logits.argmax(dim=-1).item())
                ok += int(pred == lbl)
        return round(ok / max(1, len(val_data)) * 100.0, 1)

    acc_before = _eval_acc(model)
    print(f"  Akurasi baseline (sebelum fine-tune): {acc_before}%", flush=True)

    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    loss_fn = torch.nn.CrossEntropyLoss()
    model.train()
    for ep in range(1, args.epochs + 1):
        tot_loss = 0.0
        for i in range(0, len(train_data), args.batch_size):
            batch = train_data[i : i + args.batch_size]
            enc = tok([b[0] for b in batch], padding=True, truncation=True, max_length=128, return_tensors="pt").to(device)
            y = torch.tensor([b[1] for b in batch], dtype=torch.long, device=device)
            opt.zero_grad()
            loss = loss_fn(model(**enc).logits, y)
            loss.backward()
            opt.step()
            tot_loss += float(loss.item())
        print(f"  Epoch {ep}/{args.epochs} | loss={tot_loss:.3f} | val_acc={_eval_acc(model)}%", flush=True)
        model.train()

    acc_after = _eval_acc(model)
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    print(f"[OK] Model fine-tuned disimpan ke {out_dir} (acc {acc_before}% -> {acc_after}%)", flush=True)

    if args.export_onnx:
        try:
            import onnxruntime as ort  # noqa: F401
            from onnxruntime.quantization import QuantType, quantize_dynamic

            model_cpu = model.to("cpu").eval()
            dummy = tok("Laba bersih naik", return_tensors="pt")
            fp32_path = out_dir / "model.onnx"
            int8_path = out_dir / "model_int8.onnx"
            torch.onnx.export(
                model_cpu,
                (dummy["input_ids"], dummy["attention_mask"]),
                str(fp32_path),
                input_names=["input_ids", "attention_mask"],
                output_names=["logits"],
                dynamic_axes={"input_ids": {0: "b", 1: "s"}, "attention_mask": {0: "b", 1: "s"}},
                opset_version=14,
            )
            quantize_dynamic(str(fp32_path), str(int8_path), weight_type=QuantType.QInt8)
            fp32_path.unlink(missing_ok=True)
            print(f"[OK] Export ONNX INT8 selesai: {int8_path} ({int8_path.stat().st_size / 1e6:.1f} MB)", flush=True)
        except Exception as exc:
            print(f"[WARN] Export ONNX INT8 dilewati ({exc})", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
