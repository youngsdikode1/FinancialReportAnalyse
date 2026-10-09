from typing import Set
from fastapi import WebSocket
from utils.logs import LogInfo, LogStack
from typing import List, Optional
import asyncio
from events.event import CEventMessage, CEventType
from events.event_manager import CEventManager


class CWebSocketGroup:
    def __init__(self, session_id: str):
        self.m_session_id: str = session_id
        self.m_sockets: Set[WebSocket] = set()

    def AddSocket(self, web_socket: WebSocket):
        self.m_sockets.add(web_socket)

    def DelSocket(self, web_socket: WebSocket):
        if web_socket in self.m_sockets:
            self.m_sockets.remove(web_socket)

    def PushSingleSocket(self, conn: WebSocket, payload: dict):

        loop = getattr(conn, "m_main_loop", None)
        if loop is None:
            LogInfo("PushSingleSocket: missing m_main_loop, skip send")
            return
        coro = conn.send_json(payload)
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError as e:
            err_text = str(e)
            if "closed" in err_text:
                LogInfo(f"PushSingleSocket: event loop closed, skip send, session={self.m_session_id}")
            else:
                LogStack(e, "PushSingleSocket runtime error")

    def PushDatas(self, payload: dict, ignore_web_socket: Optional[WebSocket] = None):
        """同步方法，协程/后台线程都可调用，禁止使用 await"""
        # 转list防止遍历过程集合变动
        for conn in list(self.m_sockets):
            if conn is ignore_web_socket:
                continue

            self.PushSingleSocket(conn, payload)

    def OnReciveJson(self, web_socket: WebSocket, json_data: dict):
        msg_proto = CEventMessage(
            msg_type=CEventType.TYPE_WEB_SOCKET_PROTO,
            session_id=self.m_session_id,
            content=None,
            datas=json_data
        )
        msg_proto.SetObj(web_socket)
        evt_mgr = CEventManager.GetInstance()
        evt_mgr.Publish(msg_proto)