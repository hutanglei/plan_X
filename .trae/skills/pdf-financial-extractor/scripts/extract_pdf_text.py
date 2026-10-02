#!/usr/bin/env python3
"""从中文财报 PDF（年报/半年报）提取全文文本，保留页码标记，供后续定位三张表。

用法：
    python3 extract_pdf_text.py <input.pdf> [output.txt]

输出：UTF-8 纯文本，每页前带 ===== 第N页 ===== 标记，行内保留原始空格与换行。
依赖：pymupdf（pip install pymupdf）。
"""
import sys
import os

def extract(pdf_path: str, out_path: str) -> None:
    try:
        import fitz  # pymupdf
    except ImportError:
        sys.exit("缺少依赖：pip install pymupdf")
    doc = fitz.open(pdf_path)
    with open(out_path, "w", encoding="utf-8") as f:
        for i, page in enumerate(doc):
            f.write(f"\n===== 第{i+1}页 =====\n")
            f.write(page.get_text())
    print(f"页数: {len(doc)}")
    print(f"输出: {out_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法: python3 extract_pdf_text.py <input.pdf> [output.txt]")
    pdf = sys.argv[1]
    if not os.path.isfile(pdf):
        sys.exit(f"文件不存在: {pdf}")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(pdf)[0] + ".txt"
    extract(pdf, out)
