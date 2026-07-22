# 视频生成能力

你可以调用 `scripts/gen_video.py` 生成短视频。

## 用法

```bash
# 快乐马（便宜快速，¥0.61/次）
python3 scripts/gen_video.py "一只猫在花园里玩耍" --model happyhorse-1.1-t2v

# 万相2.7（高质量，¥15/次）
python3 scripts/gen_video.py "赛博朋克城市夜景" --model wan2.7-t2v --size 1920*1080
```

## 参数

- `prompt`：文本描述（必填，支持中文）
- `--model`：happyhorse-1.1-t2v（默认）/ wan2.7-t2v
- `--size`：分辨率，默认 1280*720
- `--output`：自定义输出路径

## 返回

脚本会等待任务完成（约1-2分钟），最后一行输出本地视频文件路径。
