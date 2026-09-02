#!/usr/bin/env python3
"""三国抖音推广 — 名场面素材批量提交（R2V，DashScope 格式）
4条镜头：
  1. 王铁柱破庙穿越醒来（单人：王铁柱）
  2. 王铁柱给刘关张讲需求文档（四人：王铁柱+刘关张）
  3. 张飞卖猪肉（单人/双人：张飞）
  4. 桃园结义（四人）
用法:
  python3 submit_sg_promo.py            # 提交全部
  python3 submit_sg_promo.py --submit-only   # 仅提交，打印 task_id
"""
import sys, os, json, time, requests, urllib3, argparse
urllib3.disable_warnings()

sys.path.insert(0, "/root/dragon-agent/scripts")
from happyhorse_api import HappyhorseAPI
api = HappyhorseAPI()
API_KEY = api.api_key

ANDLAPI_URL = "https://api.andlapi.cn/v1/video/generations"
OUT_DIR = "/root/.hermes/promo_videos/sg"

URLS = json.load(open("/tmp/sg_ref_urls.json"))
WTZ = URLS["wang_tiezhu"]
GY  = URLS["guan_yu"]
LB  = URLS["liu_bei"]
ZF  = URLS["zhang_fei"]

# 竖屏 9:16，1080P，用于抖音
RESOLUTION = "1080P"
RATIO = "9:16"

SHOTS = [
    {
        "id": "shot01_wake",
        "model": "happyhorse-1.1-r2v",
        "duration": 8,
        "refs": [WTZ],
        "prompt": "王铁柱穿着粗麻布衣，猛地从破庙草堆上惊醒坐起，惊恐慌张地环顾四周，惨白月光从破洞的屋顶漏下，身后一尊泥菩萨半张脸慈眉善目半张脸被雨水泡得坑坑洼洼，他低头看自己粗糙的手，满脸不可置信",
    },
    {
        "id": "shot02_meeting",
        "model": "happyhorse-1.1-r2v",
        "duration": 10,
        "refs": [WTZ, LB, GY, ZF],
        "prompt": "古代军营大帐内，王铁柱站在一块木板前指着一幅简陋的地图讲解，刘备关羽张飞三人围坐在旁神情专注，王铁柱嘴里说着需求文档闭环赋能颗粒度的话，张飞一脸困惑挠头，烛火摇曳",
    },
    {
        "id": "shot03_pork",
        "model": "happyhorse-1.1-r2v",
        "duration": 9,
        "refs": [ZF],
        "prompt": "三国时期热闹集市，张飞站在自家的猪肉铺前，粗豪地挥舞着手臂招呼客人，案板上摆着新鲜的猪肉，铺子上挂着一块新招牌，张飞满脸得意，周围百姓围观",
    },
    {
        "id": "shot04_oath",
        "model": "happyhorse-1.1-r2v",
        "duration": 10,
        "refs": [WTZ, LB, GY, ZF],
        "prompt": "桃园里桃花盛开，四人跪拜结义，刘备居中，关羽张飞在两侧，王铁柱站在一旁抱拳，四人神情庄重，香案上香烛缭绕，落英缤纷，电影感大片构图",
    },
]


def submit(shot):
    ref_tags = "，".join(f"[Image {i+1}]" for i in range(len(shot["refs"])))
    dashscope_prompt = f"{ref_tags}中的场景，{shot['prompt']}"
    body = {
        "model": shot["model"],
        "input": {
            "prompt": dashscope_prompt,
            "media": [{"type": "reference_image", "url": u} for u in shot["refs"]],
        },
        "parameters": {
            "resolution": RESOLUTION,
            "ratio": RATIO,
            "duration": shot["duration"],
            "watermark": False,
        },
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    r = requests.post(ANDLAPI_URL, headers=headers, json=body, timeout=60, verify=False)
    if r.status_code != 200:
        print(f"  ❌ {shot['id']} HTTP {r.status_code}: {r.text[:200]}")
        return None
    data = r.json()
    tid = data.get("task_id") or data.get("id")
    print(f"  ✅ {shot['id']} -> {tid}")
    return tid


def wait_download(task_id, out_path, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{ANDLAPI_URL}/{task_id}",
                             headers={"Authorization": f"Bearer {API_KEY}"},
                             timeout=15, verify=False)
            d = r.json()
            data = d.get("data", d)
            status = data.get("status", d.get("status", ""))
            url = data.get("result_url", "") or data.get("url", "") or d.get("url", "")
            fail = data.get("fail_reason", "") or d.get("error", "")
        except Exception as e:
            time.sleep(12); continue
        if status in ("SUCCESS", "SUCCEEDED", "COMPLETED", "succeeded", "DONE"):
            if url:
                r2 = requests.get(url, timeout=120, verify=False)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with open(out_path, "wb") as f:
                    f.write(r2.content)
                print(f"  📥 {out_path} ({len(r2.content)/1024/1024:.1f}MB)")
                return True
            print(f"  ⚠️ SUCCESS but no url: {fail}")
            return False
        elif status in ("FAILED", "FAILURE", "CANCELED", "failed"):
            print(f"  ❌ FAILED: {fail}")
            return False
        time.sleep(12)
    print("  ⚠️ TIMEOUT")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit-only", action="store_true")
    ap.add_argument("--skip", default="", help="逗号分隔的镜头id，跳过")
    args = ap.parse_args()

    skip = set(x.strip() for x in args.skip.split(",") if x.strip())
    tasks = []
    for shot in SHOTS:
        if shot["id"] in skip:
            continue
        tid = submit(shot)
        if tid:
            tasks.append((shot, tid))
        time.sleep(2)

    if args.submit_only:
        print(json.dumps([{"id": s["id"], "task_id": t} for s, t in tasks], ensure_ascii=False, indent=2))
        return

    for shot, tid in tasks:
        out = os.path.join(OUT_DIR, f"{shot['id']}.mp4")
        wait_download(tid, out)


if __name__ == "__main__":
    main()