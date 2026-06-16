import { badge, button, el, field, input, table } from "../render.js";
import { state } from "../state.js";

export async function renderBaidu(root, context) {
  await context.refreshAppState();
  const app = (state.appState && state.appState.app) || {};
  const accountsData = await context
    .call("list_baidu_accounts")
    .catch((error) => ({ accounts: [], selected_account_id: "", error: error.message || "账号读取失败" }));
  root.appendChild(el("section", { class: "panel", style: "margin-bottom:16px" }, [el("h2", { text: "设备授权状态" }), credentialStatus(app, accountsData)]));
  const authBox = el("div", { class: "panel" }, [el("h2", { text: "授权流程" }), authFlow(context)]);
  const verifyBox = el("div", { class: "panel" }, [el("h2", { text: "验证授权密码" }), tokenVerification(accountsData.accounts || [], context)]);
  const tableBox = el("div", { class: "panel", style: "margin-top:16px" }, [el("h2", { text: "百度账号" }), accountTable(accountsData.accounts || [], context)]);
  root.appendChild(el("div", { class: "grid two" }, [authBox, verifyBox]));
  root.appendChild(tableBox);
}

function credentialStatus(app, accountsData) {
  const items = [
    el("div", { class: "item" }, [
      el("div", { class: "item-row" }, [
        el("strong", { text: "Device Token" }),
        badge(app.device_token_available ? "已加载" : "未加载", app.device_token_available ? "green" : "yellow"),
      ]),
      el("div", { class: "item-meta", text: `来源：${app.device_credential_source || "-"}` }),
      el("div", { class: "item-meta", text: `设备摘要：${app.device_id_hint || "-"}` }),
    ]),
  ];
  if (app.device_credential_error) {
    items.push(el("div", { class: "item" }, [el("div", { class: "item-title", text: "凭据加载错误" }), el("div", { class: "item-meta", text: app.device_credential_error })]));
  }
  if (accountsData.error) {
    items.push(el("div", { class: "item" }, [el("div", { class: "item-title", text: "账号读取错误" }), el("div", { class: "item-meta", text: accountsData.error })]));
  }
  return el("div", { class: "list" }, items);
}

function authFlow(context) {
  const stateBox = el("div", { class: "item" }, [el("div", { class: "item-meta", text: "尚未开始授权" })]);
  const password = input("authorization-password", { type: "password", placeholder: "授权密码，只用于本次 token 加密/解密" });

  function showAuthorization(auth) {
    stateBox.replaceChildren(
      el("div", { class: "item-row" }, [el("strong", { text: auth.status }), badge(auth.can_complete ? "可完成" : "等待中", auth.can_complete ? "green" : "yellow")]),
      field("授权链接", input("auth-url", { value: auth.user_action_url || auth.qrcode_url || "" })),
      field("用户码", input("user-code", { value: auth.user_code || "" })),
      el("div", { class: "item-meta", text: `过期时间 ${auth.expires_at}` }),
    );
  }

  return el("div", {}, [
    stateBox,
    field("授权密码", password),
    el("div", { class: "toolbar" }, [
      button("开始授权", {
        variant: "primary",
        onClick: async () => {
          const data = await context.call("start_baidu_authorization");
          showAuthorization(data.authorization);
        },
      }),
      button("刷新状态", {
        onClick: async () => {
          const data = await context.call("poll_baidu_authorization");
          showAuthorization(data.authorization);
        },
      }),
      button("完成授权", {
        onClick: async () => {
          await context.call("complete_baidu_authorization", password.value);
          password.value = "";
          context.showToast("百度授权已完成");
        },
      }),
    ]),
  ]);
}

function tokenVerification(accounts, context) {
  const selected = accounts.find((row) => row.selected) || accounts[0] || {};
  const accountSelect = input("verify-account", {
    select: true,
    value: selected.account_id || "",
    options: accounts.map((row) => ({
      value: row.account_id,
      label: `${row.display_name || row.baidu_uk || row.account_id} / ${row.device_hint || "-"}`,
    })),
  });
  const password = input("verify-authorization-password", { type: "password", placeholder: "输入授权密码验证本地解密" });
  const result = el("div", { class: "item-meta", text: accounts.length ? "尚未验证" : "暂无可验证账号" });
  const verifyButton = button("验证授权密码", {
    variant: "primary",
    disabled: accounts.length === 0,
    onClick: async () => {
      const accountId = accountSelect.value;
      if (!accountId) {
        context.showToast("请先选择百度账号");
        return;
      }
      const data = await context.call("verify_baidu_token", accountId, password.value);
      const verification = data.verification || {};
      result.textContent = verification.valid ? `验证通过，token version ${verification.token_version || "-"}` : verification.message || "验证失败";
      context.showToast(verification.valid ? "授权密码验证通过" : result.textContent);
      password.value = "";
    },
  });
  return el("div", {}, [
    field("账号", accountSelect),
    field("授权密码", password),
    el("div", { class: "toolbar" }, [verifyButton]),
    result,
  ]);
}

function accountTable(accounts, context) {
  return table(
    [
      { label: "账号", render: (row) => el("strong", { text: row.display_name || row.baidu_uk || row.account_id }) },
      { label: "设备", render: (row) => el("span", { class: "mono", text: row.device_hint || "-" }) },
      { label: "UID", render: (row) => el("span", { class: "mono", text: row.uid_hint || "-" }) },
      { label: "状态", render: (row) => badge(row.token_valid ? "有效" : "失效", row.token_valid ? "green" : "red") },
      { label: "本机解密", render: (row) => badge(row.local_kdf_available ? "已保存" : "缺失", row.local_kdf_available ? "green" : "yellow") },
      { label: "选中", render: (row) => (row.selected ? badge("当前", "blue") : "-") },
      { label: "到期", key: "token_expires_at" },
      {
        label: "操作",
        render: (row) =>
          el("div", { class: "toolbar" }, [
            button("设为当前", {
              disabled: row.selected,
              onClick: async () => {
                await context.call("select_baidu_account", row.account_id);
                context.showToast("已选择百度账号");
              },
            }),
            button("验证", {
              onClick: async () => {
                const passwordInput = document.querySelector("#verify-authorization-password");
                const accountSelect = document.querySelector("#verify-account");
                if (accountSelect) {
                  accountSelect.value = row.account_id;
                }
                if (passwordInput) {
                  passwordInput.focus();
                }
                context.showToast("请在验证授权密码区域输入密码后验证");
              },
            }),
          ]),
      },
    ],
    accounts,
    "暂无百度账号",
  );
}
