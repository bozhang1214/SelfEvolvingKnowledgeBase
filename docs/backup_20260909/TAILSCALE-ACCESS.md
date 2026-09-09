# Tailscale 内网组网配置手册

> 目的：用 Tailscale 建立一条私有虚拟局域网，让 MacBook Pro / 手机安全访问云服务器上的 Grafana、Prometheus 监控页面，**无需开放公网端口、无需 SSH 隧道、无需每次挂终端**。
>
> 适用设备：腾讯云轻量应用服务器（Linux）、macOS（MacBook Pro）、手机（iOS / Android）
>
> 关联文档：[PRODUCTION-DEPLOY.md](./PRODUCTION-DEPLOY.md)、[TENCENT-CLOUD-DEPLOY.md](./TENCENT-CLOUD-DEPLOY.md)

---

## 0. 前置说明（务必先读）

1. **三台设备必须登录同一个 Tailscale 账号**（一个邮箱即可），它们会自动组成一张私有网络（Tailnet）。
2. 组网后每台设备会获得一个 `100.x.x.x` 的固定内网 IP（MagicDNS 还提供 `<主机名>.<tailnet>.ts.net` 域名，但本手册统一用 IP，最简单）。
3. 访问监控页面的地址会变成：
   - Grafana：`http://<服务器100.x.x.x>:3001`
   - Prometheus：`http://<服务器100.x.x.x>:9091`
4. **国内网络提示**：Tailscale 的控制面（协调服务器）与 DERP 中继节点在境外，国内可能偶发慢或首连稍慢；只要能连上，日常看指标影响不大。若体验很差，见文末「常见问题」的备选方案。

---

## 1. 云服务器（腾讯云轻量）

### 1.1 安装 Tailscale

SSH 登录服务器后执行（支持 Ubuntu / Debian / CentOS）：

```bash
# 官方一键安装脚本
curl -fsSL https://tailscale.com/install.sh | sh
```

安装完成后可用 `tailscale version` 确认。

### 1.2 登录并加入你的私有网络

```bash
sudo tailscale up
```

执行后会打印一个形如 `https://login.tailscale.com/a/xxxxxx` 的**授权链接**：

1. 在浏览器（任意设备）打开该链接；
2. 用你的 Tailscale 账号登录（首次会提示注册，可用 Google/GitHub/微软账号或邮箱）；
3. 点击授权后，回到服务器终端，稍等片刻即显示 `Success`。

> 若服务器没有图形浏览器，直接把链接复制到你自己电脑的浏览器打开即可，两者无需在同一网络。

### 1.3 查看服务器的内网 IP

```bash
tailscale status          # 查看所有已登录设备
tailscale ip -4           # 只看本机（服务器）自己的 100.x IP
```

记下服务器这个 `100.x.x.x` 地址，后面访问监控页就用它。

### 1.4（强烈建议）防火墙放行 UDP 41641

Tailscale 用 WireGuard 协议做设备间**直连**，默认监听 **UDP 41641**。若被云防火墙拦截，流量会回落境外 DERP 中继，国内会明显变慢。

在腾讯云控制台放行：

1. 登录 [腾讯云控制台](https://console.cloud.tencent.com/) → **轻量应用服务器**；
2. 选中你的实例 → 顶部 **防火墙** 标签页；
3. 点击「添加规则」：
   - 协议：`UDP`
   - 端口：`41641`
   - 来源：`0.0.0.0/0`（Tailscale 流量本身已加密，放行所有来源是安全的）
4. 保存。

> 若你使用 `ufw` / `firewalld` 等系统防火墙，也需放行 UDP 41641：
> ```bash
> sudo ufw allow 41641/udp
> ```

---

## 2. MacBook Pro

### 2.1 安装

任选其一：

- **App Store（推荐）**：搜索「Tailscale」安装；
- 或 Homebrew：`brew install --cask tailscale`；
- 或官网下载 `.pkg`：https://tailscale.com/download

### 2.2 登录并连接

1. 打开 Tailscale 应用；
2. 用**与服务器相同的账号**登录；
3. 顶部菜单栏出现 Tailscale 图标，点击可看到「Connected」；
4. 确认本机也拿到了 `100.x.x.x` IP。

### 2.3 验证连通性

```bash
# 用服务器的 100.x IP 替换下面地址
ping 100.x.x.x
```

能 ping 通即组网成功。

---

## 3. 手机（iOS / Android）

### 3.1 iPhone（iOS）

1. App Store 搜索「Tailscale」安装；
2. 打开后允许添加 VPN 配置；
3. 用**同一账号**登录；
4. 主界面打开开关，状态变为 Connected。

> 国区 App Store 若搜不到，需使用海外 Apple ID 下载。

### 3.2 Android

1. 优先从 Google Play 搜索「Tailscale」安装；
2. 若无法访问 Google Play，从 Tailscale 官网下载 APK 侧载，或使用可信的国内应用市场；
3. 用**同一账号**登录，打开连接开关。

### 3.3 验证

手机浏览器直接访问：

```
http://100.x.x.x:3001    # Grafana
http://100.x.x.x:9091    # Prometheus
```

能打开登录页即成功。

---

## 4. 访问监控页面（三端通用）

用**服务器的** `100.x.x.x` IP，而不是公网 IP：

| 页面 | 地址 | 账号 |
|------|------|------|
| Grafana | `http://100.x.x.x:3001` | 默认 `admin` / `admin`（生产建议改） |
| Prometheus | `http://100.x.x.x:9091` | 无鉴权 |

> 提醒：只要看「SEKB 系统总览」看板，只需 Grafana 即可（它的数据源在容器内网已指向 Prometheus）。Prometheus 页面仅在手写 PromQL 时才需要。

---

## 5. 常见问题

### 5.1 服务器 `tailscale up` 一直连不上 / 打印不出授权链接

- 检查服务器能否访问 `https://login.tailscale.com`（控制面在境外，个别时段可能慢，多试几次）。
- 可先 `sudo tailscale up --reset` 重置后再试。

### 5.2 能连上但访问很慢

- 多半是没放行 **UDP 41641**，流量走了境外 DERP 中继，回到 1.4 放行即可；
- 在服务器上执行 `tailscale ping <另一设备IP>`，看走的是 `direct` 还是 `relay`：显示 `direct` 说明已直连。

### 5.3 国内直连仍不稳定，想换更快的方案

服务器本身有公网 IP，可改用 **WireGuard 直连**（服务器做 WG 服务端、Mac/手机做客户端），延迟更低、不依赖境外控制面；代价是要手动配置密钥和一条 UDP 端口。需要时再补充。

### 5.4 只想让某几台设备访问，不想全家桶进同一个网络

在 Tailscale 控制台（https://login.tailscale.com/admin/machines）可对设备做 ACL 权限控制，或直接断开不需要的设备即可。
