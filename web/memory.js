// 记忆管理页：浏览、搜索、编辑、删除记忆，管理核心档案
(() => {
  const TABS = [
    ["profile", "核心档案"],
    ["commitment", "承诺"],
    ["pattern", "模式"],
    ["person", "人物"],
    ["fact", "事实"],
    ["episode", "事件"],
  ];
  const TYPE_ZH = { fact: "事实", person: "人物", commitment: "承诺", pattern: "模式", episode: "事件" };
  const STATUS_ZH = { open: "进行中", done: "完成", dropped: "放弃", overdue: "逾期" };
  const ACTOR_ZH = { writer: "她整理时", reflector: "她反思时", user: "你" };
  const OP_ZH = { add: "新增", update: "修改", delete: "删除" };

  const tabsEl = document.getElementById("tabs");
  const listEl = document.getElementById("list");
  const logEl = document.getElementById("log");
  const searchEl = document.getElementById("search");
  const toastEl = document.getElementById("toast");

  let listSeq = 0;  // 只渲染最后一次请求的结果，避免快速切换标签时旧响应覆盖新内容
  let tab = TABS.some(([k]) => k === location.hash.slice(1)) ? location.hash.slice(1) : "commitment";

  // ---------- 工具 ----------

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(text, onclick, cls = "") {
    const b = el("button", cls, text);
    b.type = "button";
    b.onclick = onclick;
    return b;
  }

  function fmt(iso) {
    const d = new Date(iso);
    return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  let toastTimer = null;
  function toast(text) {
    toastEl.textContent = text;
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 2200);
  }

  async function api(path, options = {}) {
    let res;
    try {
      res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
        body: options.body ? JSON.stringify(options.body) : undefined,
      });
    } catch (e) {
      toast("连不上 Mira，确认她正在运行");
      throw e;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const msg = typeof data.detail === "string" ? data.detail : "操作失败";
      toast(msg);
      throw new Error(msg);
    }
    return data;
  }

  // ---------- 最近变动 ----------

  async function loadLog() {
    const entries = await api("/api/memory-log?limit=20");
    logEl.replaceChildren();
    if (!entries.length) {
      logEl.appendChild(el("li", "", "还没有记忆。多聊几句，她停下来整理时就会记住。"));
      return;
    }
    for (const e of entries) {
      const m = e.after || e.before || {};
      const li = el("li");
      li.append(`${fmt(e.created_at)} ${ACTOR_ZH[e.actor] || e.actor}${OP_ZH[e.op] || e.op}了${TYPE_ZH[m.type] || "记忆"}：`);
      li.appendChild(el("b", "", m.content || ""));
      logEl.appendChild(li);
    }
  }

  // ---------- 标签页 ----------

  function renderTabs() {
    tabsEl.replaceChildren(
      ...TABS.map(([key, label]) =>
        button(label, () => {
          tab = key;
          location.hash = key;
          searchEl.value = "";
          renderTabs();
          loadList();
        }, key === tab ? "active" : ""),
      ),
    );
    searchEl.style.display = tab === "profile" ? "none" : "";
  }

  async function loadList() {
    if (tab === "profile") return renderProfile();
    const seq = ++listSeq;
    const q = searchEl.value.trim();
    const params = new URLSearchParams({ type: tab });
    if (q) params.set("q", q);
    const items = await api(`/api/memories?${params}`);
    if (seq !== listSeq) return;
    listEl.replaceChildren();
    if (!items.length) {
      listEl.appendChild(el("div", "empty", q ? "没有搜到相关的记忆" : "这里还是空的"));
      return;
    }
    for (const m of items) listEl.appendChild(card(m));
  }

  async function refresh() {
    await Promise.all([loadList(), loadLog()]);
  }

  // ---------- 记忆卡片 ----------

  function card(m) {
    const c = el("div", `card${m.superseded_by ? " superseded" : ""}`);
    const content = el("div", "content", m.content);
    c.appendChild(content);

    const meta = el("div", "meta");
    if (m.type === "person" && m.subject) meta.appendChild(el("span", "badge", m.subject));
    if (m.type === "commitment") {
      const sel = el("select");
      for (const [k, v] of Object.entries(STATUS_ZH)) {
        const o = el("option", "", v);
        o.value = k;
        o.selected = m.status === k;
        sel.appendChild(o);
      }
      sel.onchange = async () => {
        sel.disabled = true;
        try {
          await api(`/api/memories/${m.id}`, { method: "PATCH", body: { status: sel.value } });
          toast("状态已更新");
          refresh();
        } catch {
          sel.value = m.status;  // 没保存成功就恢复原样（api 已经提示了错误）
        } finally {
          sel.disabled = false;
        }
      };
      meta.appendChild(sel);
      if (m.due_at) meta.appendChild(el("span", "", `截止 ${m.due_at}`));
    }
    if (m.type !== "episode") meta.appendChild(el("span", "", `重要度 ${m.importance}`));
    meta.appendChild(el("span", "", fmt(m.updated_at)));
    if (m.user_locked) meta.appendChild(el("span", "", "🔒 你改过"));
    if (m.superseded_by) meta.appendChild(el("span", "badge", "已过时"));
    if (m.score !== undefined) meta.appendChild(el("span", "", `相关度 ${m.score}`));
    c.appendChild(meta);

    const detail = el("div", "detail");
    detail.hidden = true;
    const actions = el("div", "actions");

    async function toggleDetail(kind) {
      if (!detail.hidden && detail.dataset.kind === kind) {
        detail.hidden = true;
        return;
      }
      const d = await api(`/api/memories/${m.id}`);
      detail.replaceChildren();
      detail.dataset.kind = kind;
      if (kind === "source") {
        if (!d.source_messages.length) detail.appendChild(el("p", "who", "没有找到原始对话"));
        for (const s of d.source_messages) {
          const p = el("p");
          p.appendChild(el("span", "who", `${s.role === "user" ? "你" : "Mira"}：`));
          p.append(s.content);
          detail.appendChild(p);
        }
      } else {
        if (!d.evidence_items.length) detail.appendChild(el("p", "who", "证据都已被删除"));
        for (const e of d.evidence_items) {
          const p = el("p");
          p.appendChild(el("span", "who", `${fmt(e.created_at)}　`));
          p.append(e.content);
          detail.appendChild(p);
        }
      }
      detail.hidden = false;
    }

    if (m.type === "pattern") {
      actions.appendChild(button(`证据（${m.evidence.length}）`, () => toggleDetail("evidence")));
    } else {
      actions.appendChild(button("来源", () => toggleDetail("source")));
    }
    actions.appendChild(
      button("编辑", () => {
        const ta = el("textarea", "edit");
        ta.value = m.content;
        content.replaceWith(ta);
        ta.focus();
        actions.replaceChildren(
          button("保存", async () => {
            await api(`/api/memories/${m.id}`, { method: "PATCH", body: { content: ta.value } });
            toast("已保存，这条记忆以后不会被她自动改动");
            refresh();
          }, "primary"),
          button("取消", () => loadList()),
        );
      }),
    );
    actions.appendChild(
      button("删除", async () => {
        if (!confirm(`确定删除这条记忆吗？\n\n${m.content}`)) return;
        await api(`/api/memories/${m.id}`, { method: "DELETE" });
        toast("已删除");
        refresh();
      }, "danger"),
    );
    c.appendChild(actions);
    c.appendChild(detail);
    return c;
  }

  // ---------- 核心档案 ----------

  async function renderProfile() {
    const seq = ++listSeq;
    const { current, history } = await api("/api/profile");
    if (seq !== listSeq) return;
    listEl.replaceChildren();

    const editor = el("div", "card");
    const ta = el("textarea", "edit profile");
    ta.value = current ? current.content : "";
    ta.placeholder = "还没有核心档案。她每天反思时会写一份，你也可以先自己写。";
    editor.appendChild(ta);
    const meta = el("div", "meta");
    if (current) meta.append(`当前版本：${fmt(current.created_at)}，${current.source === "user" ? "你写的" : "她写的"}`);
    editor.appendChild(meta);
    const actions = el("div", "actions");
    actions.appendChild(
      button("保存", async () => {
        await api("/api/profile", { method: "PUT", body: { content: ta.value } });
        toast("已保存为新版本");
        renderProfile();
      }, "primary"),
    );
    editor.appendChild(actions);
    listEl.appendChild(editor);

    if (history.length > 1) {
      listEl.appendChild(el("h2", "", "历史版本"));
      for (const p of history.slice(1)) {
        const c = el("div", "card");
        const head = el("div", "meta");
        head.append(`${fmt(p.created_at)}，${p.source === "user" ? "你写的" : "她写的"}`);
        c.appendChild(head);
        const body = el("div", "detail");
        body.hidden = true;
        body.appendChild(el("p", "", p.content));
        const acts = el("div", "actions");
        acts.appendChild(button("查看", () => (body.hidden = !body.hidden)));
        acts.appendChild(
          button("回滚到此版本", async () => {
            if (!confirm("用这个版本替换当前的核心档案？（当前版本会保留在历史里）")) return;
            await api(`/api/profile/rollback/${p.id}`, { method: "POST" });
            toast("已回滚");
            renderProfile();
          }),
        );
        c.append(acts, body);
        listEl.appendChild(c);
      }
    }
  }

  // ---------- 启动 ----------

  searchEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.isComposing && e.keyCode !== 229) loadList();
  });
  searchEl.addEventListener("search", () => {
    if (!searchEl.value) loadList();
  });

  renderTabs();
  refresh().catch(() => toast("加载失败，确认 Mira 正在运行"));
})();
