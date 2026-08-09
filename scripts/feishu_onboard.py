#!/usr/bin/env python3
"""
Dragon Agent — Feishu Bot 一键创建
==================================
用法: python3 scripts/feishu_onboard.py

通过 Feishu 设备码 OAuth 流程，扫码即可自动创建飞书机器人应用。
生成 QR 码 → 手机飞书扫码 → 自动获得 App ID + Secret → 写入 .env。

参考: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/authen-v1/authen/device-flow
"""
from __future__ import annotations

import json
import os
import sys
import time
import textwrap
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ── 配置 ──────────────────────────────────────────────────────
ACCOUNTS_BASE = "https://accounts.feishu.cn"  # 飞书中国区
# ACCOUNTS_BASE = "https://accounts.larksuite.com"  # Lark 国际版
REGISTRATION_PATH = "/oauth/v1/app/registration"
POLL_INTERVAL = 5  # 轮询间隔（秒）
EXPIRE_SECONDS = 600  # 设备码有效期

# ── 颜色 ──────────────────────────────────────────────────────
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
RED = "\033[0;31m"
BOLD = "\033[1m"
NC = "\033[0m"


def post(url: str, data: dict) -> dict:
    """HTTP POST with form-encoded body."""
    body = urlencode(data).encode()
    req = Request(url, data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        return json.loads(e.read().decode())


def generate_qr_terminal(url: str) -> str:
    """Generate ASCII QR code in terminal (fallback when qrcode lib not available)."""
    # Simple terminal-friendly display
    width = 60
    return textwrap.dedent(f"""
    {BOLD}╔══════════════════════════════════════════════════════════╗{NC}
    {BOLD}║  请用飞书 APP 扫描以下二维码完成授权                    ║{NC}
    {BOLD}╠══════════════════════════════════════════════════════════╣{NC}
    {BOLD}║                                                          ║{NC}
    {BOLD}║   🔗 扫码链接:                                          ║{NC}
    {CYAN}║   {url}{NC}
    {BOLD}║                                                          ║{NC}
    {BOLD}╚══════════════════════════════════════════════════════════╝{NC}
    """)


def display_qr(verification_url: str, user_code: str):
    """Display QR code — tries qrcode lib, falls back to ASCII."""
    try:
        import qrcode
        # Generate QR image
        qr = qrcode.QRCode(
            version=3,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=4,
        )
        qr.add_data(verification_url)
        qr.make(fit=True)

        # Save PNG
        qr_path = Path.home() / ".dragon" / "feishu_onboard_qr.png"
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(str(qr_path))

        # Also print ASCII
        qr.print_ascii(invert=True)

        print(f"\n{GREEN}✓ QR 码已保存: {qr_path}{NC}")
        print(f"{CYAN}  也可以在飞书 App 中直接打开链接:{NC}")
        print(f"  {verification_url}")
    except ImportError:
        print(generate_qr_terminal(verification_url))
        print(f"  {YELLOW}💡 安装 qrcode 可显示图形二维码: pip install qrcode[pil]{NC}")
    
    print(f"\n  {YELLOW}⏱  用户码: {BOLD}{user_code}{NC}")
    print(f"  {YELLOW}⏱  有效期: {EXPIRE_SECONDS // 60} 分钟{NC}")


def write_env(app_id: str, app_secret: str, domain: str = "feishu"):
    """Write or update Feishu credentials to .env file."""
    # Try multiple locations
    env_paths = [
        Path.home() / ".dragon" / ".env",  # Dragon's dotenv path
        Path.home() / "dragon-agent" / ".env",  # Project .env
    ]
    
    for env_path in env_paths:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        
        lines = []
        if env_path.exists():
            lines = env_path.read_text().splitlines()
        
        new_lines = []
        updated = {"FEISHU_APP_ID": False, "FEISHU_APP_SECRET": False}
        
        for line in lines:
            if line.startswith("FEISHU_APP_ID="):
                new_lines.append(f"FEISHU_APP_ID={app_id}")
                updated["FEISHU_APP_ID"] = True
            elif line.startswith("FEISHU_APP_SECRET="):
                new_lines.append(f"FEISHU_APP_SECRET={app_secret}")
                updated["FEISHU_APP_SECRET"] = True
            elif line.startswith("FEISHU_DOMAIN="):
                new_lines.append(f"FEISHU_DOMAIN={domain}")
            elif line.startswith("FEISHU_CONNECTION_MODE="):
                new_lines.append("FEISHU_CONNECTION_MODE=websocket")
            else:
                new_lines.append(line)
        
        if not updated["FEISHU_APP_ID"]:
            new_lines.append(f"FEISHU_APP_ID={app_id}")
        if not updated["FEISHU_APP_SECRET"]:
            new_lines.append(f"FEISHU_APP_SECRET={app_secret}")
        
        # Ensure FEISHU_DOMAIN and FEISHU_CONNECTION_MODE exist
        if not any(l.startswith("FEISHU_DOMAIN=") for l in new_lines):
            new_lines.append(f"FEISHU_DOMAIN={domain}")
        if not any(l.startswith("FEISHU_CONNECTION_MODE=") for l in new_lines):
            new_lines.append("FEISHU_CONNECTION_MODE=websocket")
        
        env_path.write_text("\n".join(new_lines) + "\n")
        print(f"  {GREEN}✓ 已写入: {env_path}{NC}")


def configure_permissions(app_id: str, app_secret: str) -> dict:
    """
    通过 API 自动配置权限和事件订阅。
    
    注意：部分权限（如 im:message）需要企业管理员审批。
    API 调用会请求这些权限，但最终生效可能需要管理员在飞书后台审批。
    
    Returns: {"permissions": bool, "events": bool, "published": bool}
    """
    result = {"permissions": False, "events": False, "published": False}
    
    # Step 1: 获取 tenant_access_token
    token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    token_data = json.dumps({
        "app_id": app_id,
        "app_secret": app_secret,
    }).encode()
    req = Request(token_url, data=token_data,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            token_resp = json.loads(resp.read().decode())
            if token_resp.get("code") != 0:
                print(f"  {YELLOW}⚠ 获取 Token 失败: {token_resp}{NC}")
                return result
            token = token_resp["tenant_access_token"]
    except Exception as e:
        print(f"  {YELLOW}⚠ Token 请求失败: {e}{NC}")
        return result
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    
    # Step 2: 授权权限
    scopes = [
        "im:message",
        "im:message.p2p",
        "im:message:readonly",
        "im:message.group_at_msg",
        "im:chat",
        "im:chat:readonly",
    ]
    
    perm_url = f"https://open.feishu.cn/open-apis/security/v1/app_perm_authorize/scopes"
    perm_data = json.dumps({"scopes": scopes}).encode()
    req = Request(perm_url, data=perm_data, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            perm_resp = json.loads(resp.read().decode())
            if perm_resp.get("code") == 0:
                result["permissions"] = True
                print(f"  {GREEN}✓ 权限已请求 (im:message, im:chat 等){NC}")
            else:
                print(f"  {YELLOW}⚠ 权限请求返回: {perm_resp.get('msg', perm_resp.get('code'))}{NC}")
                # Some scopes might still succeed partially
                result["permissions"] = "partial"
    except Exception as e:
        print(f"  {YELLOW}⚠ 权限 API 失败: {e}{NC}")
    
    # Step 3: 订阅事件
    events = ["im.message.receive_v1"]
    event_url = f"https://open.feishu.cn/open-apis/event/v1/app/event_subscription"
    event_data = json.dumps({"subscriptions": [{"event_type": e, "event_name": e} for e in events]}).encode()
    req = Request(event_url, data=event_data, headers=headers)
    try:
        with urlopen(req, timeout=10) as resp:
            event_resp = json.loads(resp.read().decode())
            if event_resp.get("code") == 0:
                result["events"] = True
                print(f"  {GREEN}✓ 事件订阅已配置 (im.message.receive_v1){NC}")
            else:
                print(f"  {YELLOW}⚠ 事件订阅返回: {event_resp.get('msg', event_resp.get('code'))}{NC}")
    except Exception as e:
        print(f"  {YELLOW}⚠ 事件 API 失败: {e}")
    
    # Step 4: 发布应用
    publish_url = f"https://open.feishu.cn/open-apis/application/v6/applications/{app_id}/app_versions"
    publish_data = json.dumps({
        "version": f"dragon-{int(time.time())}",
        "ability": {
            "gadget": {"enable": False},
            "web_app": {"enable": False},
            "bot": {"enable": True},
        },
    }).encode()
    req = Request(publish_url, data=publish_data, headers=headers, method="POST")
    req.method = "POST"
    try:
        with urlopen(req, timeout=10) as resp:
            pub_resp = json.loads(resp.read().decode())
            if pub_resp.get("code") == 0:
                # 还需要审批发布
                version_id = pub_resp.get("data", {}).get("version_id", "")
                if version_id:
                    approve_url = f"https://open.feishu.cn/open-apis/application/v6/applications/{app_id}/app_versions/{version_id}/publish"
                    approve_req = Request(approve_url, data=b"", headers=headers, method="POST")
                    approve_req.method = "POST"
                    try:
                        with urlopen(approve_req, timeout=10) as approve_resp:
                            approve_data = json.loads(approve_resp.read().decode())
                            if approve_data.get("code") == 0:
                                result["published"] = True
                                print(f"  {GREEN}✓ 应用已发布{NC}")
                            else:
                                print(f"  {YELLOW}⚠ 发布审批: {approve_data.get('msg')}{NC}")
                    except Exception:
                        print(f"  {YELLOW}⚠ 发布审批请求失败，请手动发布{NC}")
    except Exception as e:
        print(f"  {YELLOW}⚠ 发布请求失败: {e}，请手动发布{NC}")
    
    return result


def update_systemd_service(app_id: str, app_secret: str):
    """Update the dragon-gateway systemd service with Feishu credentials."""
    service_file = Path("/etc/systemd/system/dragon-gateway.service")
    if not service_file.exists():
        print(f"  {YELLOW}⚠ systemd 服务不存在，跳过自动配置{NC}")
        return
    
    try:
        content = service_file.read_text()
        lines = content.splitlines()
        new_lines = []
        updated_env = False
        found_feishu_id = False
        found_feishu_secret = False
        
        for line in lines:
            if line.strip().startswith("Environment=FEISHU_APP_ID="):
                new_lines.append(f"Environment=FEISHU_APP_ID={app_id}")
                found_feishu_id = True
                updated_env = True
            elif line.strip().startswith("Environment=FEISHU_APP_SECRET="):
                new_lines.append(f"Environment=FEISHU_APP_SECRET={app_secret}")
                found_feishu_secret = True
                updated_env = True
            else:
                new_lines.append(line)
        
        if not found_feishu_id:
            new_lines.append(f"Environment=FEISHU_APP_ID={app_id}")
        if not found_feishu_secret:
            new_lines.append(f"Environment=FEISHU_APP_SECRET={app_secret}")
        
        service_file.write_text("\n".join(new_lines) + "\n")
        print(f"  {GREEN}✓ systemd 服务已更新 Feishu 凭证{NC}")
        
        # Reload and restart
        import subprocess
        subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True)
        subprocess.run(["sudo", "systemctl", "restart", "dragon-gateway"], capture_output=True)
        print(f"  {GREEN}✓ dragon-gateway 已重启{NC}")
    except PermissionError:
        print(f"  {YELLOW}⚠ 无权限修改 systemd 服务，请手动: sudo vim {service_file}{NC}")
    except Exception as e:
        print(f"  {YELLOW}⚠ systemd 更新失败: {e}{NC}")


def print_next_steps(app_id: str, perm_result: dict):
    """Print next steps after onboarding."""
    console_url = f"https://open.feishu.cn/app/{app_id}"
    
    print(f"\n{BOLD}{'═' * 60}{NC}")
    print(f"{BOLD}  🎉 飞书机器人创建完成！{NC}")
    print(f"{BOLD}{'═' * 60}{NC}")
    print(f"  App ID:     {CYAN}{app_id}{NC}")
    
    if perm_result.get("permissions") and perm_result.get("events") and perm_result.get("published"):
        print(f"\n  {GREEN}✓ 权限、事件订阅、发布 已全部自动配置！{NC}")
        print(f"  {GREEN}✓ 直接启动 Gateway 即可使用。{NC}")
    else:
        print(f"\n  {YELLOW}⚠ 部分配置需要手动完成:{NC}")
        print(f"  {YELLOW}   1. 打开: {console_url}{NC}")
        print(f"  {YELLOW}   2. 权限管理 → 确认 im:message 等权限已开通{NC}")
        print(f"  {YELLOW}   3. 事件订阅 → 添加 im.message.receive_v1{NC}")
        print(f"  {YELLOW}   4. 版本管理与发布 → 创建版本并发布{NC}")
    
    print(f"\n  {BOLD}启动 Dragon Gateway:{NC}")
    print(f"  {CYAN}cd ~/dragon-agent && source .venv/bin/activate{NC}")
    print(f"  {CYAN}dragon gateway --feishu --port 8090{NC}")
    print()


def main():
    print(f"\n  {BOLD}╔══════════════════════════════════════╗{NC}")
    print(f"  {BOLD}║   🐉 Dragon × 飞书 一键创建机器人  ║{NC}")
    print(f"  {BOLD}╚══════════════════════════════════════╝{NC}")
    print()

    # ── Check existing Feishu config ──
    existing_app_id = os.getenv("FEISHU_APP_ID", "")
    existing_app_secret = os.getenv("FEISHU_APP_SECRET", "")
    
    env_path = Path.home() / "dragon-agent" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("FEISHU_APP_ID=") and not existing_app_id:
                existing_app_id = line.split("=", 1)[1].strip('"').strip("'")
            elif line.startswith("FEISHU_APP_SECRET=") and not existing_app_secret:
                existing_app_secret = line.split("=", 1)[1].strip('"').strip("'")
    
    if existing_app_id and existing_app_secret:
        print(f"  {GREEN}✓ 检测到已有飞书配置{NC}")
        print(f"  App ID:     {CYAN}{existing_app_id}{NC}")
        print(f"  App Secret: {CYAN}{existing_app_secret[:8]}...{NC}")
        print()
        choice = input(f"  {BOLD}[K] 保持现有配置  [R] 重新创建 (默认 K): {NC}").strip().lower()
        if choice != "r":
            print(f"  {GREEN}✓ 保持现有配置，跳过创建{NC}")
            update_systemd_service(existing_app_id, existing_app_secret)
            return
        print(f"  {YELLOW}将重新创建飞书应用...{NC}")
        print()

    # Step 1: Init
    print(f"{BOLD}[1/5]{NC} 连接飞书...")
    url = f"{ACCOUNTS_BASE}{REGISTRATION_PATH}"
    res = post(url, {"action": "init"})
    auth_methods = res.get("supported_auth_methods", [])
    if "client_secret" not in auth_methods:
        print(f"{RED}✗ 飞书设备码 OAuth 不支持，请使用其他方式创建应用{NC}")
        print(f"  手动创建: https://open.feishu.cn/app")
        sys.exit(1)
    print(f"  {GREEN}✓ 设备码 OAuth 可用{NC}")

    # Step 2: Begin
    print(f"{BOLD}[2/5]{NC} 生成设备码...")
    res = post(url, {
        "action": "begin",
        "archetype": "PersonalAgent",
        "auth_method": "client_secret",
        "request_user_info": "open_id",
    })
    device_code = res.get("device_code")
    verification_url = res.get("verification_uri_complete")
    user_code = res.get("user_code")
    expire_in = res.get("expire_in", EXPIRE_SECONDS)

    if not device_code or not verification_url:
        print(f"{RED}✗ 设备码生成失败: {json.dumps(res, indent=2)}{NC}")
        sys.exit(1)
    print(f"  {GREEN}✓ 设备码已生成{NC}")

    # Step 3: Display QR
    print(f"{BOLD}[3/5]{NC} 请扫码授权...")
    display_qr(verification_url, user_code)

    # Step 4: Poll
    print(f"\n{BOLD}[4/5]{NC} 等待扫码...")
    deadline = time.monotonic() + expire_in
    dots = 0
    while time.monotonic() < deadline:
        res = post(url, {"action": "poll", "device_code": device_code, "tp": "ob_app"})

        if res.get("client_id"):
            app_id = res["client_id"]
            app_secret = res["client_secret"]
            user_info = res.get("user_info", {})
            print(f"\n  {GREEN}✓ 授权成功！{NC}")
            print(f"  App ID:      {CYAN}{app_id}{NC}")
            print(f"  App Secret:  {CYAN}{app_secret[:8]}...{NC}")
            break
        
        error = res.get("error", "")
        if error == "access_denied":
            print(f"\n{RED}✗ 用户拒绝授权{NC}")
            sys.exit(1)
        elif error == "expired_token":
            print(f"\n{RED}✗ 设备码已过期，请重新运行{NC}")
            sys.exit(1)
        elif error == "authorization_pending":
            dots = (dots + 1) % 4
            print(f"\r  ⏳ 等待扫码{'.' * dots}   ", end="", flush=True)
        else:
            print(f"\n{YELLOW}⚠ 未知错误: {error}{NC}")
        
        time.sleep(POLL_INTERVAL)
    else:
        print(f"\n{RED}✗ 超时未扫码{NC}")
        sys.exit(1)

    # Step 5: Configure
    print(f"\n{BOLD}[5/5]{NC} 配置中...")
    
    # Write credentials
    write_env(app_id, app_secret)
    
    # Update systemd service if exists
    update_systemd_service(app_id, app_secret)
    
    # Try auto-configure permissions + events + publish
    print()
    perm_result = configure_permissions(app_id, app_secret)
    
    # Done
    print_next_steps(app_id, perm_result)


if __name__ == "__main__":
    main()
