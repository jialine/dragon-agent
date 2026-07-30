# Happyhorse 1.1 视频生成 API（via andlapi.cn）

## 概述

Happyhorse 1.1 是 andlapi.cn 提供的视频生成模型，支持三种模式：
- **T2V**（文生视频）：纯文本提示词生成视频
- **R2V**（参考图生视频）：文本 + 角色/场景参考图
- **I2V**（图生视频）：以上一帧尾帧作为起点继续生成

**关键限制**：R2V/I2V 的 `ref_image` 必须是**公网可访问的 URL**。ComfyUI 出的本地图需先通过 SignOSS 上传到 OSS。

---

## API 端点

| 操作 | 方法 | URL |
|------|------|-----|
| 提交任务 | POST | `https://api.andlapi.cn/v1/video/generations` |
| 轮询任务 | GET | `https://api.andlapi.cn/task/{task_id}` |
| 上传参考图 | POST | `https://api.andlapi.cn/signoss/upload` |

**认证**：所有请求带 Header `Authorization: Bearer {API_KEY}`

---

## 一、T2V（文生视频）

### 请求体

```json
{
  "model": "happyhorse-1.1",
  "prompt": "...",
  "negative_prompt": "watermark, text, logo, happyhorse, subtitle, words, letters, brand, label, copyright, UI, overlay, signature",
  "num_frames": 81,
  "fps": 16,
  "logo": false,
  "aspect_ratio": "16:9",
  "image_size": "1280x720"
}
```

### 参数说明

| 参数 | 类型 | 说明 | 常用值 |
|------|------|------|--------|
| `model` | str | 模型名称 | `"happyhorse-1.1"` |
| `prompt` | str | 正向提示词（英文，200-800字符） | — |
| `negative_prompt` | str | 负面提示词 | 统一使用全局负面词 |
| `num_frames` | int | 总帧数 | `81`（约5秒@16fps） |
| `fps` | int | 帧率 | `16` |
| `logo` | bool | 是否加水印 | `false` |
| `aspect_ratio` | str | 画幅比例 | `"16:9"` |
| `image_size` | str | 分辨率 | `"1280x720"` |

### 时长计算

- `num_frames=81 / fps=16` → **~5秒**
- `num_frames=161 / fps=16` → **~10秒**
- `num_frames=241 / fps=16` → **~15秒**

---

## 二、R2V（参考图生视频）

### 请求体（在 T2V 基础上增加 `ref_image`）

```json
{
  "model": "happyhorse-1.1",
  "prompt": "...",
  "negative_prompt": "...",
  "num_frames": 81,
  "fps": 16,
  "logo": false,
  "aspect_ratio": "16:9",
  "image_size": "1280x720",
  "ref_image": "https://ossuploadimages.oss-cn-hangzhou.aliyuncs.com/DRAGON-DLLJEL5DMQHKAJM/characters/chenmo_ref.jpg"
}
```

使用场景：开场镜头提供角色定妆照确保一致性、场景概念图作为参考、道具/载具参考图。

---

## 三、I2V（图生视频，接上一帧）

请求体同 R2V，`ref_image` 为上一镜尾帧的公网 URL。用于同一场景内连续镜头的无缝转场和角色持续动作的接力生成。

---

## 四、提交 & 轮询

### 快速脚本

```bash
# T2V
python3 scripts/gen_video.py "prompt here" --model happyhorse-1.1-t2v

# 或直接调用 API
python3 scripts/happyhorse_api.py t2v "prompt here" --output /tmp/video.mp4
```

### Python 调用

```python
from scripts.happyhorse_api import HappyhorseAPI

api = HappyhorseAPI()

# T2V
task_id = api.submit(prompt="Underground lab corridor...")
result = api.poll(task_id, max_wait=600)
if result["status"] == "SUCCESS":
    api.download(result["url"], "output/video.mp4")

# R2V（需先上传参考图）
oss_url = api.upload_ref("characters/chenmo.png", category="characters")
task_id = api.submit(prompt="Chen Mo walking...", ref_image=oss_url)

# I2V（接上一帧）
task_id = api.submit(prompt="...continues walking...", ref_image=last_frame_url)
```

---

## 五、SignOSS 参考图上传

```bash
# 命令行上传
python3 scripts/happyhorse_api.py upload /path/to/image.png --category characters
```

```python
# Python
api.upload_ref("/path/to/image.png", category="characters")
# → "https://ossuploadimages.oss-cn-hangzhou.aliyuncs.com/DRAGON-DLLJEL5DMQHKAJM/characters/image.png"
```

### OSS 目录约定

```
DRAGON-DLLJEL5DMQHKAJM/
├── characters/       # 角色定妆照
├── scenes/           # 场景概念图
├── props/            # 道具/载具
└── frames/           # I2V 尾帧
```

---

## 六、全局负面提示词

```
watermark, text, logo, happyhorse, subtitle, words, letters, brand, label, copyright, UI element, overlay text, signature, username, channel name, low quality, blurry, jpeg artifacts, distorted, deformed
```

---

## 七、猩火(EMBER) 项目速查

| 项目 | 值 |
|------|-----|
| 分辨率 | `1280x720` |
| 画幅 | `16:9` |
| 帧率 | `16` fps |
| 默认帧数 | `81`（5秒）/ `161`（10秒） |
| 模型 | `happyhorse-1.1` |
| API Base | `https://api.andlapi.cn/v1/video/generations` |
| 轮询 | `https://api.andlapi.cn/task/{task_id}` |
| SignOSS | `https://api.andlapi.cn/signoss/upload` |

---

## 八、错误处理

| 错误 | 原因 | 解决 |
|------|------|------|
| `ref_image 不可访问` | 本地路径或私有 URL | 先用 SignOSS 上传获取公网 URL |
| `prompt 过长` | 超过模型限制 | 控制在 800 字符以内 |
| `TIMEOUT` | 队列拥堵 | 延长 max_wait 到 900s |
| `FAILED` | 内容安全拦截 | 检查提示词敏感词 |
