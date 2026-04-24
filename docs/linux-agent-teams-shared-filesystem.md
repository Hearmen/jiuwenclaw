# Linux 跨节点 Agent Teams 共享文件系统说明

本文说明如何使用 `scripts/nfs/` 下的脚本，在多个 Linux 节点之间共享 `jiuwenclaw` 工作空间。

## 一、适用场景

适合下面这种结构：

- 一个中心节点作为 NFS server
- 一个或多个节点作为 NFS client
- 所有节点共享同一个 `jiuwenclaw_workspace`

默认共享目录为：

```text
/root/.jiuwenclaw/agent/jiuwenclaw_workspace
```

## 二、前提条件

需要满足：

- 所有机器都是 Linux
- 所有机器都已经初始化过 `jiuwenclaw`
- 服务端和客户端可以通过内网互通
- 云安全组或防火墙已经放行 NFS / RPC 相关端口

建议优先使用内网 IP，不建议优先用公网 IP。

## 三、脚本位置

- 服务端脚本：`scripts/nfs/setup_nfs_server.sh`
- 客户端脚本：`scripts/nfs/setup_nfs_client.sh`

## 四、服务端配置

在服务端执行：

```bash
cd /path/to/jiuwenclaw
sudo bash scripts/nfs/setup_nfs_server.sh --client-ip <客户端内网IP>
```



如果需要手动指定共享目录：

```bash
sudo bash scripts/nfs/setup_nfs_server.sh \
  --client-ip <客户端内网IP> \
  --export-dir /root/.jiuwenclaw/agent/jiuwenclaw_workspace \
  --mount-point /root/.jiuwenclaw/agent/jiuwenclaw_workspace
```

执行后，脚本会：

- 安装 NFS server 依赖
- 创建共享目录
- 写入导出规则
- 重新加载导出配置
- 启动 NFS 服务

## 五、客户端配置

在每个客户端执行：

```bash
cd /path/to/jiuwenclaw
sudo bash scripts/nfs/setup_nfs_client.sh --server-ip <服务端内网IP>
```



如果需要手动指定路径：

```bash
sudo bash scripts/nfs/setup_nfs_client.sh \
  --server-ip <服务端内网IP> \
  --export-dir /root/.jiuwenclaw/agent/jiuwenclaw_workspace \
  --mount-point /root/.jiuwenclaw/agent/jiuwenclaw_workspace
```

执行后，脚本会：

- 安装 NFS client 依赖
- 检查并备份已有本地目录
- 使用 `nfs4` 挂载服务端目录
- 把挂载信息写入 `/etc/fstab`

## 六、配置完成后的检查

### 1. 服务端检查

```bash
exportfs -v
showmount -e 127.0.0.1
```

### 2. 客户端检查

```bash
rpcinfo -p <服务端内网IP>
showmount -e <服务端内网IP>
mount | grep jiuwenclaw_workspace
df -h | grep jiuwenclaw_workspace
```

## 七、同步验证

先在服务端执行：

```bash
echo from-server > /root/.jiuwenclaw/agent/jiuwenclaw_workspace/nfs_sync_test.txt
```

再在客户端执行：

```bash
cat /root/.jiuwenclaw/agent/jiuwenclaw_workspace/nfs_sync_test.txt
echo from-client >> /root/.jiuwenclaw/agent/jiuwenclaw_workspace/nfs_sync_test.txt
```

最后回到服务端执行：

```bash
cat /root/.jiuwenclaw/agent/jiuwenclaw_workspace/nfs_sync_test.txt
```

如果两边看到的内容一致，就说明共享和同步成功。

## 八、多个客户端

如果后续要增加更多客户端：

1. 在服务端放行新的客户端内网 IP
2. 在新客户端上执行客户端脚本
3. 所有客户端都挂载到同一个工作空间路径

## 九、边界说明

- 这套方案共享的是文件系统
- 如果只有一个中心节点运行 `jiuwenclaw server`，方案会比较稳定
- 不建议多个节点同时编辑同一个文件
- 如果 `rpcinfo` 或 `showmount` 超时，优先检查内网、安全组和端口放行
