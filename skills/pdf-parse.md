# PDF 解析技能 (pdf-parse)

## 触发条件
- 用户上传/提到 PDF 文件，需要提取文字、表格、图片或元信息
- 关键词：PDF、解析、提取、读取、转换、合并、拆分

## 可用工具

| 工具 | 用途 | 速度 |
|------|------|------|
| `pdftotext` | 提取纯文本 | ⚡快 |
| `pdfinfo` | 获取元信息（页数、作者等） | ⚡快 |
| `python3 + PyMuPDF (fitz)` | 高级操作（图片/表格/注释/渲染） | 🐢慢 |

> 环境现状：PyMuPDF 1.27.2.3 已安装，pdftotext/pdfinfo 可用（poppler-utils）。

---

## 场景一：提取纯文本

```bash
# 基础提取（保留布局）
pdftotext -layout input.pdf output.txt

# 指定页码范围
pdftotext -f 1 -l 5 -layout input.pdf output.txt

# 直接输出到stdout（用于小文件直接读取）
pdftotext -layout input.pdf -
```

## 场景二：获取文档信息

```bash
pdfinfo input.pdf              # 基础信息：页数、作者、大小
pdfinfo -box input.pdf         # 含页面边界框
pdfinfo -meta input.pdf        # 含XML元数据
```

## 场景三：提取图片（PyMuPDF）

```python
import fitz
doc = fitz.open("input.pdf")
for page_num in range(len(doc)):
    page = doc[page_num]
    images = page.get_images(full=True)
    for i, img in enumerate(images):
        xref = img[0]
        pix = fitz.Pixmap(doc, xref)
        if pix.n < 5:  # GRAY or RGB
            pix.save(f"page{page_num+1}_img{i+1}.png")
        else:  # CMYK, 转RGB
            pix = fitz.Pixmap(fitz.csRGB, pix)
            pix.save(f"page{page_num+1}_img{i+1}.png")
doc.close()
```

## 场景四：提取表格

```python
import fitz
doc = fitz.open("input.pdf")
for page_num in range(len(doc)):
    page = doc[page_num]
    tables = page.find_tables()
    if tables:
        for t_idx, table in enumerate(tables):
            data = table.extract()
            print(f"=== Page {page_num+1}, Table {t_idx+1} ===")
            for row in data:
                print("\t".join([str(cell) for cell in row]))
doc.close()
```

## 场景五：逐页提取文本（带页码分隔）

```python
import fitz
doc = fitz.open("input.pdf")
for page_num in range(len(doc)):
    page = doc[page_num]
    text = page.get_text()
    print(f"\n{'='*40}")
    print(f"  PAGE {page_num+1} / {len(doc)}")
    print(f"{'='*40}\n")
    print(text)
doc.close()
```

## 场景六：搜索关键词定位

```python
import fitz
doc = fitz.open("input.pdf")
keyword = "KEYWORD"
for i, page in enumerate(doc):
    areas = page.search_for(keyword)
    if areas:
        print(f"Page {i+1}: found {len(areas)} occurrence(s)")
        for area in areas:
            text = page.get_text("text", clip=area)
            print(f"  -> '{text.strip()}' at {area}")
doc.close()
```

## 场景七：页面渲染为图片

```python
import fitz
doc = fitz.open("input.pdf")
# 渲染指定页为PNG（dpi控制清晰度）
page = doc[0]
pix = page.get_pixmap(dpi=200)
pix.save("page1.png")
doc.close()

# 批量渲染全部页面：
# for i, page in enumerate(doc):
#     page.get_pixmap(dpi=150).save(f"page_{i+1}.png")
```

## 场景八：PyMuPDF 提取元数据

```python
import fitz
doc = fitz.open("input.pdf")
meta = doc.metadata
print(f"标题: {meta.get('title', 'N/A')}")
print(f"作者: {meta.get('author', 'N/A')}")
print(f"主题: {meta.get('subject', 'N/A')}")
print(f"关键词: {meta.get('keywords', 'N/A')}")
print(f"创建工具: {meta.get('creator', 'N/A')}")
print(f"页数: {doc.page_count}")
print(f"是否加密: {doc.is_encrypted}")
print(f"PDF版本: {doc.pdf_version}")
doc.close()
```

## 场景九：合并多个PDF

```python
import fitz
result = fitz.open()
for pdf_path in ["file1.pdf", "file2.pdf", "file3.pdf"]:
    doc = fitz.open(pdf_path)
    result.insert_pdf(doc)
    doc.close()
result.save("merged.pdf")
result.close()
```

## 场景十：拆分PDF（提取指定页）

```python
import fitz
doc = fitz.open("input.pdf")
new_doc = fitz.open()
# 提取第1-3页和第5页
for page_num in [0, 1, 2, 4]:
    new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
new_doc.save("extracted.pdf")
new_doc.close()
doc.close()
```

---

## 常见坑

1. **扫描版PDF**：pdftotext 提取为空，需 OCR（按需安装 `apt install tesseract-ocr tesseract-ocr-chi-sim`）
2. **中文乱码**：pdftotext 默认 UTF-8；若 PDF 用系统字体（Helvetica）生成则中文无法提取，需 PDF 嵌入 CJK 字体
3. **大文件**：先用 `pdfinfo` 看页数，再用 `-f -l` 分批提取
4. **PyMuPDF 表格**：`find_tables()` 对无边框表格效果差，复杂表格可装 camelot
5. **加密PDF**：`doc.authenticate("password")` 解密
6. **pdftotext vs PyMuPDF**：pdftotext 快但只提文本；PyMuPDF 功能全。先用 pdftotext 探路，再按需用 PyMuPDF

## 标准工作流

```
1. pdfinfo input.pdf           → 了解文档全貌（页数/作者/加密状态）
2. pdftotext -layout ... -     → 快速查看文本内容（前几页即可）
3. 如需图片/表格/渲染 → PyMuPDF 脚本
4. 如需搜索关键词  → 场景六定位
```
