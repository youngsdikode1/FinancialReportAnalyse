# users/user_manager.py
from typing import Dict
from users.user import CUser
import uuid
import os

def gen_session_id() -> str:
    """hex 24位，12字节原始随机 → hex得到24字符"""
    return os.urandom(12).hex()

class CUserManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.m_users: Dict[str, CUser] = {}
        return cls._instance

    def __AddUser(self, user):
        user.Register()
        self.m_users[user.GetSessionID()] = user

    def CreateNewUser(self, session_id: str) -> CUser:
        if session_id in self.m_users:
            return

        if not session_id:
            session_id = gen_session_id()

        new_user = CUser(session_id)
        self.__AddUser(new_user)
        return new_user

    def LoadUser(self, session_id: str) -> CUser:


        if session_id in self.m_users:
            return self.m_users[session_id]

        return self.CreateNewUser(session_id)


    def GetOnlineUserCount(self) -> int:
        """统计当前至少有一个活跃 WebSocket 连接的用户数"""
        count = 0
        for user in self.m_users.values():
            if user.GetWebSocketGroup().IsOnline():
                count += 1
        return count
