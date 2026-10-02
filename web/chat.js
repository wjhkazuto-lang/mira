// 聊天页：WebSocket 收发 + 历史记录分页 + 断线重连
(() => {
  const log = document.getElementById("log");
  const input = document.getElementById("input");
  const form = document.getElementById("composer");
  const sendBtn = document.getElementById("send");
  const banner = document.getElementById("banner");

  const RECONNECT_DELAYS = [1000, 2000, 5000];
  const TYPING_THROTTLE = 2000;
  const STALE_UNANSWERED_MS = 2 * 60 * 1000;

  let messages = [];        // {id, role, content, created_at}，按时间升序
  let typing = false;       // Mira 正在输入
  let notice = null;        // {text, retry: bool}
  let ws = null;
  let reconnectAttempt = 0;
  let everConnected = false;
  let lastTypingSent = 0;
  let loadingOlder = false;
  let noMoreHistory = false;

  // ---------- 渲染 ----------

  function dayLabel(iso) {
    const d = new Date(iso);
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86400000);
    const same = (a, b) => a.toDateString() === b.toDateString();
    if (same(d, today)) return "今天";
    if (same(d, yesterday)) return "昨天";
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render() {
    const frag = document.createDocumentFragment();
    if (!messages.length && !typing && !notice) {
      frag.appendChild(el("div", "empty-hint", "跟 Mira 打个招呼吧"));
    }
    let lastDay = null;
    let lastRole = null;
    for (const m of messages) {
      const day = dayLabel(m.created_at);
      if (day !== lastDay) {
        frag.appendChild(el("div", "date-sep", day));
        lastDay = day;
        lastRole = null;
      }
      const b = el("div", `bubble ${m.role === "user" ? "me" : "mira"}`, m.content);
      if (lastRole && lastRole !== m.role) b.classList.add("turn");
      b.title = new Date(m.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
      frag.appendChild(b);
      lastRole = m.role;
    }
    if (notice) {
      const n = el("div", "note");
      n.appendChild(el("span", "", notice.text));
      if (notice.retry) {
        const btn = el("button", "", "重试");
        btn.type = "button";
        btn.onclick = () => {
          notice = null;
          send({ type: "retry" });
          render();
        };
        n.appendChild(btn);
      }
      frag.appendChild(n);
    }
    if (typing) {
      const t = el("div", "bubble mira typing");
      t.append(el("span"), el("span"), el("span"));
      frag.appendChild(t);
    }
    log.replaceChildren(frag);
  }

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 120;
  }

  function renderAndFollow(force = false) {
    const follow = force || nearBottom();
    render();
    if (follow) log.scrollTop = log.scrollHeight;
  }

  // ---------- 历史记录 ----------

  async function fetchMessages(before) {
    const url = before ? `/api/messages?limit=50&before=${before}` : "/api/messages?limit=50";
    const res = await fetch(url);
    if (!res.ok) throw new Error(res.statusText);
    return res.json();
  }

  async function loadInitial() {
    messages = await fetchMessages();
    noMoreHistory = messages.length < 50;
    const last = messages[messages.length - 1];
    if (last && last.role === "user" && Date.now() - new Date(last.created_at) > STALE_UNANSWERED_MS) {
      notice = { text: "Mira 还没回复上一条", retry: true };
    }
    renderAndFollow(true);
  }

  async function loadOlder() {
    if (loadingOlder || noMoreHistory || !messages.length || messages[0].id == null) return;
    loadingOlder = true;
    try {
      const older = await fetchMessages(messages[0].id);
      if (older.length < 50) noMoreHistory = true;
      if (older.length) {
        const prevHeight = log.scrollHeight;
        messages = older.concat(messages);
        render();
        log.scrollTop += log.scrollHeight - prevHeight;  // 保持当前阅读位置
      }
    } finally {
      loadingOlder = false;
    }
  }

  log.addEventListener("scroll", () => {
    if (log.scrollTop < 60) loadOlder();
  });

  // ---------- WebSocket ----------

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
      return true;
    }
    return false;
  }

  function setConnected(ok) {
    banner.classList.toggle("show", !ok);
    sendBtn.disabled = !ok;
  }

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);
    ws.onopen = () => {
      reconnectAttempt = 0;
      setConnected(true);
      // 断线期间她可能已经回复了（回复照样会存下来），重连后补上
      if (everConnected) loadInitial().catch(() => {});
      everConnected = true;
    };
    ws.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      if (ev.type === "typing") {
        typing = true;
      } else if (ev.type === "bubble") {
        typing = false;
        messages.push({ id: ev.id, role: "assistant", content: ev.text, created_at: ev.created_at });
      } else if (ev.type === "error") {
        typing = false;
        notice = { text: ev.message, retry: true };
      }
      renderAndFollow();
    };
    ws.onclose = () => {
      setConnected(false);
      typing = false;
      render();
      const delay = RECONNECT_DELAYS[Math.min(reconnectAttempt, RECONNECT_DELAYS.length - 1)];
      reconnectAttempt += 1;
      setTimeout(connect, delay);
    };
  }

  // ---------- 输入 ----------

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }

  function submit() {
    const text = input.value.trim();
    if (!text) return;
    if (!send({ type: "message", text })) return;  // 没连上时保留输入内容
    messages.push({ id: null, role: "user", content: text, created_at: new Date().toISOString() });
    notice = null;
    input.value = "";
    autosize();
    renderAndFollow(true);
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
    input.focus();
  });

  input.addEventListener("keydown", (e) => {
    // 输入法正在组字时按 Enter 是选词，不能发送
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && e.keyCode !== 229) {
      e.preventDefault();
      submit();
    }
  });

  input.addEventListener("input", () => {
    autosize();
    const now = Date.now();
    if (input.value.trim() && now - lastTypingSent > TYPING_THROTTLE) {
      if (send({ type: "typing" })) lastTypingSent = now;
    }
  });

  setConnected(false);
  loadInitial().catch(() => {
    notice = { text: "历史记录加载失败，刷新试试", retry: false };
    render();
  });
  connect();
})();
