import os
import threading
import uuid
import asyncio
import hashlib
import requests
from typing import List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from users.user_manager import CUserManager
from users.user import CUser
from users.message import CMessage, CMessageType
from utils.logs import LogStack, LogInfo

app = FastAPI()
UPLOAD_DIR = "./files"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Ollama配置
OLLAMA_BASE_URL = "http://192.168.1.25:11434/v1/chat/completions"
MODEL_NAME = "qwen2.5:7b"
USE_STREAM = True

def calc_file_md5(file_bytes: bytes) -> str:
    m = hashlib.md5()
    m.update(file_bytes)
    return m.hexdigest()

"""
# ===================== 流式版本Agent =====================
def agent_worker_stream(session_id: str, user_query: str, web_socket: WebSocket):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(session_id)

    log_msg_start = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content="✅收到用户请求，启动【流式Agent】",
        m_color="#dddd22"
    )
    c_user.AddChatMessage(log_msg_start, ignore_web_socket=None)

    log_msg_ollama = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content="🧠调用Ollama qwen2.5:7b 流式推理",
        m_color="#dddd22"
    )
    c_user.AddChatMessage(log_msg_ollama, ignore_web_socket=None)

    req_payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": user_query}],
        "stream": True
    }
    resp = requests.post(OLLAMA_BASE_URL, json=req_payload, stream=True, timeout=120)
    if resp.status_code != 200:
        log_msg_err = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content=f"❌Ollama请求失败，code={resp.status_code}",
            m_color="#ff4444"
        )
        c_user.AddChatMessage(log_msg_err, ignore_web_socket=None)
        return

    full_answer = ""
    for line in resp.iter_lines():
        if not line:
            continue
        raw = line.decode("utf-8").strip()
        if raw.startswith("data: "):
            data = raw[6:]
            if data == "[DONE]":
                break
            import json
            try:
                j = json.loads(data)
                delta = j["choices"][0]["delta"].get("content", "")
                if delta:
                    full_answer += delta
                    c_user.PushStreamChatChunk(web_socket, delta)
            except Exception:
                continue

    c_user.PushStreamChatEnd(web_socket)

    bot_msg = CMessage(
        m_type=CMessageType.TYPE_CHAT_BOT,
        m_content=full_answer,
        m_color="#2288ff"
    )
    c_user.AddChatMessage(bot_msg, ignore_web_socket=web_socket)

    log_msg_done = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content="🏁【流式】LLM输出完成",
        m_color="#33cc33"
    )
    c_user.AddChatMessage(log_msg_done, ignore_web_socket=None)


# ===================== 一次性全量输出Agent =====================
def agent_worker_complete(session_id: str, user_query: str, web_socket: WebSocket):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(session_id)

    log_msg_start = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content="✅收到用户请求，启动【一次性全量Agent】",
        m_color="#dddd22"
    )
    c_user.AddChatMessage(log_msg_start, ignore_web_socket=None)

    log_msg_ollama = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content="🧠调用Ollama qwen2.5:7b，等待完整结果",
        m_color="#dddd22"
    )
    c_user.AddChatMessage(log_msg_ollama, ignore_web_socket=None)

    req_payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": user_query}],
        "stream": False
    }
    resp = requests.post(OLLAMA_BASE_URL, json=req_payload, timeout=120)
    if resp.status_code != 200:
        log_msg_err = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content=f"❌Ollama请求失败，code={resp.status_code}",
            m_color="#ff4444"
        )
        c_user.AddChatMessage(log_msg_err, ignore_web_socket=None)
        return

    import json
    j = resp.json()
    full_answer = j["choices"][0]["message"]["content"]

    bot_msg = CMessage(
        m_type=CMessageType.TYPE_CHAT_BOT,
        m_content=full_answer,
        m_color="#2288ff"
    )
    c_user.AddChatMessage(bot_msg, ignore_web_socket=web_socket)

    log_msg_done = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content="🏁【一次性】LLM输出完成",
        m_color="#33cc33"
    )
    c_user.AddChatMessage(log_msg_done, ignore_web_socket=None)

"""

# ===================== 文件后台任务 =====================
def upload_background_task(session_id: str, orig_filename: str, file_bytes: bytes):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(session_id)

    log_msg_begin = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content=f"📂后台线程开始处理文件：{orig_filename}",
        m_color="#dddd22"
    )
    c_user.AddChatMessage(log_msg_begin, ignore_web_socket=None)

    md5_hash = calc_file_md5(file_bytes)
    md5_name = f"{md5_hash}.pdf"
    save_path = os.path.join(UPLOAD_DIR, md5_name)
    if not os.path.exists(save_path):
        with open(save_path, "wb") as f:
            f.write(file_bytes)
        log_msg_write_ok = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content=f"✅文件写入成功，md5={md5_hash}",
            m_color="#33cc33"
        )
        c_user.AddChatMessage(log_msg_write_ok, ignore_web_socket=None)
    else:
        log_msg_exist = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content=f"ℹ️文件已存在，复用md5={md5_hash}",
            m_color="#dddd22"
        )
        c_user.AddChatMessage(log_msg_exist, ignore_web_socket=None)

    log_msg_finish = CMessage(
        m_type=CMessageType.TYPE_CHAT_MONITOR,
        m_content=f"📄【{orig_filename}】上传任务全部完成",
        m_color="#33cc33"
    )
    c_user.AddChatMessage(log_msg_finish, ignore_web_socket=None)

# ===================== 外部消息处理函数 =====================
async def handle_incoming_message(raw_msg: dict, sid: str, web_socket: WebSocket):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(sid)
    socket_group = c_user.GetWebSocketGroup()
    socket_group.OnReciveJson(web_socket, raw_msg)

    """
    msg_type = raw_msg.get("type")

    if msg_type == "load_history":
        LogInfo(f"handle_incoming_message 收到load_history请求, sid={sid} "
                f"chat_history_size={len(c_user.m_chat_history)} "
                f"monitor_history_size={len(c_user.m_monitor_history)}")
        # 已改为普通同步方法，移除 await
        c_user.RefreshChatUI()
        c_user.RefreshMonitorUI()
        return


    if msg_type == "query":
        user_text = raw_msg["query"]
        LogInfo(f"handle_incoming_message 收到用户query sid={sid}, user_text={user_text}")
        user_msg = CMessage(
            m_type=CMessageType.TYPE_CHAT_USER,
            m_content=user_text,
            m_color="#e7f3ff"
        )
        c_user.AddChatMessage(user_msg, ignore_web_socket=web_socket)

        if USE_STREAM:
            target_func = agent_worker_stream
        else:
            target_func = agent_worker_complete
        t = threading.Thread(
            target=target_func,
            args=(sid, user_text, web_socket),
            daemon=True
        )
        t.start()
    """

@app.get("/")
async def index():
    return RedirectResponse("/static/index.html")

@app.websocket("/ws")
async def web_socket_endpoint(web_socket: WebSocket):
    main_loop = asyncio.get_running_loop()
    await web_socket.accept()
    # 在AddSocket之前挂载本连接专属loop
    web_socket.m_main_loop = main_loop

    query_params = web_socket.query_params
    sid = query_params.get("sid")
    user_mgr = CUserManager()
    user: CUser = user_mgr.LoadUser(sid)
    sid = user.GetSessionID()
    socket_group = user.GetWebSocketGroup()
    socket_group.AddSocket(web_socket)

    LogInfo(f"web_socket_endpoint 会话建立 sid={sid}")
    try:
        await web_socket.send_json({"type": "init", "session_id": sid})
        while True:
            received_msg = await web_socket.receive_json()

            try:
                # 【业务处理，单独内层try，业务异常只打日志，不断开连接】
                await handle_incoming_message(received_msg, sid, web_socket)
            except Exception as e:
                desc = f"handle_incoming_message 业务处理异常 sid={sid}, err={str(e)} raw_msg={received_msg}"
                LogInfo(desc)
                LogStack(e, desc)

    except WebSocketDisconnect:
        LogInfo(f"web_socket_endpoint 会话断开 sid={sid}")
        socket_group.DelSocket(web_socket)
    except Exception as e:
        desc = f"web_socket_endpoint 异常 sid={sid}, err={str(e)}"
        LogInfo(desc)
        socket_group.DelSocket(web_socket)
        LogStack(e, desc)

@app.post("/upload")
async def upload_file(
    session_id: str = Form(),
    file: UploadFile = None
):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(session_id)
    LogInfo(f"upload_file 收到上传请求 session_id={session_id}, filename={file.filename if file else None}")
    if not file:
        return {"ok": False, "msg": "未选择文件"}
    if not file.filename.lower().endswith(".pdf"):
        return {"ok": False, "msg": "仅支持PDF文件"}
    file_bytes = await file.read()
    orig_name = file.filename
    t = threading.Thread(
        target=upload_background_task,
        args=(session_id, orig_name, file_bytes),
        daemon=True
    )
    t.start()
    return {"ok": True, "msg": "文件已接收，后台正在处理，请查看右侧状态栏"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)