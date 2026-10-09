from typing import List, Optional
from fastapi import WebSocket
from users.web_socket_group import CWebSocketGroup
from users.message import CMessage, CMessageType
from utils.logs import LogInfo
import events.event
from events.event_manager import CEventManager
import json
import agents.agent_advisor

class CUser:
    def __init__(self, session_id: str):
        self.m_session_id: str = session_id
        self.m_socket_group: CWebSocketGroup = CWebSocketGroup(session_id)
        self.m_chat_history: List[CMessage] = []
        self.m_monitor_history: List[CMessage] = []
        self.m_uploaded_files: List[dict] = []
        self.m_advisor = agents.agent_advisor.CAgentAdvisor(self.m_session_id)


        LogInfo(f"CUser::__init__  创建会话 session_id={self.m_session_id}")

    def GetSessionID(self) -> str:
        return self.m_session_id

    def GetWebSocketGroup(self) -> CWebSocketGroup:
        return self.m_socket_group


    def __AddMonitorMessage(self, content):

        log_msg_start = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content=content,
            m_color="#dddd22"
        )
        self.AddChatMessage(log_msg_start, ignore_web_socket=None)

    def OnWorkFlow(self, msg):
        self.__AddMonitorMessage(msg.m_content)

    def OnAgentThink(self, msg):
        self.__AddMonitorMessage(msg.m_content)


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

    def PushStreamChatChunk(self, ws_conn: WebSocket, text: str):
        payload = {"type": "chat_chunk", "text": text}
        self.m_socket_group.PushSingleSocket(ws_conn, payload)

    def PushStreamChatEnd(self, ws_conn: WebSocket):
        payload = {"type": "chat_end"}
        self.m_socket_group.PushSingleSocket(ws_conn, payload)

    def AddChatMessage(self, msg: CMessage, ignore_web_socket: Optional[WebSocket] = None):
        if msg.m_type in (CMessageType.TYPE_CHAT_USER, CMessageType.TYPE_CHAT_BOT):
            self.m_chat_history.append(msg)
            LogInfo(f"CUser::AddChatMessage 保存聊天消息 type={msg.m_type}, chat_history_size={len(self.m_chat_history)}, session_id={self.m_session_id}")
        elif msg.m_type == CMessageType.TYPE_CHAT_MONITOR:
            self.m_monitor_history.append(msg)
            LogInfo(f"CUser::AddChatMessage 保存监控消息 type={msg.m_type}, monitor_history_size={len(self.m_monitor_history)}, session_id={self.m_session_id}")

        payload = {
            "msg_type": "append_msg",
            "item": msg.ToDict()
        }
        self.m_socket_group.PushDatas(payload, ignore_web_socket=ignore_web_socket)

    """
    def RefreshChatUI(self):
        LogInfo(f"CUser::RefreshChatUI session_id={self.m_session_id}, 待推送聊天消息数量={len(self.m_chat_history)}")
        data_list = [msg.ToDict() for msg in self.m_chat_history]
        payload = {
            "msg_type": "full_chat_history",
            "data": data_list
        }
        self.m_socket_group.PushDatas(payload)

    def RefreshMonitorUI(self):
        LogInfo(f"CUser::RefreshMonitorUI session_id={self.m_session_id}, 待推送监控消息数量={len(self.m_monitor_history)}")
        data_list = [msg.ToDict() for msg in self.m_monitor_history]
        payload = {
            "msg_type": "full_monitor_history",
            "data": data_list
        }
        self.m_socket_group.PushDatas(payload)
    """
        

    def RefreshSocketChatUI(self, web_socket):

        LogInfo(f"CUser::RefreshSocketChatUI session_id={self.m_session_id}, 待推送监控消息数量={len(self.m_monitor_history)}")
        data_list = [msg.ToDict() for msg in self.m_chat_history]
        payload = {
            "msg_type": "full_chat_history",
            "data": data_list
        }
        self.m_socket_group.PushSingleSocket(web_socket, payload)


    def RefreshSocketMonitorUI(self, web_socket):
        LogInfo(f"CUser::RefreshSocketMonitorUI session_id={self.m_session_id}, 待推送监控消息数量={len(self.m_monitor_history)}")
        data_list = [msg.ToDict() for msg in self.m_monitor_history]
        payload = {
            "msg_type": "full_monitor_history",
            "data": data_list
        }
        self.m_socket_group.PushSingleSocket(web_socket, payload)



    def OnTestStreamOutput(self, user_query: str, web_socket: WebSocket):

        import requests

        url = "http://192.168.1.25:11434/v1/chat/completions"
        model_name = "qwen2.5:7b"

        user_msg = CMessage(
            m_type=CMessageType.TYPE_CHAT_USER,
            m_content=user_query,
            m_color="#e7f3ff"
        )
        self.AddChatMessage(user_msg, ignore_web_socket=web_socket)

        log_msg_start = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content="✅收到用户请求，启动【流式Agent】",
            m_color="#dddd22"
        )
        self.AddChatMessage(log_msg_start, ignore_web_socket=None)

        log_msg_ollama = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content="🧠调用Ollama qwen2.5:7b 流式推理",
            m_color="#dddd22"
        )
        self.AddChatMessage(log_msg_ollama, ignore_web_socket=None)

        req_payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": user_query}],
        }

        use_stream = 1
        if use_stream:
            req_payload['stream'] = True

            resp = requests.post(url, json=req_payload, stream=True, timeout=120)
            if resp.status_code != 200:
                log_msg_err = CMessage(
                    m_type=CMessageType.TYPE_CHAT_MONITOR,
                    m_content=f"❌Ollama请求失败，code={resp.status_code}",
                    m_color="#ff4444"
                )
                self.AddChatMessage(log_msg_err, ignore_web_socket=None)
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
                log_msg_err = CMessage(
                    m_type=CMessageType.TYPE_CHAT_MONITOR,
                    m_content=f"❌Ollama请求失败，code={resp.status_code}",
                    m_color="#ff4444"
                )
                c_user.AddChatMessage(log_msg_err, ignore_web_socket=None)
                return
            j = resp.json()
            full_answer = j["choices"][0]["message"]["content"]

        bot_msg = CMessage(
            m_type=CMessageType.TYPE_CHAT_BOT,
            m_content=full_answer,
            m_color="#2288ff"
        )

        if use_stream: 
            self.AddChatMessage(bot_msg, ignore_web_socket=web_socket)
        else:
            self.AddChatMessage(bot_msg)            

        log_msg_done = CMessage(
            m_type=CMessageType.TYPE_CHAT_MONITOR,
            m_content="🏁LLM输出完成",
            m_color="#33cc33"
        )
        self.AddChatMessage(log_msg_done, ignore_web_socket=None)


    def OnGetHistory(self, msg):

        web_socket = msg.GetObj()
        LogInfo(f"CUser.OnGetHistory {self.m_session_id}")

        self.RefreshSocketChatUI(web_socket)
        self.RefreshSocketMonitorUI(web_socket)



    def OnQuery(self, msg):
        web_socket = msg.GetObj()
        _datas = msg.GetDatas()      
        user_query = _datas["query"]
        LogInfo(f"CUser.OnQuery {self.m_session_id} query:{user_query}")

        test = 0
        if test:
            self.OnTestStreamOutput(user_query, web_socket)
            return

        user_msg = CMessage(
            m_type=CMessageType.TYPE_CHAT_USER,
            m_content=user_query,
            m_color="#e7f3ff"
        )
        self.AddChatMessage(user_msg, ignore_web_socket = web_socket)

        full_answer = self.m_advisor.HandleUserInput(user_query)
        bot_msg = CMessage(
            m_type=CMessageType.TYPE_CHAT_BOT,
            m_content=full_answer,
            m_color="#2288ff"
        )
        self.AddChatMessage(bot_msg)               


    def OnProto(self, msg):
        handlers = {}
        handlers['load_history'] = self.OnGetHistory
        handlers['query'] = self.OnQuery

        _datas = msg.GetDatas()
        _type = _datas['type']
        handler = handlers.get(_type)
        if not handler:
            LogInfo(f"CUser.OnProto {self.m_session_id} {msg.m_session_id} {_datas} proto not found.")
            return
        LogInfo(f"CUser.OnProto {self.m_session_id} {msg.m_session_id} {_datas}")
        handler(msg)