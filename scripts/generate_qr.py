#!/usr/bin/env python3
"""Generate Feishu/Lark bot install QR code for Dragon Agent."""

import qrcode
from PIL import Image, ImageDraw, ImageFont
import sys, os

def generate_feishu_qr(app_id: str, output: str = "dragon_feishu_qr.png", domain: str = "feishu"):
    """Generate QR code for Feishu bot install."""
    DOMAINS = {
        "feishu": "applink.feishu.cn",
        "lark": "applink.larksuite.com",
    }
    base = DOMAINS.get(domain, DOMAINS["feishu"])
    url = f"https://{base}/client/bot/install?app_id={app_id}"
    
    print(f"URL: {url}")
    
    qr = qrcode.QRCode(
        version=3,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=12,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    
    # Make image with label below
    img = qr.make_image(fill_color="#3370FF", back_color="white").convert("RGB")
    w, h = img.size
    margin = 50
    new_h = h + margin + 30
    
    final = Image.new("RGB", (w, new_h), "white")
    final.paste(img, (0, 0))
    
    draw = ImageDraw.Draw(final)
    label = "Dragon Agent Bot"
    url_label = url[:55] + "..." if len(url) > 55 else url
    
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_url = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    except:
        font_title = ImageFont.load_default()
        font_url = ImageFont.load_default()
    
    # Center title
    bbox = draw.textbbox((0, 0), label, font=font_title)
    tw = bbox[2] - bbox[0]
    draw.text(((w - tw) // 2, h + 8), label, fill="#3370FF", font=font_title)
    
    # URL below
    bbox2 = draw.textbbox((0, 0), url_label, font=font_url)
    tw2 = bbox2[2] - bbox2[0]
    draw.text(((w - tw2) // 2, h + 35), url_label, fill="#888888", font=font_url)
    
    final.save(output)
    size_kb = os.path.getsize(output) / 1024
    print(f"Saved: {output} ({size_kb:.1f} KB)")
    return output

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python3 generate_qr.py <APP_ID> [output.png] [feishu|lark]")
        print()
        print("Steps to get your App ID:")
        print("  1. Open https://open.feishu.cn/app")
        print("  2. Create custom app -> Enable Bot")
        print("  3. Copy App ID from Credentials page")
        print("  4. Run: python3 generate_qr.py cli_xxxxxxxxxxxx")
        print()
        sys.exit(1)
    
    app_id = sys.argv[1]
    output = sys.argv[2] if len(sys.argv) > 2 else "dragon_feishu_qr.png"
    domain = sys.argv[3] if len(sys.argv) > 3 else "feishu"
    
    generate_feishu_qr(app_id, output, domain)
