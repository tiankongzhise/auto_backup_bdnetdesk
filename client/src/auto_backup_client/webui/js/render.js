export function qs(selector, root = document) {
  return root.querySelector(selector);
}

export function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

export function text(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

export function el(tag, options = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === "class") {
      node.className = value;
    } else if (key === "text") {
      node.textContent = text(value);
    } else if (key === "html") {
      node.innerHTML = value;
    } else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2), value);
    } else if (value !== false && value !== null && value !== undefined) {
      node.setAttribute(key, value === true ? "" : value);
    }
  }
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined) {
      continue;
    }
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function button(label, options = {}) {
  return el(
    "button",
    {
      class: `btn ${options.variant || ""}`.trim(),
      type: "button",
      disabled: options.disabled || null,
      title: options.title || null,
      onclick: options.onClick || null,
    },
    label,
  );
}

export function badge(label, tone = "") {
  return el("span", { class: `badge ${tone}`.trim(), text: label });
}

export function input(name, options = {}) {
  const attrs = {
    class: options.type === "textarea" ? "textarea" : options.select ? "select" : "input",
    name,
    id: options.id || name,
    value: options.value || "",
    placeholder: options.placeholder || "",
    type: options.type || "text",
  };
  if (options.type === "textarea") {
    return el("textarea", attrs);
  }
  if (options.select) {
    const node = el("select", attrs);
    for (const item of options.options || []) {
      node.appendChild(el("option", { value: item.value, text: item.label }));
    }
    node.value = options.value || "";
    return node;
  }
  return el("input", attrs);
}

export function field(label, control, options = {}) {
  return el("label", { class: `field ${options.inline ? "inline" : ""}`.trim() }, [
    el("span", { class: "field-label", text: label }),
    control,
  ]);
}

export function table(columns, rows, emptyText = "暂无数据") {
  if (!rows || rows.length === 0) {
    return el("div", { class: "empty", text: emptyText });
  }
  const head = el("thead", {}, [
    el(
      "tr",
      {},
      columns.map((column) => el("th", { text: column.label })),
    ),
  ]);
  const body = el(
    "tbody",
    {},
    rows.map((row) =>
      el(
        "tr",
        {},
        columns.map((column) => {
          const rendered = typeof column.render === "function" ? column.render(row) : text(row[column.key]);
          return el("td", {}, rendered);
        }),
      ),
    ),
  );
  return el("div", { class: "table-wrap" }, [el("table", {}, [head, body])]);
}

export function showToast(message) {
  const toast = qs("#toast");
  toast.textContent = message;
  toast.classList.add("is-visible");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("is-visible"), 3600);
}

export function operationView(operation) {
  if (!operation) {
    return el("div", { class: "empty", text: "暂无运行中的操作" });
  }
  const percent = Math.round((operation.progress || 0) * 100);
  return el("div", { class: "operation" }, [
    el("div", { class: "item-row" }, [
      el("strong", { text: operation.message || operation.kind }),
      badge(operation.status, operation.status === "failed" ? "red" : operation.status === "completed" ? "green" : "blue"),
    ]),
    el("div", { class: "progress" }, [el("span", { style: `width:${percent}%` })]),
    el("div", { class: "item-meta", text: `${operation.kind} / ${operation.stage} / ${percent}%` }),
  ]);
}

export function statusTone(status) {
  if (["completed", "consistent", "synced", "valid", "ready_local", "eligible"].includes(status)) {
    return "green";
  }
  if (["failed", "failed_terminal", "remote_missing", "blocked"].includes(status)) {
    return "red";
  }
  if (["failed_retryable", "paused", "needs_download", "sync_pending"].includes(status)) {
    return "yellow";
  }
  return "blue";
}
