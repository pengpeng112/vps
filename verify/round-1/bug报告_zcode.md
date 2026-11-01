# bug 报告（ZCode）：管理台登录凭据无法被浏览器保存 — round-1

## 现象

用户日常经本机 SSH -L 隧道访问 `https://127.0.0.1:8444/devices`（DMIT 管理台），浏览器（Chrome/Edge）从不弹出"保存密码"提示，每次登录需手输用户名+密码。用户明确反馈"很麻烦需要处理下"。

## 复现

每次登录必现（用户日常使用反馈；2026-10-02 曾按"DOM 层 autocomplete + form submit"修复过一次，未生效）。

## 证据（已执行命令+原始输出摘要，2026-10-10）

1. **DOM 层已具备密码保存要素**（`mgmt/frontend/src/views/Login.vue:61-70`）：
   - `<n-form @submit.prevent="login">`（naive-ui 的 n-form 渲染为 `<form>`）
   - 用户名输入框 `autocomplete:'username', name:'username'`
   - 密码输入框 `type="password" autocomplete:'current-password', name:'password'`
   - 按钮 `attr-type="submit"`（渲染 `<button type="submit">`）
   - 结论：10-02 的 DOM 修复是对的，但它不是瓶颈。

2. **TLS 证书实测**（本机 `openssl s_client -connect 127.0.0.1:8444` 与 DMIT 侧 `-connect 127.0.0.1:8443` 输出一致）：
   ```
   subject=CN = 10.10.0.1
   X509v3 Subject Alternative Name:
       IP Address:10.10.0.1
   notBefore=Sep 17 00:48:40 2026 GMT
   notAfter=Dec 20 00:48:40 2028 GMT
   ```
   SAN 不含 `127.0.0.1`，也不含 `localhost`。

3. **因果链**：访问主机名 `127.0.0.1` ∉ SAN → 浏览器判定证书不匹配 → 用户每次手动"高级→继续访问"绕过 → **Chromium/Edge 安全策略：在用户绕过证书错误的页面上禁用密码保存气泡**（防止中间人窃取凭据的既定行为）。即密码保存被禁用的直接原因是"证书错误被手动绕过"，与 DOM 层写法无关。

## 根因

管理台自签证书的 SAN 只含 `IP:10.10.0.1`（WG 地址），而日常访问入口是 `127.0.0.1:8444`。主机名不在 SAN 内 → 永久证书错误 → 密码管理器被禁用。

## 影响面

- 所有经 `SSH -L 8444` 的本机浏览器访问（用户主用入口，`VPS远程工具包\管理台通道-DMIT.bat`）。
- 不影响 `10.10.0.1:8443` 的 WG 访问（该主机名匹配 SAN，但同样需要信任自签 CA 才无警告）。
- 不影响 API/agent 通道（curl -k / 程序化访问不做证书校验阻断）。

## 最小修复思路（供拍板）

1. 在 DMIT 上重签自签证书：`CN=10.10.0.1`，`SAN=IP:127.0.0.1, IP:10.10.0.1, DNS:localhost`（可顺手把有效期延到 2028+，沿用当前长度即可）。
2. 替换 api 容器挂载的证书文件（deploy 配置中的 cert 路径，执行时先确认 compose 挂载点并备份旧证书），`docker compose restart` 对应服务（管理台闪断约 30-60 秒，四台设备心跳不受影响——agent 会自动重试）。
3. 把新证书（.crt/.pem 公开部分）拷给用户，Windows 双击导入"受信任的根证书颁发机构"（一次性操作）。
4. 验收：Chrome 访问 `https://127.0.0.1:8444` 地址栏显示锁形（非"不安全"）；登录后弹出"保存密码"提示；保存后再次访问自动填充。

## 备选方案

- 改用 `https://10.10.0.1:8443` 访问：主机名匹配现有 SAN，但仍需导入信任 CA 才无警告，且本机需保持 WG 连接 —— 不如重签证书一次到位。
- mkcert 等本地 CA 方案：引入新工具链，对单用户自用管理台过重。

## 验证状态

- DOM 层证据：已验证（源码 Login.vue:61-70）。
- 证书证据：已验证（openssl 实测，双端一致）。
- "Chromium 对绕过证书错误页面禁用密码保存"：浏览器既定安全行为（公开文档行为），未在本机做浏览器自动化二次实证——修复后验收（绿锁+弹保存）即为终验。
