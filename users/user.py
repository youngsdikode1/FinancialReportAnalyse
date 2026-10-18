from typing import List, Optional
from fastapi import WebSocket
from users.web_socket_group import CWebSocketGroup
from users.message import CMessage, CMessageType
from users.chat_history import CChatHistory
from utils.logs import LogInfo, LogStack
import events.event
from events.event_manager import CEventManager
import agents.agent_advisor
from agents.agent_pdf_processor import CAgentPDFProcessor
from utils.prompt_manager import CPromptManager
import users.defs



class CUser:
    def __init__(self, session_id: str):
        self.m_session_id: str = session_id
        self.m_socket_group: CWebSocketGroup = CWebSocketGroup(session_id)
        # 把session_id传给CChatHistory实例
        self.m_history = CChatHistory(session_id)
        self.m_uploaded_files: List[dict] = []
        self.m_advisor = agents.agent_advisor.CAgentAdvisor(self)
        LogInfo(f"CUser::__init__  创建会话 session_id={self.m_session_id}")
        self.Load()

    def GetHistory(self):
        return self.m_history

    def GetSessionID(self) -> str:
        return self.m_session_id

    def GetWebSocketGroup(self) -> CWebSocketGroup:
        return self.m_socket_group

    def ClearChatHistory(self):
        """清空当前会话聊天历史，内存user_history + 磁盘存档一并清空；monitor监控日志保留"""
        self.m_history.ClearUserHistory()

    def AddMonitorMessage(self, content):
        """快捷：新增一条状态栏监控消息"""
        msg = CMessage.NewMessage(
            msg_type=CMessageType.TYPE_CHAT_MONITOR,
            content=content,
            color="#dddd22"
        )
        self.AddChatMessage(msg, ignore_web_socket=None)

    def AddUserMessage(self, content: str, ignore_web_socket: Optional[WebSocket] = None):
        """快捷：新增用户聊天消息"""
        msg = CMessage.NewMessage(
            msg_type=CMessageType.TYPE_CHAT_USER,
            content=content,
            color="#e7f3ff"
        )
        self.AddChatMessage(msg, ignore_web_socket=ignore_web_socket)

    def AddBotMessage(self, content: str, ignore_web_socket: Optional[WebSocket] = None):
        """快捷：新增机器人聊天消息"""
        msg = CMessage.NewMessage(
            msg_type=CMessageType.TYPE_CHAT_BOT,
            content=content,
            color="#2288ff"
        )
        self.AddChatMessage(msg, ignore_web_socket=ignore_web_socket)

    def OnWorkFlow(self, msg):
        self.AddMonitorMessage(msg.m_content)

    def OnAgentThink(self, msg):
        self.AddMonitorMessage(msg.m_content)

    def OnUpLoad(self, msg):

        session_id = msg.m_session_id
        pdf_path = msg.m_content
        self.AddMonitorMessage(f"📂收到上传PDF，开始解析：{pdf_path}")

        LogInfo(f"CUser.OnUpLoad {self.m_session_id} {pdf_path}")

        proc = CAgentPDFProcessor()
        proc.m_session_id = self.m_session_id

        # 上传场景 force_overwrite=True，强制覆盖旧报告
        result = proc.Run(pdf_file_path=pdf_path,
                          force_overwrite=True,
                          dump_batch_summary=False)

        if not result["success"]:
            self.AddMonitorMessage(f"❌PDF处理失败：{result.get('fail_msg','未知错误')}")
            return

        report_name = result['report_name']

        rag_ok = result.get("rag_ingest_ok", False)
        if rag_ok:
            self.AddMonitorMessage(f"✅{report_name}已完成解析及保存")
        else:
            self.AddMonitorMessage(f"⚠️{report_name}PDF解析保存成功，但RAGf向量化入库失败，部分检索能力不可用，可以尝试重新上传。")

        self.AddBotMessage(f"系统接收到您上传的PDF文件，内容为{report_name}，已完成解析及保存")
        self.m_history.Save()

    def Register(self):
        LogInfo(f"CUser.Register {self.m_session_id}")
        subscribe_proto = events.event.CSubScribeObj(
            events.event.CEventType.TYPE_WEB_SOCKET_PROTO,
            session_id=self.m_session_id,
            callback=self.OnProto
        )
        CEventManager.GetInstance().Subscribe(subscribe_proto)
        sub_think = events.event.CSubScribeObj(events.event.CEventType.TYPE_AGENT_THINK,
        session_id=self.m_session_id, callback=self.OnAgentThink)
        CEventManager.GetInstance().Subscribe(sub_think)
        sub_work_flow = events.event.CSubScribeObj(events.event.CEventType.TYPE_WORK_FLOW_STEP,
        session_id=self.m_session_id, callback=self.OnWorkFlow)
        CEventManager.GetInstance().Subscribe(sub_work_flow)

        sub_upload = events.event.CSubScribeObj(events.event.CEventType.TYPE_USER_UPLOAD,
        self.m_session_id, callback=self.OnUpLoad)

        CEventManager.GetInstance().Subscribe(sub_upload)

    def PushStreamChatChunk(self, ws_conn: WebSocket, text: str):
        payload = {"type": "chat_chunk", "text": text}
        self.m_socket_group.PushSingleSocket(ws_conn, payload)

    def PushStreamChatEnd(self, ws_conn: WebSocket):
        payload = {"type": "chat_end"}
        self.m_socket_group.PushSingleSocket(ws_conn, payload)

    def AddChatMessage(self, msg: CMessage, ignore_web_socket: Optional[WebSocket] = None):
        # 根据消息类型分派到CChatHistory
        if msg.m_type in (CMessageType.TYPE_CHAT_USER, CMessageType.TYPE_CHAT_BOT):
            self.m_history.AddUserHistory(msg)
        elif msg.m_type == CMessageType.TYPE_CHAT_MONITOR:
            self.m_history.AddMonitorHistory(msg)
        payload = {
            "msg_type": "append_msg",
            "item": msg.ToDict()
        }
        self.m_socket_group.PushDatas(payload, ignore_web_socket=ignore_web_socket)

    def RefreshSocketChatUI(self, web_socket):
        LogInfo(f"CUser::RefreshSocketChatUI session_id={self.m_session_id}")
        data_list = self.m_history.GetUserHistoryDicts()
        payload = {
            "msg_type": "full_chat_history",
            "data": data_list
        }
        self.m_socket_group.PushSingleSocket(web_socket, payload)

    def RefreshSocketMonitorUI(self, web_socket):
        LogInfo(f"CUser::RefreshSocketMonitorUI session_id={self.m_session_id}")
        data_list = self.m_history.GetMonitorHistoryDicts()
        payload = {
            "msg_type": "full_monitor_history",
            "data": data_list
        }
        self.m_socket_group.PushSingleSocket(web_socket, payload)

    def RefreshInputUI(self, state: str, hint: Optional[str] = None):
        """
        刷新前端输入框与发送按钮状态
        :param state: SYS_STATE_IDLE / SYS_STATE_RUNNING
        :param hint: 输入框提示语；为空/None 时按 state 取默认文案
        """
        if state == users.defs.SYS_STATE_IDLE:
            if not hint:
                hint = users.defs.DEFAULT_INPUT_HINT_IDLE
        else:
            if not hint:
                hint = users.defs.DEFAULT_INPUT_HINT_RUNNING

        payload = {
            "type": "sys_state",
            "state": state,
            "input_hint": hint,
        }
        self.m_socket_group.PushDatas(payload)

    def Save(self) -> None:
        """
        持久化聊天历史，仅保存user_history
        CUser不再处理路径，全部交给m_history内部；异常只打日志，不抛出打断业务
        """
        try:
            self.m_history.Save()
        except Exception as e:
            LogStack(e, f"CUser::Save 会话存档异常 session={self.m_session_id}")

    def Load(self) -> bool:
        """
        从本地磁盘加载会话聊天历史
        返回True加载成功；文件缺失/异常返回False，不崩溃，会话继续可用
        新增逻辑：首次登录无历史记录，自动推送自我介绍
        """

        ok = self.m_history.Load()

        if ok:
            LogInfo(f"CUser::Load 成功加载会话历史 session={self.m_session_id}")
        else:
            LogInfo(f"CUser::Load 无可用会话历史 session={self.m_session_id}")
        return ok

    def OnTestStreamOutput(self, user_query: str, web_socket: WebSocket):
        import requests
        url = "http://192.168.1.25:11434/v1/chat/completions"
        model_name = "qwen2.5:7b"
        self.AddUserMessage(user_query, ignore_web_socket=web_socket)
        self.AddMonitorMessage("✅收到用户请求，启动【流式Agent】")
        self.AddMonitorMessage("🧠调用Ollama qwen2.5:7b 流式推理")
        req_payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": user_query}],
        }
        use_stream = 1
        if use_stream:
            req_payload['stream'] = True
            resp = requests.post(url, json=req_payload, stream=True, timeout=120)
            if resp.status_code != 200:
                self.AddMonitorMessage(f"❌Ollama请求失败，code={resp.status_code}")
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
                            self.PushStreamChatChunk(web_socket, delta)
                    except Exception:
                        continue
            self.PushStreamChatEnd(web_socket)
        else:
            req_payload['stream'] = False
            resp = requests.post(url, json=req_payload, timeout=120)
            if resp.status_code != 200:
                self.AddMonitorMessage(f"❌Ollama请求失败，code={resp.status_code}")
                return
            j = resp.json()
            full_answer = j["choices"][0]["message"]["content"]
        self.AddBotMessage(full_answer, ignore_web_socket=web_socket)
        self.AddMonitorMessage("🏁LLM输出完成")

    def OnGetHistory(self, msg):
        web_socket = msg.GetObj()
        LogInfo(f"CUser.OnGetHistory {self.m_session_id}")

        # 前端请求聊天历史：如果用户对话记录为空，则自动插入自我介绍bot消息
        history_items = self.m_history.GetUserHistoryDicts()
        if len(history_items) == 0:
            from utils.prompt_manager import CPromptManager
            intro_text = CPromptManager().IntroMyself()
            self.AddBotMessage(intro_text)
            self.m_history.Save()
            LogInfo(f"CUser.OnGetHistory 会话为空，自动下发自我介绍 session_id={self.m_session_id}")

        self.RefreshSocketChatUI(web_socket)
        self.RefreshSocketMonitorUI(web_socket)

    def OnClearChatHistory(self, msg):
        """处理前端清空聊天记录协议"""
        web_socket = msg.GetObj()
        self.ClearChatHistory()
        # 单播回执给发起操作的客户端
        payload_cleared = {"type":"chat_cleared"}
        self.m_socket_group.PushSingleSocket(web_socket, payload_cleared)
        # 群发：向本会话所有连接推送最新聊天历史(空数组)，全部浏览器Tab同步刷新
        chat_data = self.m_history.GetUserHistoryDicts()
        payload_refresh = {
            "msg_type": "full_chat_history",
            "data": chat_data
        }
        self.m_socket_group.PushDatas(payload_refresh)

        self.__CancelTask()


    def OnGetSysState(self, msg):
        """前端登录时请求当前系统状态，目前默认回 idle"""

        if self.m_advisor.IsTaskRunning():
            self.RefreshInputUI(users.defs.SYS_STATE_RUNNING, None)
        else: 
            self.RefreshInputUI(users.defs.SYS_STATE_IDLE, None)

    def OnQuery(self, msg):
        web_socket = msg.GetObj()
        _datas = msg.GetDatas()
        user_query = _datas["query"]
        LogInfo(f"CUser.OnQuery {self.m_session_id} query:{user_query}")
        test = 0
        if test:
            self.OnTestStreamOutput(user_query, web_socket)
            return
        self.AddUserMessage(user_query, ignore_web_socket=web_socket)



        full_answer = self.m_advisor.HandleUserInput(user_query)
        self.AddBotMessage(full_answer)



        # ✅一轮问答全部处理完成，执行存档
        self.Save()

    def OnCancelTask(self, msg):
        """
        前端点击停止按钮，发送 cancel_task 协议
        这里只做入口，具体取消逻辑后续填充
        """
        web_socket = msg.GetObj()
        LogInfo(f"CUser.OnCancelTask session={self.m_session_id}")

        self.__CancelTask()
        self.AddMonitorMessage("❌当前任务已暂停")
        self.AddMonitorMessage("")

    def __CancelTask(self):
        self.m_advisor.CancelTask()
        self.RefreshInputUI(users.defs.SYS_STATE_IDLE, None)


    def OnProto(self, msg):
        handlers = {}
        handlers['load_history'] = self.OnGetHistory
        handlers['query'] = self.OnQuery
        handlers['clear_chat_history'] = self.OnClearChatHistory
        handlers['get_sys_state'] = self.OnGetSysState   # ← 新增
        handlers['cancel_task'] = self.OnCancelTask
        _datas = msg.GetDatas()
        _type = _datas['type']
        handler = handlers.get(_type)
        if not handler:
            LogInfo(f"CUser.OnProto {self.m_session_id} {msg.m_session_id} {_datas} proto not found.")
            return
        LogInfo(f"CUser.OnProto {self.m_session_id} {msg.m_session_id} {_datas}")
        handler(msg)