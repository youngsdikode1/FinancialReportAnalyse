import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Dict, Tuple, Set
from events.event import CSubScribeObj, CEventMessage, CEventType
from utils.logs import LogStack, LogInfo


class CEventManager:
    # 单例全局实例
    _instance: Optional["CEventManager"] = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, max_workers: int = 50):
        # 防止多次 __init__ 重复重置内部状态
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        # 消息队列，哨兵用None
        self.m_queue: queue.Queue[Optional[CEventMessage]] = queue.Queue()
        self.m_sub_scribes: Dict[Tuple[str, str], Set[CSubScribeObj]] = dict()
        self.m_lock = threading.Lock()
        self.m_consumer_thread: Optional[threading.Thread] = None
        self.m_running = False
        # 线程池最大50工作线程
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        self.Start()

    @classmethod
    def GetInstance(cls) -> "CEventManager":
        """获取全局唯一单例实例，对外标准入口"""
        if cls._instance is None:
            max_workers = 50
            cls._instance = cls(max_workers=max_workers)
        return cls._instance

    def Subscribe(self, sub_obj: CSubScribeObj):
        """注册订阅，set自动去重，同一个sub_obj实例不会重复加入集合"""
        with self.m_lock:
            key = (sub_obj.m_msg_type, sub_obj.m_session_id)
            if key not in self.m_sub_scribes:
                self.m_sub_scribes[key] = set()
            self.m_sub_scribes[key].add(sub_obj)

    def UnSubscribe(self, sub_obj: CSubScribeObj):
        """传入订阅对象，移除对应订阅；集合为空自动清理字典key"""
        with self.m_lock:
            key = (sub_obj.m_msg_type, sub_obj.m_session_id)
            if key not in self.m_sub_scribes:
                return
            sub_set = self.m_sub_scribes[key]
            sub_set.discard(sub_obj)
            if len(sub_set) == 0:
                del self.m_sub_scribes[key]

    def Publish(self, msg: CEventMessage):
        """发布消息，仅入队列，立刻返回，不阻塞发布线程"""
        self.m_queue.put(msg)

    def _run_callback_wrapper(self, callback, event: CEventMessage):
        """线程池内执行回调，异常当场捕获并调用LogStack"""
        try:
            callback(event)
        except Exception as e:
            desc = f"EventBus callback error, session_id={event.m_session_id}, event_type={event.m_msg_type}, callback={callback.__name__}"
            LogStack(e, desc)

    def ConsumeLoop(self):
        """后台消费循环：提交任务到线程池，提交后立刻继续，不阻塞"""
        while self.m_running:
            event = self.m_queue.get()
            # 哨兵消息，用于退出
            if event is None:
                self.m_queue.task_done()
                continue

            lookup_key = (event.m_msg_type, event.m_session_id)


            subs_snapshot: Set[CSubScribeObj] = set()
            with self.m_lock:
                if lookup_key in self.m_sub_scribes:
                    subs_snapshot = self.m_sub_scribes[lookup_key].copy()

            # 该(msg_type,user_id)下全部订阅回调，提交线程池执行
            for sub in subs_snapshot:
                callback_func = sub.m_callback
                self._executor.submit(self._run_callback_wrapper, callback_func, event)

            self.m_queue.task_done()

    def Start(self):
        """启动消费线程"""
        if self.m_running:
            return
        self.m_running = True
        self.m_consumer_thread = threading.Thread(target=self.ConsumeLoop, daemon=True)
        self.m_consumer_thread.start()

    def Stop(self):
        """优雅停止事件管理器"""
        if not self.m_running:
            return
        self.m_running = False
        # 塞入哨兵唤醒阻塞的get()
        self.m_queue.put(None)
        if self.m_consumer_thread is not None:
            self.m_consumer_thread.join()
        # wait=False：不等待已提交任务，快速退出；正式环境可改为wait=True
        self._executor.shutdown(wait=False)


def PostWorkFlowMessage(session_id, content):
    event_msg = CEventMessage(
        msg_type = CEventType.TYPE_WORK_FLOW_STEP,
        session_id = session_id,
        content = content,
    )

    evt_mgr = CEventManager.GetInstance()
    evt_mgr.Publish(event_msg)    

    if session_id == "test-session":
        print(content)

# ====================== 模块内测试用例（原样恢复） ======================
if __name__ == "__main__":
    import time
    print("===== Run CEventManager【单例模式】Test, max_workers=50 =====")
    # 使用单例接口获取实例
    evt_mgr = CEventManager.GetInstance()

    def User1_OnAgentThink(msg: CEventMessage):
        LogInfo(f"[User1 AgentThink] session_id:{msg.m_session_id}, content:{msg.m_content}, datas={msg.m_datas}")

    def ErrorCallback(msg: CEventMessage):
        1 / 0

    def ParallelWorkCallback(msg: CEventMessage):
        task_id = msg.m_datas.get("task_id", "unknown")
        tid = threading.get_ident()
        LogInfo(f"[ParallelWork] START task={task_id}, thread_id={tid}")
        time.sleep(3)
        LogInfo(f"[ParallelWork] END   task={task_id}, thread_id={tid}")

    class UiReceiver:
        def OnWorkflowStep(self, msg: CEventMessage):
            LogInfo(f"[UiReceiver Workflow] user_id:{msg.m_session_id}, content:{msg.m_content}, int_val={msg.m_int_value}")
        def OnUserUpload(self, msg: CEventMessage):
            LogInfo(f"[UiReceiver Upload] user_id:{msg.m_session_id}, content:{msg.m_content}, datas={msg.m_datas}")

    ui_recv_user2 = UiReceiver()
    # 注册订阅
    sub_user1 = CSubScribeObj(CEventType.TYPE_AGENT_THINK, user_id="user_001", callback=User1_OnAgentThink)
    sub_err = CSubScribeObj(CEventType.TYPE_AGENT_THINK, user_id="user_001", callback=ErrorCallback)
    sub_parallel = CSubScribeObj(CEventType.TYPE_AGENT_THINK, user_id="user_001", callback=ParallelWorkCallback)
    sub_user2_flow = CSubScribeObj(CEventType.TYPE_WORK_FLOW_STEP, user_id="user_002", callback=ui_recv_user2.OnWorkflowStep)
    sub_user2_upload = CSubScribeObj(CEventType.TYPE_USER_UPLOAD, user_id="user_002", callback=ui_recv_user2.OnUserUpload)

    evt_mgr.Subscribe(sub_user1)
    evt_mgr.Subscribe(sub_err)
    evt_mgr.Subscribe(sub_parallel)
    evt_mgr.Subscribe(sub_user2_flow)
    evt_mgr.Subscribe(sub_user2_upload)

    LogInfo("---- 一次性发布15条事件，每条触发ParallelWorkCallback(睡眠3秒)，观察并行效果 ----")
    start_total = time.time()
    # 一次性发布15条事件
    for idx in range(15):
        msg = CEventMessage(
            msg_type=CEventType.TYPE_AGENT_THINK,
            user_id="user_001",
            content=f"parallel test {idx}",
            datas={"task_id": f"t{idx}"}
        )
        evt_mgr.Publish(msg)

    msg_flow = CEventMessage(
        msg_type=CEventType.TYPE_WORK_FLOW_STEP,
        user_id="user_002",
        content="开始执行RAG召回",
        int_value=1,
        datas={"task_id": "task_002"}
    )
    evt_mgr.Publish(msg_flow)

    msg_upload = CEventMessage(
        msg_type=CEventType.TYPE_USER_UPLOAD,
        user_id="user_002",
        content="用户上传财报PDF文件",
        datas={"file_name": "maotai2023.pdf"}
    )
    evt_mgr.Publish(msg_upload)

    # 测试跨用户隔离：user002发AGENT_THINK，user001订阅的AGENT_THINK不会收到
    msg_wrong_user = CEventMessage(
        msg_type=CEventType.TYPE_AGENT_THINK,
        user_id="user_002",
        content="这条消息是user002的agent think，user002自己的事件",
    )
    evt_mgr.Publish(msg_wrong_user)

    # 等待足够时间让全部并行任务跑完
    time.sleep(6)
    cost_total = time.time() - start_total
    LogInfo(f"测试总耗时={cost_total:.2f}s，如果是并行，总耗时约3s左右；串行会接近45s")

    evt_mgr.Stop()
    print("===== Test Complete, event manager stopped =====")