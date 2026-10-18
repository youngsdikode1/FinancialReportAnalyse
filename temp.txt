marked.setOptions({
    gfm: true,
    breaks: true,
    highlight: function (code, lang) {
        if (lang && hljs.getLanguage(lang)) {
            return hljs.highlight(code, { language: lang }).value;
        }
        return code;
    }
});

function renderBotMarkdown(domEl, rawMdText) {
    domEl.innerHTML = marked.parse(rawMdText);
    domEl.dataset.rawmd = rawMdText; // 挂载原始markdown

    // 插入复制按钮
    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.title = "复制Markdown原文";
    // 标准双矩形复制图标
    copyBtn.innerHTML = `
<svg viewBox="0 0 24 24" width="20" height="20" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="9" y="9" width="11" height="11" rx="2" stroke="#6b7280" stroke-width="1.6" />
  <path d="M5 15V6a2 2 0 0 1 2-2h9" stroke="#6b7280" stroke-width="1.6" stroke-linecap="round" />
</svg>
    `;
    copyBtn.onclick = async (e) => {
        e.stopPropagation();
        const text = domEl.dataset.rawmd;
        try {
            // 优先现代剪贴板API
            await navigator.clipboard.writeText(text);
            copyBtn.classList.add("copied");
            setTimeout(() => {
                copyBtn.classList.remove("copied");
            }, 1200);
        } catch (err) {
            // 降级方案：创建临时textarea复制
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'absolute';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            copyBtn.classList.add("copied");
            setTimeout(() => {
                copyBtn.classList.remove("copied");
            }, 1200);
        }
    }
    domEl.appendChild(copyBtn);
    domEl.querySelectorAll("table").forEach(tbl => {
        if (!tbl.parentElement.classList.contains("table-wrap")) {
            const wrap = document.createElement("div");
            wrap.className = "table-wrap";
            tbl.parentNode.insertBefore(wrap, tbl);
            wrap.appendChild(tbl);
        }
    });
}

let ws = null;
let sessionId = null;
let currentBotDiv = null;
let currentBotRawText = "";

// 系统状态：idle / running
let sysState = "idle";
// 停止请求 120 秒兜底定时器
let cancelFallbackTimer = null;
// 重连状态：false=正常，true=正在重连中（用于提示只打一次）
let isReconnecting = false;

const chatArea = document.getElementById("chatArea");
const logPanel = document.getElementById("logPanel");
const chatInput = document.getElementById("chatInput");
const inputWrap = document.getElementById("inputWrap");
const tabBtns = document.querySelectorAll(".tab-btn");
const panels = document.querySelectorAll(".tab-panel");
const showLocalSid = document.getElementById("showLocalSid");

inputWrap.onclick = function(e){
    if(e.target.id !== "btnSend"){
        chatInput.focus();
    }
};

tabBtns.forEach(btn=>{
    // 只处理带 data-tab 的按钮，避免上传按钮 #btnUpload 被误绑
    if(!btn.dataset.tab){
        return;
    }
    btn.onclick = ()=>{
        const targetTab = btn.dataset.tab;
        tabBtns.forEach(b=>b.classList.remove("active"));
        panels.forEach(p=>p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById(targetTab).classList.add("active");
        if(targetTab === "tab-debug"){
            showLocalSid.innerText = localStorage.getItem("agent_session_id") || "无(未存储)";
        }
    }
});

function renderChatAll(list){
    if(!list || list.length === 0){
        return;
    }
    currentBotDiv = null;
    currentBotRawText = "";
    chatArea.innerHTML = "";
    list.forEach(item=>{
        const div = document.createElement("div");
        if(item.m_type === "TYPE_CHAT_USER"){
            div.className = "msg-user";
            div.innerText = item.m_content;
        }else if(item.m_type === "TYPE_CHAT_BOT"){
            div.className = "msg-bot";
            renderBotMarkdown(div, item.m_content);
        }
        chatArea.appendChild(div);
    })
    chatArea.scrollTop = chatArea.scrollHeight;
}

function renderMonitorAll(list){
    if(!list || list.length === 0){
        return;
    }
    logPanel.innerText = "";
    list.forEach(item=>{
        logPanel.innerText += item.m_content + "\n";
    })
    logPanel.scrollTop = logPanel.scrollHeight;
}

// ====================== 系统状态处理 ======================
function clearCancelFallback() {
    if (cancelFallbackTimer) {
        clearTimeout(cancelFallbackTimer);
        cancelFallbackTimer = null;
    }
}

function startCancelFallback() {
    clearCancelFallback();
    cancelFallbackTimer = setTimeout(() => {
        cancelFallbackTimer = null;
        logPanel.innerText += "⏱️停止请求超时120秒，已自动恢复输入框；任务可能仍在后台执行\n";
        logPanel.scrollTop = logPanel.scrollHeight;
        applySysState({"state": "idle", "input_hint": ""});
    }, 120000);
}

function applySysState(data) {
    sysState = data.state || "idle";
    const hint = data.input_hint || "";
    const isRunning = (sysState === "running");

    // 切回 idle 时清掉停止兜底定时器
    if (!isRunning) {
        clearCancelFallback();
    }

    // 输入框：锁输入 + 改提示
    chatInput.disabled = isRunning;
    chatInput.placeholder = isRunning
        ? (hint || "任务正在进行中，请稍候...")
        : "输入你的问题";

    // 发送按钮：运行中变成停止按钮
    const btnSend = document.getElementById("btnSend");
    if (isRunning) {
        btnSend.dataset.mode = "stop";
        btnSend.innerText = "■";
        btnSend.title = "停止任务";
        btnSend.disabled = false;
    } else {
        btnSend.dataset.mode = "send";
        btnSend.innerText = "↑";
        btnSend.title = "发送";
        btnSend.disabled = false;
    }
}

// ====================== WebSocket ======================
function connectWs(){
    const savedSid = localStorage.getItem("agent_session_id");
    let wsUrl;
    const proto = location.protocol==='https:'?'wss:':'ws:';
    if(savedSid){
        wsUrl = `${proto}//${location.host}/ws?sid=${savedSid}`;
    }else{
        wsUrl = `${proto}//${location.host}/ws`;
    }
    ws = new WebSocket(wsUrl);
    ws.onopen = ()=> {
        if(isReconnecting){
            logPanel.innerText += "✅重连成功\n";
            isReconnecting = false;
        }else{
            logPanel.innerText += "✅WebSocket已连接\n";
        }
        logPanel.scrollTop = logPanel.scrollHeight;
    };
    ws.onmessage = (ev)=>{
        const data = JSON.parse(ev.data);
        console.log("ws收到数据包：", data);
        if(data.type === "init"){
            sessionId = data.session_id;
            localStorage.setItem("agent_session_id", sessionId);
            logPanel.innerText += "🆔会话ID："+sessionId.substring(0,12)+"...\n";
            // 登录时主动请求一次系统状态
            ws.send(JSON.stringify({"type":"get_sys_state"}));
            ws.send(JSON.stringify({"type":"load_history"}));
        }
        else if(data.type === "sys_state"){
            applySysState(data);
        }
        else if(data.type === "chat_chunk"){
            if(currentBotDiv === null){
                currentBotDiv = document.createElement("div");
                currentBotDiv.className = "msg-bot";
                chatArea.appendChild(currentBotDiv);
                currentBotRawText = "";
            }
            currentBotRawText += data.text;
            currentBotDiv.innerText = currentBotRawText;
            chatArea.scrollTop = chatArea.scrollHeight;
        }
        else if(data.type === "chat_end"){
            if(currentBotDiv){
                renderBotMarkdown(currentBotDiv, currentBotRawText);
            }
            currentBotDiv = null;
            currentBotRawText = "";
        }
        else if(data.msg_type === "append_msg"){
            const item = data.item;
            if(item.m_type === "TYPE_CHAT_MONITOR"){
                logPanel.innerText += item.m_content + "\n";
                logPanel.scrollTop = logPanel.scrollHeight;
                return;
            }
            const div = document.createElement("div");
            if(item.m_type === "TYPE_CHAT_USER"){
                div.className = "msg-user";
                div.innerText = item.m_content;
            }else if(item.m_type === "TYPE_CHAT_BOT"){
                div.className = "msg-bot";
                renderBotMarkdown(div, item.m_content);
                // 兜底：收到 bot 回复，说明任务已出结果，切回 idle
                if(sysState === "running"){
                    clearCancelFallback();
                    applySysState({"state":"idle", "input_hint":""});
                }
            }
            chatArea.appendChild(div);
            chatArea.scrollTop = chatArea.scrollHeight;
        }
        else if(data.msg_type === "full_chat_history"){
            renderChatAll(data.data);
        }
        else if(data.msg_type === "full_monitor_history"){
            renderMonitorAll(data.data);
        }
        else if(data.type === "chat_cleared"){
            chatArea.innerHTML = "";
            currentBotDiv = null;
            currentBotRawText = "";
            logPanel.innerText = "";
            logPanel.innerText += "🗑️已清空本会话聊天历史（后端已持久化清空，sessionId保留）\n";
            logPanel.scrollTop = logPanel.scrollHeight;
        }
    };
    ws.onclose = ()=>{
        if(!isReconnecting){
            isReconnecting = true;
            logPanel.innerText += "❌连接断开，3秒后尝试重连\n";
            logPanel.scrollTop = logPanel.scrollHeight;
        }
        setTimeout(connectWs, 3000);
    };
}
connectWs();

// ====================== 发送 / 停止按钮 ======================
document.getElementById("btnSend").onclick = ()=>{
    const btnSend = document.getElementById("btnSend");

    // 运行中：按钮是停止
    if(btnSend.dataset.mode === "stop"){
        if(!ws) return;
        ws.send(JSON.stringify({"type":"cancel_task"}));
        btnSend.disabled = true;
        btnSend.innerText = "…";
        btnSend.title = "正在停止...";
        return;
    }

    // 空闲：正常发送，不自行改状态
    const val = chatInput.value.trim();
    if(!val || !ws) return;
    const div = document.createElement("div");
    div.className = "msg-user";
    div.innerText = val;
    chatArea.appendChild(div);
    chatArea.scrollTop = chatArea.scrollHeight;
    ws.send(JSON.stringify({"type":"query", "query":val}));
    chatInput.value = "";
};

chatInput.onkeydown = function(e){
    if(e.key === "Enter" && !e.shiftKey){
        e.preventDefault();
        document.getElementById("btnSend").click();
    }
}

document.getElementById("btnUpload").onclick = async ()=>{
    const file = document.getElementById("fileInput").files[0];
    if(!file || !sessionId){
        alert("等待会话初始化完成，选择PDF文件");
        return;
    }
    const fd = new FormData();
    fd.append("session_id", sessionId);
    fd.append("file", file);
    const resp = await fetch("/upload",{method:"POST", body:fd});
    const res = await resp.json();
    logPanel.innerText += `📩${res.msg}\n`;
    logPanel.scrollTop = logPanel.scrollHeight;
};

document.getElementById("btnClearSession").onclick = function(){
    localStorage.removeItem("agent_session_id");
    if(ws){
        ws.close();
    }
    location.reload();
};

document.getElementById("btnClearChat").onclick = function () {
    const ok = window.confirm("确认要清空全部聊天记录吗？该操作不可撤销。");
    if (!ok) {
        return;
    }
    if (!ws) return;
    ws.send(JSON.stringify({
        "type": "clear_chat_history"
    }));
};