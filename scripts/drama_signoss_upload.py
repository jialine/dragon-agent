#!/usr/bin/env python3
"""drama_signoss_upload.py — 上传角色/场景参考图到 SignOSS"""

import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from drama_common import *

def main():
    p = arg_parser("上传资产到SignOSS")
    p.add_argument("--assets", required=True, help="05_assets 目录")
    p.add_argument("--manifest", required=True, help="分镜表 YAML")
    p.add_argument("--output", required=True, help="输出 storyboard_oss.yaml")
    args = p.parse_args()

    if not SIGNOSS_API_KEY or not SIGNOSS_BASE:
        print("⚠️ SignOSS 未配置，跳过上传（保留本地路径）")
        # 直接复制
        import shutil
        shutil.copy(args.manifest, args.output)
        return

    assets_dir = Path(args.assets)
    storyboard = yaml.safe_load(read(args.manifest))
    project = Path(args.workspace).name

    # 扫描所有图片
    image_map = {}  # 本地路径 → OSS URL
    for ext in ["*.jpg", "*.png", "*.jpeg", "*.webp"]:
        for img in assets_dir.rglob(ext):
            rel = str(img.relative_to(Path(args.workspace)))
            try:
                url = signoss_upload(str(img), f"dramas/{project}/{rel}")
                image_map[str(img)] = url
                image_map[rel] = url
                print(f"  📤 {rel} → OSS")
            except Exception as e:
                print(f"  ⚠️ {rel}: {e}")

    print(f"  上传 {len(image_map)} 个文件")

    # 替换 storyboard 中的图片引用
    def replace_refs(obj):
        if isinstance(obj, str):
            for local, oss in image_map.items():
                if local in obj:
                    obj = obj.replace(local, oss)
            return obj
        elif isinstance(obj, dict):
            return {k: replace_refs(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [replace_refs(v) for v in obj]
        return obj

    updated = replace_refs(storyboard)
    write(args.output, yaml.dump(updated, allow_unicode=True, default_flow_style=False))
    print(f"  ✅ → {args.output}")

if __name__ == "__main__":
    main()
