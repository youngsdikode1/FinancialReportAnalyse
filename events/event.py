from typing import Callable, Optional


class CEventType:
    TYPE_USER_INPUT = "user_input"      # 用户输入
    TYPE_AGENT_THINK = "agent_think"    # agent 思考
    TYPE_WORK_FLOW_STEP = "work_flow_step" # 工作流状态步骤
    TYPE_USER_UPLOAD = "user_upload"    # 用户上传
    TYPE_WEB_SOCKET_PROTO = "web_socket_proto" #前端ws协议

class CSubScribeObj:
    """
    订阅封装对象
    唯一性判定：(m_msg_type, m_session_id, m_callback) 三者组合
    支持放入set，支持全局函数、类实例成员绑定方法作为callback
    """
    def __init__(self, msg_type: str, session_id: str, callback: Callable):
        self.m_msg_type = msg_type
        self.m_session_id: str = session_id
        self.m_callback: Callable = callback

    def __hash__(self):
        return hash((self.m_msg_type, self.m_session_id, self.m_callback))

    def __eq__(self, other):
        if not isinstance(other, CSubScribeObj):
            return False
        return (self.m_msg_type == other.m_msg_type
                and self.m_session_id == other.m_session_id
                and self.m_callback == other.m_callback)


class CEventMessage:
    """事件消息载体DTO，m_session_id为必填项"""
    def __init__(
        self,
        msg_type: str,
        session_id: str,
        content: str = "",
        int_value: int = 0,
        datas: Optional[dict] = None
    ):
        # 非空校验
        if not session_id or not isinstance(session_id, str):
            raise ValueError("CEventMessage: m_session_id must be non-empty string")
        self.m_msg_type = msg_type
        self.m_session_id: str = session_id
        self.m_content = content
        self.m_int_value = int_value
        self.m_datas: Optional[dict] = datas    #字典数据
        self.m_obj = None   #任意对象


    def __repr__(self):
        return f"CEventMessage<{self.m_msg_type},{self.m_session_id},{self.m_content}>"

    def SetObj(self, obj):
        self.m_obj = obj

    def GetObj(self):
        return self.m_obj

    def GetDatas(self):
        return self.m_datas

    def SetDatas(self, datas):
        self.m_datas = datas