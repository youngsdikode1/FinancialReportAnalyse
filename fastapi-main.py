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
from utils.config import CConfigManager
from monitor_manager import CMonitorManager
from pathlib import Path
from fastapi.responses import FileResponse
import version


app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")


USE_STREAM = True

def calc_file_md5(file_bytes: bytes) -> str:
    m = hashlib.md5()
    m.update(file_bytes)
    return m.hexdigest()



# ===================== 文件后台任务 =====================
def upload_background_task(session_id: str, orig_filename: str, file_bytes: bytes):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(session_id)
    c_user.AddMonitorMessage(f"📂后台线程开始处理文件：{orig_filename}")

    upload_dir = CConfigManager.GetInstance().GetDataDir()
    upload_dir += "/uploads/"
    os.makedirs(upload_dir, exist_ok=True)

    md5_hash = calc_file_md5(file_bytes)
    md5_name = f"{md5_hash}.pdf"
    save_path = os.path.join(upload_dir, md5_name)
    if not os.path.exists(save_path):
        with open(save_path, "wb") as f:
            f.write(file_bytes)

        c_user.AddMonitorMessage(f"✅文件{orig_filename}写入成功")
    else:

        c_user.AddMonitorMessage(f"ℹ️文件已存在")

    c_user.AddMonitorMessage(f"📄【{orig_filename}】保存完成，开始解析PDF财报")

    # 发布用户上传PDF事件，content传递pdf磁盘完整路径
    from events.event import CEventMessage, CEventType
    from events.event_manager import CEventManager
    event_msg = CEventMessage(
        msg_type=CEventType.TYPE_USER_UPLOAD,
        session_id=session_id,
        content=save_path,
    )
    evt_mgr = CEventManager.GetInstance()
    evt_mgr.Publish(event_msg)

# ===================== 外部消息处理函数 =====================
async def handle_incoming_message(raw_msg: dict, sid: str, web_socket: WebSocket):
    user_mgr = CUserManager()
    c_user = user_mgr.LoadUser(sid)
    socket_group = c_user.GetWebSocketGroup()
    socket_group.OnReciveJson(web_socket, raw_msg)



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

@app.get("/api/get_intro")
async def api_get_intro():
    from utils.prompt_manager import CPromptManager
    text = CPromptManager().IntroMyself()
    text += CPromptManager().GetExampleDesc()
    text += CPromptManager().GetSourceIntro()

    return {"intro_markdown": text}


@app.get("/api/monitor_info")
async def api_monitor_info():
    mgr = CMonitorManager.GetInstance()
    return {
        "user_info": mgr.GetUserInfo(),
        "rag_info": mgr.GetRAGInfo(),
        "hardware_info": mgr.GetHardwareInfo()
    }

#http://172.18.132.109:8000/monitor 
@app.get("/monitor")
async def redirect_monitor():
    return RedirectResponse(url="/static/monitor.html")



@app.get("/download_source")
async def download_source():


    cfg = CConfigManager.GetInstance()
    data_dir = cfg.GetDataDir()
    source_path = data_dir + "/source_code/" + "FinancialReportAnalyse.tar.gz"

    if not os.path.exists(source_path):
        LogInfo(f"download_source file not exist")

        return {"ok": False, "msg": "源码包不存在"}


    LogInfo(f"download_source file")

    return FileResponse(
        path=source_path,
        filename="FinancialReportAnalyse-src.tar.gz",
        media_type="application/zip"
    )


if __name__ == "__main__":
    import uvicorn


    LogInfo(f"finacial seek, vesion {version.GetVersion()}")
    uvicorn.run(app, host="0.0.0.0", port=8000)